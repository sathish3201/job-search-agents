"""Scrapes the live portfolio SPA (sathish3201.github.io/portfolio) and
embeds the extracted profile/resume content into ChromaDB.

LOCAL-ONLY by design: Playwright + a real Chromium binary is far too heavy
for Render's 512MB free tier (the deployed API never imports this module).
Run it on your own machine when you want to refresh the embedded profile
data from the live site:

    python scripts/scrape_portfolio.py

Flow:
    1. Check the URL cache (SQLite, same cache.py used elsewhere) for a
       recent fetch of this URL — skip re-scraping if already cached.
    2. Playwright renders the page (it's a client-rendered React app, so
       plain requests/BeautifulSoup would see an empty <div id="root">).
    3. BeautifulSoup parses the rendered HTML into clean text sections.
    4. The extracted text + RESUME.md (structured fallback/supplement) are
       embedded into the "portfolio_profile" ChromaDB collection via the
       same fastembed model used by agents/vector_store.py.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup

from cache import SqliteCache, content_hash

PORTFOLIO_URL = "https://sathish3201.github.io/portfolio/"
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours — the portfolio doesn't change often
_COLLECTION_NAME = "portfolio_profile"
_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def _fetch_rendered_html(url: str) -> str:
    """Renders the page with a real browser so client-side React content
    actually appears in the DOM before we read it."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        # Give React a moment past "networkidle" for any client-side
        # rendering that doesn't itself trigger further network activity.
        page.wait_for_timeout(1000)
        html = page.content()
        browser.close()
        return html


def _get_cached_or_fetch(url: str, cache: SqliteCache) -> str:
    key = "scraped_url:" + content_hash(url)
    cached = cache.get(key)
    if cached is not None and (time.time() - cached["fetched_at"]) < CACHE_TTL_SECONDS:
        print(f"[scrape] cache hit for {url} (fetched {int(time.time() - cached['fetched_at'])}s ago)")
        return cached["html"]

    print(f"[scrape] fetching {url}...")
    html = _fetch_rendered_html(url)
    cache.set(key, {"html": html, "fetched_at": time.time(), "url": url})
    return html


def _extract_text_sections(html: str) -> list[tuple[str, str]]:
    """Returns (section_label, text) pairs — one per heading-delimited block,
    which gives ChromaDB more granular, individually-retrievable chunks than
    one giant blob of the whole page."""
    soup = BeautifulSoup(html, "html.parser")

    # Strip script/style — irrelevant to content, and script tags in a Vite
    # bundle are large enough to dominate token count if left in.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    sections: list[tuple[str, str]] = []
    current_label = "intro"
    current_text: list[str] = []

    def flush():
        text = " ".join(current_text).strip()
        text = " ".join(text.split())  # collapse whitespace
        if text:
            sections.append((current_label, text))

    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "span", "div"]):
        if el.name in ("h1", "h2", "h3"):
            flush()
            current_label = el.get_text(strip=True) or current_label
            current_text = []
        else:
            # Only take direct text, not text already captured by a nested
            # element we'll also visit — avoids massive duplication from
            # div-in-div-in-div React output.
            direct = el.find(string=True, recursive=False)
            if direct and direct.strip():
                current_text.append(direct.strip())

    flush()
    return [(label, text) for label, text in sections if len(text) > 20]


