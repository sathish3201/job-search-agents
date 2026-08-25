"""Loads and LLM-parses the candidate's RESUME.md into a structured CandidateProfile.
Parsing is cached in SQLite keyed by a hash of the resume text (see cache.py,
the same store used by agents/ranker.py) — an exact-match key-value lookup is
the right tool here, not ChromaDB: this is "have I parsed this exact text
before", not a similarity search. RESUME.md rarely changes between runs, so
this turns the profile-parsing LLM call from "every run" into "once per
resume edit"."""
from __future__ import annotations

import os
import re
from datetime import date

from agents.ats_checker import _SKILL_VOCABULARY
from cache import SqliteCache, content_hash
from models import CandidateProfile

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Matches "Oct 2024 — Aug 2026", "Oct 2024 - Present", etc. — the em-dash/
# hyphen/en-dash variants and optional "Present"/"Current" end date.
_DATE_RANGE_RE = re.compile(
    r"([A-Za-z]{3,9})\.?\s+(\d{4})\s*[-–—]\s*(?:([A-Za-z]{3,9})\.?\s+(\d{4})|(Present|Current))",
    re.IGNORECASE,
)


def _years_experience_from_dates(resume_text: str) -> float | None:
    """Deterministic fallback/override for YEARS_EXPERIENCE: small local
    models are unreliable at date arithmetic (confirmed by a real bug — one
    run reported "2.6 years" for an Oct 2024-Aug 2026 role, which is
    actually ~1.8-2 years). Parses the widest employment date range
    literally stated in the resume and computes years from it directly,
    rather than trusting the LLM's math. Returns None if no date range is
    found, so the caller falls back to whatever the LLM extracted."""
    spans = []
    for m in _DATE_RANGE_RE.finditer(resume_text):
        start_month_name, start_year, end_month_name, end_year, present = m.groups()
        start_month = _MONTHS.get(start_month_name[:3].lower())
        if start_month is None:
            continue
        start = date(int(start_year), start_month, 1)

        if present:
            end = date.today()
        else:
            end_month = _MONTHS.get(end_month_name[:3].lower())
            if end_month is None:
                continue
            end = date(int(end_year), end_month, 1)

        if end >= start:
            spans.append((start, end))

    if not spans:
        return None

    earliest_start = min(s for s, _ in spans)
    latest_end = max(e for _, e in spans)
    months = (latest_end.year - earliest_start.year) * 12 + (latest_end.month - earliest_start.month)
    return round(months / 12, 1)


def load_resume_text(path: str | None = None) -> str:
    path = path or os.getenv("RESUME_MD_PATH", "")
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            f"RESUME_MD_PATH not found: '{path}'. Set it in .env to your resume markdown file."
        )
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_profile(
    resume_text: str, llm, cache: SqliteCache | None = None, user_id: int | str = "local"
) -> CandidateProfile:
    """Ask the LLM to extract a few structured fields with a short, direct prompt.
    Small local models (3B-class) are unreliable at full pydantic-schema JSON
    output — a plain labeled-line format is faster and much less likely to fail
    to parse, at the cost of being a bit more manual to pull apart here.

    user_id is folded into the cache key (default "local" for the
    single-user CLI/dev path via RESUME_MD_PATH) so two different users
    who happen to upload byte-identical resume text don't collide and
    read back each other's cached CandidateProfile — a real, if
    low-probability, correctness gap once multiple users exist."""
    cache = cache or SqliteCache()
    cache_key = f"profile:{user_id}:" + content_hash(resume_text)
    cached = cache.get(cache_key)
    if cached is not None:
        return CandidateProfile(**cached)

    prompt = f"""Read this resume and answer in EXACTLY this format, one line each,
no extra commentary:

NAME: <full name>
HEADLINE: <current professional title/headline, one line>
SUMMARY: <2-3 sentence summary of their background>
SKILLS: <comma-separated list of their top 10-15 technical skills>
YEARS_EXPERIENCE: <total years of professional experience, as a whole or
  one-decimal number. Compute this from the actual employment date ranges
  stated in the resume (e.g. "Oct 2024 - Aug 2026" is about 1.8-2 years) —
  do not guess or invent a number, and do not copy any example number from
  these instructions.>
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

    # Deterministic override: trust literal date-range math over the LLM's
    # arithmetic whenever a real date range is found in the resume text.
    years_from_dates = _years_experience_from_dates(resume_text)
    if years_from_dates is not None:
        years = years_from_dates

    skills = [s.strip() for s in fields["SKILLS"].split(",") if s.strip()]
    if not skills:
        # Confirmed real failure mode (not hypothetical): a live run
        # against qwen2.5:3b produced a response that skipped the SKILLS
        # line entirely (jumped straight from SUMMARY to YEARS_EXPERIENCE)
        # despite the prompt asking for it — the model doesn't always fail
        # loudly, it can just omit a field it finds harder to answer. An
        # empty skills list is worse than a merely imperfect one: every
        # downstream ranking prompt renders "Skills: ." with nothing to
        # match against, which was traced to a real bug in
        # agents/ranker.py (the LLM degenerating into bare digits for
        # MATCHING_SKILLS/MISSING_SKILLS when given nothing to match).
        # Deterministic fallback: extract skills directly from the resume
        # text via the same skill taxonomy agents/ats_checker.py already
        # uses for keyword matching (reused, not duplicated — one skill
        # vocabulary for the whole project, not two that can drift apart).
        resume_lower = resume_text.lower()
        skills = [kw for kw in _SKILL_VOCABULARY if kw in resume_lower][:15]
        print(
            f"[profile_loader] LLM omitted SKILLS field — fell back to "
            f"{len(skills)} skills extracted from resume text via keyword match",
            flush=True,
        )

    profile = CandidateProfile(
        name=fields["NAME"],
        headline=fields["HEADLINE"],
        summary=fields["SUMMARY"],
        skills=skills,
        years_experience=years,
        target_roles=[r.strip() for r in fields["TARGET_ROLES"].split(",") if r.strip()],
        resume_raw_text=resume_text,
    )
    cache.set(cache_key, profile.model_dump())
    return profile
