"""Scrapes an arbitrary job-listing or single-posting URL, auto-detecting
which kind of page it is and extracting JobListing objects directly — no
intermediate ScrapedJob model, since this module's only consumer
(sources/scraper_service_source.py) normalizes into JobListing anyway.

Two-tier fetch strategy: try a fast, lightweight httpx GET + BeautifulSoup
parse first (works fine for server-rendered pages), and only fall back to
a full Playwright render when the static HTML comes back too thin to
plausibly contain real job data. Most job boards render listings
client-side via JS, so a plain GET alone returns near-empty HTML for
them — confirmed pattern from this project's history
(scripts/scrape_portfolio.py hit the same wall). The fallback keeps that
case working while keeping the common case (server-rendered pages, or
sites with enough static content) cheap and fast.

Originally a separate microservice (job-scraper-service) to keep the
Playwright/Chromium dependency off the main API's Render instance. Merged
back in once BROWSERLESS_WS_URL made the browser itself remote (no local
Chromium needed here, just the thin Playwright client) — at that point a
second Render service only added a cold-start penalty (~50s on free tier)
with no memory benefit left to justify it.

Single Responsibility: this module only knows how to turn a URL into
JobListing objects. It doesn't know about ranking or storage — that's
sources/scraper_service_source.py's concern.
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from cache import SqliteCache, content_hash
from models import JobListing

logger = logging.getLogger("url_scraper")

_SOURCE_NAME = "custom_url"

# Render's free tier (512MB) can't reliably run headless Chromium
# in-process — the browser binary alone needs 300-500MB+ under load.
# Connect to a remote browser (Browserless.io's free tier) over CDP when
# configured; this process then only needs the lightweight Playwright
# *client*, not the browser itself. Falls back to a local Chromium launch
# when unset — the right default for local dev, where
# `playwright install chromium` already put a real browser on disk.
_BROWSERLESS_WS_URL = os.getenv("BROWSERLESS_WS_URL", "")

# A page with fewer than this many bytes of HTML, or with no <body> text
# of substance, is almost certainly a JS-rendered shell (React/Vue root
# div with nothing in it yet) rather than a real empty page — worth a
# Playwright render before giving up. Picked conservatively: a real
# server-rendered job listing page is rarely this small.
_THIN_HTML_BYTES_THRESHOLD = 3000

_HTTPX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# URL scrapes don't change fast enough to justify re-rendering on every
# request — cache.SqliteCache has no native TTL, so it's enforced here via
# a "fetched_at" timestamp stored inside the cached value, same pattern as
# sources/apify_source.py.
_CACHE_TTL_SECONDS = 6 * 60 * 60
_cache = SqliteCache()

# Heuristic vocabulary for spotting "this looks like a job card" repeated
# elements on a listing page — class/id name fragments used by common ATS
# platforms and job boards (LinkedIn, Naukri, Greenhouse, Lever, Workday,
# generic WordPress job plugins, etc.). Not exhaustive by design — this is
# a best-effort heuristic, not a guarantee, same spirit as
# agents/ats_checker.py's skill vocabulary: extend it as real sites are
# tried and found not to match.
_CARD_HINTS = [
    "job-card", "jobcard", "job-item", "job-listing", "job-result",
    "search-result", "posting-card", "career-item", "opening", "vacancy",
    "job-tile", "position-card",
]

_TITLE_HINTS = ["job-title", "jobtitle", "position-title", "posting-title"]
_COMPANY_HINTS = ["company-name", "companyname", "employer"]
_LOCATION_HINTS = ["job-location", "joblocation", "location"]

# Repeated site-chrome phrases that show up verbatim across a whole nav
# menu or footer, rather than in actual job-description prose — used to
# discount candidate blocks that are mostly boilerplate.
_BOILERPLATE_PHRASES = [
    "sign in", "post a job", "all jobs", "search by", "frequently asked questions",
    "privacy policy", "terms of service", "cookie", "subscribe", "newsletter",
    "follow us", "© ", "all rights reserved",
]


def _has_hint(el, hints: list[str]) -> bool:
    classes = " ".join(el.get("class", [])).lower()
    el_id = (el.get("id") or "").lower()
    haystack = f"{classes} {el_id}"
    return any(h in haystack for h in hints)


def _find_card_elements(soup: BeautifulSoup):
    """Finds repeated elements that look like individual job cards on a
    listing page. Returns the list of matched elements (empty if this
    doesn't look like a listing page)."""
    candidates = soup.find_all(attrs={"class": True})
    matched = [el for el in candidates if _has_hint(el, _CARD_HINTS)]
    # De-duplicate nested matches (a card's own children can also match the
    # hint vocabulary) by keeping only outermost matches.
    outer = []
    for el in matched:
        if not any(el in parent.find_all() for parent in outer):
            outer.append(el)
    return outer


def _extract_text_by_hints(el, hints: list[str], fallback_tag: str | None = None) -> str:
    for hint_el in el.find_all(attrs={"class": True}):
        if _has_hint(hint_el, hints):
            text = hint_el.get_text(strip=True)
            if text:
                return text
    if fallback_tag:
        found = el.find(fallback_tag)
        if found:
            return found.get_text(strip=True)
    return ""


def _job_from_card(card, base_url: str, index: int) -> JobListing | None:
    title = _extract_text_by_hints(card, _TITLE_HINTS, fallback_tag="h2") or _extract_text_by_hints(
        card, _TITLE_HINTS, fallback_tag="h3"
    )
    if not title:
        # Fall back to the first link's text — most job cards wrap the
        # title in an <a>, even without a matching class name.
        link = card.find("a")
        if link:
            title = link.get_text(strip=True)
    if not title:
        return None

    company = _extract_text_by_hints(card, _COMPANY_HINTS)
    location = _extract_text_by_hints(card, _LOCATION_HINTS)

    link = card.find("a", href=True)
    url = urljoin(base_url, link["href"]) if link else base_url

    description = card.get_text(" ", strip=True)[:1000]

    return JobListing(
        source=_SOURCE_NAME,
        external_id=f"{url}#{index}",
        title=title,
        company=company or "Unknown",
        location=location,
        remote="remote" in location.lower() if location else False,
        description=description,
        url=url,
    )


def _boilerplate_density(text: str) -> float:
    """Fraction of the text's length covered by known site-chrome phrases —
    a proxy for 'this block is mostly nav/footer, not job content'."""
    if not text:
        return 1.0
    lowered = text.lower()
    covered = sum(len(p) for p in _BOILERPLATE_PHRASES if p in lowered)
    return covered / len(lowered)


def _extract_single_posting(soup: BeautifulSoup, url: str) -> JobListing:
    """Treats the whole page as one job posting — used when no repeated
    card structure is found. Title is the page's <h1> or <title>.

    Description picks the best candidate content block rather than simply
    the largest by text length: a raw-length max grabs whole-page nav/
    footer boilerplate on pages that aren't a clean single-posting layout.
    Semantic tags (<article>, <main>) are preferred outright since they're
    purpose-built for primary content; among <div>/<section> candidates,
    blocks with high boilerplate-phrase density are discounted rather than
    picked just for being long."""
    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else "Untitled posting"

    semantic = soup.find_all(["article", "main"])
    generic = soup.find_all(["div", "section"])

    def _score(el) -> float:
        text = el.get_text(strip=True)
        if len(text) < 100:
            return -1  # too short to be a real description
        return len(text) * (1 - _boilerplate_density(text))

    body = max(semantic, key=_score, default=None)
    if body is None or _score(body) <= 0:
        body = max(generic, key=_score, default=None)
    description = (
        body.get_text(" ", strip=True)[:3000] if body and _score(body) > 0
        else soup.get_text(" ", strip=True)[:3000]
    )

    company = ""
    for el in soup.find_all(attrs={"class": True}):
        if _has_hint(el, _COMPANY_HINTS):
            company = el.get_text(strip=True)
            break

    return JobListing(
        source=_SOURCE_NAME,
        external_id=url,
        title=title,
        company=company or "Unknown",
        location="",
        remote=False,
        description=description,
        url=url,
    )


def scrape_url(url: str, timeout_ms: int = 30000, use_cache: bool = True) -> list[JobListing]:
    """Cache-checking wrapper around _scrape_url_uncached. Cache key is
    just the URL's content hash — no query/params to fold in here, since
    this scrapes exact URLs rather than running parameterized searches.
    Returns an empty list on any failure (never raises) — callers treat
    "nothing found" the same whether the cause was a bad URL, a bot wall,
    or a genuinely empty page."""
    cache_key = "url_scrape:" + content_hash(url)

    if use_cache:
        cached = _cache.get(cache_key)
        if cached is not None and (time.time() - cached["fetched_at"]) < _CACHE_TTL_SECONDS:
            age_min = int((time.time() - cached["fetched_at"]) / 60)
            logger.info("scrape_url: cache hit for %s (age %dm)", url, age_min)
            return [JobListing(**j) for j in cached["jobs"]]

    jobs, error = _scrape_url_uncached(url, timeout_ms)

    # Don't cache outright failures — a transient timeout/block shouldn't
    # poison the cache for the TTL window; only successful renders are
    # worth avoiding a re-scrape for.
    if use_cache and error is None:
        _cache.set(cache_key, {"fetched_at": time.time(), "jobs": [j.model_dump() for j in jobs]})

    return jobs


def _extract_from_html(html: str, url: str) -> list[JobListing]:
    """Shared extraction logic for both fetch tiers: parse, strip noise
    tags, apply the listing-vs-single-posting heuristic."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    cards = _find_card_elements(soup)
    logger.info("scrape_url: found %d card-like elements", len(cards))

    if len(cards) >= 2:
        jobs = []
        for i, card in enumerate(cards):
            job = _job_from_card(card, url, i)
            if job:
                jobs.append(job)
        logger.info("scrape_url: extracted %d jobs as listing page", len(jobs))
        return jobs

    # Fewer than 2 card-like matches — treat as a single posting rather
    # than a listing. (0 or 1 "card" isn't a meaningful listing.)
    single = _extract_single_posting(soup, url)
    logger.info("scrape_url: extracted 1 job as single_posting")
    return [single]


def _looks_thin(html: str) -> bool:
    """A JS-rendered SPA's initial HTML is typically a near-empty shell
    (root div, script tags, no real body text) — small in bytes and light
    on visible text even if the raw HTML string itself isn't tiny (inline
    JSON blobs can pad byte count without adding readable content). Check
    both: byte count as a cheap first filter, then actual visible text
    length as the real signal."""
    if len(html) < _THIN_HTML_BYTES_THRESHOLD:
        return True
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    visible_text = soup.get_text(strip=True)
    return len(visible_text) < 500


def _fetch_httpx(url: str, timeout_seconds: float) -> str | None:
    """Fast path: a plain GET works fine for server-rendered pages and
    costs a fraction of a Playwright render. Returns None on any failure
    (network error, non-2xx, or non-HTML content-type) so the caller falls
    through to Playwright.

    Confirmed real failure mode: weworkremotely.com serves an RSS/XML feed
    at the same URL a browser would get an HTML listing page from, when
    the request doesn't send a browser-like Accept header — httpx without
    one got back `application/rss+xml`, which the HTML card-scraping
    heuristic then silently misparsed as a single garbage "job" (the feed
    title). An explicit Accept: text/html header avoids that for sites
    that content-negotiate on it; the content-type check below is a
    backstop for sites that don't honor it."""
    try:
        resp = httpx.get(
            url,
            headers={
                "User-Agent": _HTTPX_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=timeout_seconds,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("scrape_url: httpx fetch failed for %s: %s", url, e)
        return None

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type.lower():
        logger.warning(
            "scrape_url: httpx got non-HTML content-type %r for %s — treating as a miss",
            content_type, url,
        )
        return None

    return resp.text


def _fetch_playwright(url: str, timeout_ms: int) -> tuple[str | None, str | None]:
    """Fallback path for JS-rendered pages: renders with a real (or
    remote, via Browserless) browser. Imported lazily so this module
    doesn't hard-require playwright to even import — most requests never
    reach this path."""
    from playwright.sync_api import sync_playwright

    logger.info("scrape_url: httpx result too thin, falling back to Playwright render")
    try:
        with sync_playwright() as p:
            # Headless Chromium's default fingerprint (headless UA string,
            # no viewport, missing navigator.webdriver spoofing) is what
            # trips bot-detection walls like Cloudflare's managed
            # challenge. A realistic desktop UA + viewport + the standard
            # automation-flag suppression args meaningfully improve
            # pass-through on sites using basic bot checks — not a
            # guarantee against a full JS/CAPTCHA challenge, but a real
            # improvement for the common case.
            if _BROWSERLESS_WS_URL:
                logger.info("scrape_url: connecting to remote browser (Browserless)")
                browser = p.chromium.connect(_BROWSERLESS_WS_URL)
            else:
                browser = p.chromium.launch(
                    args=["--disable-blink-features=AutomationControlled"]
                )
            context = browser.new_context(
                user_agent=_HTTPX_USER_AGENT,
                viewport={"width": 1366, "height": 900},
                locale="en-US",
            )
            page = context.new_page()
            try:
                # "networkidle" (no network activity for 500ms) is
                # unreliable on real-world pages — confirmed by a real
                # timeout against remotive.com, which (like most job
                # boards) has continuous background activity (analytics
                # beacons, polling widgets) that never lets the network go
                # fully idle. "domcontentloaded" is the DOM-ready signal
                # instead, backed by an explicit settle delay below for
                # client-side rendering to catch up.
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as nav_err:
                # Some hosts (Cloudflare-fronted sites, slow SPAs) never
                # fire "domcontentloaded" cleanly either — fall back to
                # whatever loaded so far rather than failing outright.
                logger.warning("scrape_url: goto raised %s, using partial content", nav_err)
            logger.info("scrape_url: dom loaded, settling for client-side render")
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
            return html, None
    except Exception as e:
        logger.error("scrape_url: browser/navigation failed for %s: %s", url, e)
        return None, str(e)


def _scrape_url_uncached(url: str, timeout_ms: int = 30000) -> tuple[list[JobListing], str | None]:
    """Two-tier fetch: fast httpx GET first, Playwright render only if that
    result looks too thin to contain real job data (JS-rendered shell).

    Logs each stage at INFO so a failure or slow run points at the exact
    step instead of one opaque try/except — real job boards fail in
    different ways (nav timeout, empty DOM, no card matches) and this
    makes each of those visible."""
    logger.info("scrape_url: fetching %s (httpx first)", url)
    html = _fetch_httpx(url, timeout_seconds=timeout_ms / 1000)

    if html is None or _looks_thin(html):
        html, error = _fetch_playwright(url, timeout_ms)
        if html is None:
            return [], error
    else:
        logger.info("scrape_url: httpx result has enough content, skipping Playwright")

    logger.info("scrape_url: got %d bytes of HTML, parsing", len(html))
    jobs = _extract_from_html(html, url)
    return jobs, None
