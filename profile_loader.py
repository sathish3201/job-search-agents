"""Loads and LLM-parses the candidate's RESUME.md into a structured CandidateProfile.
Parsing is cached in SQLite keyed by a hash of the resume text (see cache.py,
the same store used by agents/ranker.py) — an exact-match key-value lookup is
the right tool here, not ChromaDB: this is "have I parsed this exact text
before", not a similarity search. RESUME.md rarely changes between runs, so
this turns the profile-parsing LLM call from "every run" into "once per
resume edit"."""
from __future__ import annotations

import os

from cache import SqliteCache, content_hash
from models import CandidateProfile


def load_resume_text(path: str | None = None) -> str:
    path = path or os.getenv("RESUME_MD_PATH", "")
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            f"RESUME_MD_PATH not found: '{path}'. Set it in .env to your resume markdown file."
        )
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_profile(resume_text: str, llm, cache: SqliteCache | None = None) -> CandidateProfile:
    """Ask the LLM to extract a few structured fields with a short, direct prompt.
    Small local models (3B-class) are unreliable at full pydantic-schema JSON
    output — a plain labeled-line format is faster and much less likely to fail
    to parse, at the cost of being a bit more manual to pull apart here."""
    cache = cache or SqliteCache()
    cache_key = "profile:" + content_hash(resume_text)
    cached = cache.get(cache_key)
    if cached is not None:
        return CandidateProfile(**cached)

    prompt = f"""Read this resume and answer in EXACTLY this format, one line each,
no extra commentary:

NAME: <full name>
HEADLINE: <current professional title/headline, one line>
SUMMARY: <2-3 sentence summary of their background>
SKILLS: <comma-separated list of their top 10-15 technical skills>
YEARS_EXPERIENCE: <a number, e.g. 1.6>
TARGET_ROLES: <comma-separated list of 2-4 job titles they should be searching for>

Resume:
{resume_text[:4000]}
"""
    resp = llm.invoke(prompt).content

    fields = {
        "NAME": "Candidate",
        "HEADLINE": "Full Stack Developer",
        "SUMMARY": resume_text[:300],
        "SKILLS": "",
        "YEARS_EXPERIENCE": "0",
        "TARGET_ROLES": "Full Stack Developer",
    }
    for line in resp.splitlines():
        for key in fields:
            prefix = f"{key}:"
            if line.strip().upper().startswith(prefix):
                fields[key] = line.split(":", 1)[1].strip()
                break

    try:
        years = float("".join(c for c in fields["YEARS_EXPERIENCE"] if c.isdigit() or c == "."))
    except ValueError:
        years = 0.0

    profile = CandidateProfile(
        name=fields["NAME"],
        headline=fields["HEADLINE"],
        summary=fields["SUMMARY"],
        skills=[s.strip() for s in fields["SKILLS"].split(",") if s.strip()],
        years_experience=years,
        target_roles=[r.strip() for r in fields["TARGET_ROLES"].split(",") if r.strip()],
        resume_raw_text=resume_text,
    )
    cache.set(cache_key, profile.model_dump())
    return profile