def main():
    cache = SqliteCache()
    html = _get_cached_or_fetch(PORTFOLIO_URL, cache)
    sections = _extract_text_sections(html)

    print(f"[scrape] extracted {len(sections)} content sections from the live portfolio")
    for label, text in sections[:5]:
        print(f"  - {label}: {text[:80]}...")

    if not sections:
        print("[scrape] WARNING: no content extracted — the page may not have "
              "finished rendering, or its structure changed. Nothing embedded.")
        return

    import chromadb
    from fastembed import TextEmbedding

    chroma_path = os.path.join(os.path.dirname(__file__), "..", "data", "chroma")
    client = chromadb.PersistentClient(path=chroma_path)
    collection = client.get_or_create_collection(_COLLECTION_NAME)
    embedder = TextEmbedding(model_name=_EMBED_MODEL)

    texts = [text for _, text in sections]
    ids = [f"portfolio:{content_hash(PORTFOLIO_URL, label, text)}" for label, text in sections]
    vectors = [v.tolist() for v in embedder.embed(texts)]

    collection.upsert(
        ids=ids,
        embeddings=vectors,
        documents=texts,
        metadatas=[{"source": "portfolio", "section": label, "url": PORTFOLIO_URL} for label, _ in sections],
    )

    print(f"[scrape] embedded {len(sections)} sections into ChromaDB collection '{_COLLECTION_NAME}'")

    # Also embed RESUME.md as a structured, reliable supplement — the live
    # site's text is looser/marketing-flavored, RESUME.md has clean facts
    # (dates, tech stacks, exact project names) that are worth having as
    # separately retrievable chunks.
    resume_path = os.path.join(os.path.dirname(__file__), "..", "RESUME.md")
    if os.path.exists(resume_path):
        with open(resume_path, encoding="utf-8") as f:
            resume_text = f.read()
        # Split on markdown ## headings for the same granular-chunk benefit.
        chunks = [c.strip() for c in resume_text.split("\n## ") if c.strip()]
        chunk_texts = [("## " + c if not c.startswith("#") else c) for c in chunks]
        chunk_vectors = [v.tolist() for v in embedder.embed(chunk_texts)]
        chunk_ids = [f"resume:{content_hash(c)}" for c in chunk_texts]
        collection.upsert(
            ids=chunk_ids,
            embeddings=chunk_vectors,
            documents=chunk_texts,
            metadatas=[{"source": "resume", "url": resume_path} for _ in chunk_texts],
        )
        print(f"[scrape] embedded {len(chunk_texts)} RESUME.md sections into the same collection")

    _sync_into_resume_md(sections, resume_path)


_SYNC_MARKER_START = "<!-- PORTFOLIO_SYNC_START (auto-generated by scripts/scrape_portfolio.py — do not hand-edit) -->"
_SYNC_MARKER_END = "<!-- PORTFOLIO_SYNC_END -->"


def _sync_into_resume_md(sections: list[tuple[str, str]], resume_path: str) -> None:
    """Writes/replaces a clearly-marked section in RESUME.md with the scraped
    portfolio content. This is the actual bridge to the deployed API: Render
    can't run Playwright/ChromaDB (too heavy for 512MB), but it already reads
    RESUME.md — so committing this file after a scrape is how portfolio
    content reaches the live ranking pipeline. The marker delimiters mean
    re-running the scraper replaces this block instead of duplicating it."""
    if not os.path.exists(resume_path):
        print(f"[scrape] {resume_path} not found — skipping RESUME.md sync")
        return

    with open(resume_path, encoding="utf-8") as f:
        current = f.read()

    block_lines = [_SYNC_MARKER_START, "", "## Portfolio Highlights (auto-synced)", ""]
    for label, text in sections:
        if label.lower() in ("intro",):
            continue  # usually just the nav/hero, low signal
        block_lines.append(f"**{label}:** {text}")
        block_lines.append("")
    block_lines.append(_SYNC_MARKER_END)
    new_block = "\n".join(block_lines)

    if _SYNC_MARKER_START in current and _SYNC_MARKER_END in current:
        before = current.split(_SYNC_MARKER_START)[0].rstrip()
        after = current.split(_SYNC_MARKER_END)[1].lstrip()
        updated = f"{before}\n\n{new_block}\n\n{after}" if after else f"{before}\n\n{new_block}\n"
    else:
        updated = f"{current.rstrip()}\n\n---\n\n{new_block}\n"

    with open(resume_path, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"[scrape] synced portfolio content into {resume_path} "
          "(review the diff and commit when ready)")


if __name__ == "__main__":
    main()
