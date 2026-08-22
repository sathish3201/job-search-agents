"""ATS (Applicant Tracking System) compatibility check: how a real
keyword-matching ATS would score this resume against a specific job
description — a different, complementary signal to ranker.py's LLM-based
"is this a good fit" judgment.

Real ATS software (Workday, Taleo, Greenhouse's screening, etc.) is
largely NOT semantic — it extracts a required-keyword list from the job
posting and checks for literal (or near-literal, stemmed) presence in the
resume text. A resume can be a great human fit and still fail an ATS if it
never says the exact words the posting uses. That's the gap this module
checks for, deterministically, before any LLM involvement:

    1. Extract candidate keywords from the job description via regex/
       phrase-list matching (no LLM — deterministic, matches how real ATS
       parsers work, and free/instant).
    2. Check literal presence of each keyword in the resume text.
    3. Compute a keyword-coverage score the same way real ATS systems do:
       percentage of required keywords found.
    4. Hand the deterministic match/gap lists to the LLM only for the
       parts that genuinely need judgment — an ATS-style summary of what
       to literally add to the resume text to close the gap.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from llm import get_llm
from models import CandidateProfile, JobListing, TailoredResume

# Real ATS keyword lists come from a maintained skill/tool taxonomy, not
# free-form phrase extraction off the posting text (naive n-gram extraction
# produces garbage like "we are looking" or phrases split across line
# breaks). This is a practical, extensible vocabulary of skill/tool names
# an ATS would plausibly be configured to look for — checked for literal
# presence in the job description first (only matched keywords are
# "required" for this posting), then checked against the resume.
_SKILL_VOCABULARY = [
    # Languages
    "python", "javascript", "typescript", "java", "c#", "c++", "go", "rust",
    "ruby", "php", "sql", "kotlin", "swift",
    # Frontend
    "react", "vue", "angular", "next.js", "node.js", "express", "tailwind css",
    "html", "css", "redux", "vite",
    # Backend/API
    "rest api", "graphql", "fastapi", "django", "flask", "spring boot",
    "microservices", "grpc",
    # Data/DB
    "mongodb", "postgresql", "mysql", "oracle pl/sql", "redis", "elasticsearch",
    "dbms_stats", "sql performance tuning", "etl", "data warehousing",
    "chromadb", "vector search", "rag",
    # Cloud/DevOps
    "aws", "azure", "gcp", "docker", "kubernetes", "ci/cd", "terraform",
    "github actions", "jenkins", "render", "vercel",
    # AI/ML
    "llm", "machine learning", "rag", "mcp", "langchain", "langgraph",
    "fine-tuning", "prompt engineering", "nlp",
    # Practices
    "agile", "scrum", "unit testing", "tdd", "code review", "git",
    "jwt authentication", "oauth", "microservices",
    # Support/ops
    "sla", "root cause analysis", "incident management", "putty", "winscp",
]


def extract_keywords(job_description: str, max_keywords: int = 30) -> list[str]:
    """Returns the subset of the skill vocabulary literally present in the
    job description — these are what an ATS built around this taxonomy
    would treat as "required" for this posting. Deterministic (no LLM):
    matches how real ATS keyword extraction works, runs fast and free
    against every job in a batch."""
    jd_lower = job_description.lower()
    found = [kw for kw in _SKILL_VOCABULARY if re.search(rf"\b{re.escape(kw)}\b", jd_lower)]
    return found[:max_keywords]


def check_keyword_presence(keywords: list[str], resume_text: str) -> tuple[list[str], list[str]]:
    """Literal (case-insensitive) presence check — this is what real ATS
    parsers actually do, not semantic similarity. Returns (found, missing)."""
    resume_lower = resume_text.lower()
    found, missing = [], []
    for kw in keywords:
        if kw.lower() in resume_lower:
            found.append(kw)
        else:
            missing.append(kw)
    return found, missing


class AtsCheckResult(BaseModel):
    ats_score: int  # 0-100, % of extracted keywords literally present in the resume
    keywords_found: list[str]
    keywords_missing: list[str]
    recommendation: str  # short, concrete: what literal phrases to add and where


def run_ats_check(job: JobListing, profile: CandidateProfile) -> AtsCheckResult:
    """The deterministic core: extract, check, score. No LLM call at all
    unless keywords are missing (see below) — a resume that already covers
    every extracted keyword doesn't need LLM commentary to say so."""
    keywords = extract_keywords(job.description or job.title)
    if not keywords:
        return AtsCheckResult(
            ats_score=100,
            keywords_found=[],
            keywords_missing=[],
            recommendation="No specific ATS keywords could be extracted from this job posting.",
        )

    found, missing = check_keyword_presence(keywords, profile.resume_raw_text)
    ats_score = round(100 * len(found) / len(keywords))

    if not missing:
        recommendation = "All extracted keywords are present — this resume should pass ATS keyword screening for this posting."
    else:
        recommendation = _draft_recommendation(job, profile, missing)

    return AtsCheckResult(
        ats_score=ats_score,
        keywords_found=found,
        keywords_missing=missing,
        recommendation=recommendation,
    )


def tailor_resume_for_target(
    job: JobListing, profile: CandidateProfile, target_score: int = 90, max_attempts: int = 3
) -> TailoredResume | None:
    """Drafts an ATS-tuned headline/summary for one target job, re-scoring
    against the same deterministic check_keyword_presence() each attempt so
    "hit target_score" is verified, not just claimed by the LLM. Only ever
    weaves in keywords that are a truthful match to profile.skills — the
    LLM is told explicitly not to claim skills the candidate doesn't have,
    and a keyword only counts as "added" if it's both ATS-required AND
    already a real skill (see _truthfully_addable below). Returns None if
    target_score can't be reached truthfully within max_attempts — that's a
    real, useful signal ("this resume can't ethically hit 90 for this job"),
    not a failure to hide."""
    keywords = extract_keywords(job.description or job.title)
    if not keywords:
        return None

    original_found, _ = check_keyword_presence(keywords, profile.resume_raw_text)
    original_score = round(100 * len(original_found) / len(keywords)) if keywords else 100

    current_headline = profile.headline
    current_summary = profile.summary
    keywords_added: list[str] = []
    reasoning = ""

    for _ in range(max_attempts):
        combined_text = f"{current_headline} {current_summary}"
        found, missing = check_keyword_presence(keywords, combined_text + " " + profile.resume_raw_text)
        score = round(100 * len(found) / len(keywords))

        if score >= target_score:
            return TailoredResume(
                target_title=job.title,
                based_on_job_url=job.url,
                original_ats_score=original_score,
                final_ats_score=score,
                tailored_headline=current_headline,
                tailored_summary=current_summary,
                keywords_added=keywords_added,
                reasoning=reasoning or "Existing resume already covered enough keywords for this target.",
            )

        truthfully_addable = _truthfully_addable(missing, profile.skills)
        if not truthfully_addable:
            break  # no more truthful ground to cover — stop rather than fabricate

        draft_headline, draft_summary, reasoning = _draft_tailored_text(
            job, profile, current_headline, current_summary, truthfully_addable
        )
        # Deterministic guard, not trusting the LLM's own "don't invent"
        # compliance: a real run produced "reduced deployment time by 50%"
        # for a candidate whose actual resume had no such metric anywhere.
        # The prompt now also warns against this, but small local models
        # still drop instructions under pressure to sound impressive — this
        # is the actual backstop.
        current_headline = _strip_fabricated_numbers(
            draft_headline, profile.resume_raw_text, fallback_text=current_headline
        )
        current_summary = _strip_fabricated_numbers(
            draft_summary, profile.resume_raw_text, fallback_text=current_summary
        )
        keywords_added.extend(truthfully_addable)

    # Ran out of attempts or truthful keywords without reaching target_score.
    final_found, _ = check_keyword_presence(
        keywords, f"{current_headline} {current_summary} {profile.resume_raw_text}"
    )
    final_score = round(100 * len(final_found) / len(keywords))
    if final_score <= original_score:
        return None  # no honest improvement possible — don't return a no-op draft

    return TailoredResume(
        target_title=job.title,
        based_on_job_url=job.url,
        original_ats_score=original_score,
        final_ats_score=final_score,
        tailored_headline=current_headline,
        tailored_summary=current_summary,
        keywords_added=keywords_added,
        reasoning=reasoning + f" (Reached {final_score}/100, short of the {target_score} target — "
        "no further truthful keywords to add.)",
    )


_NUMBER_RE = re.compile(r"\d[\d,.]*%?")


def _strip_fabricated_numbers(draft_text: str, source_text: str, fallback_text: str) -> str:
    """Deterministic fabrication guard: any numeric token (count, percentage,
    dollar figure, year count, etc.) in the drafted text that doesn't
    literally appear in the candidate's own resume text gets that whole
    sentence removed, rather than trusting the LLM's "don't invent a
    metric" instruction to actually hold. Confirmed necessary by a real
    failure: a drafted summary claimed "reduced deployment time by 50%"
    for a candidate whose resume had no such figure anywhere. Whole
    sentences are dropped rather than just the number, since editing out
    only the digits usually leaves a grammatically broken or misleading
    claim behind (e.g. "reduced deployment time by %" or "reduced
    deployment time" implying an unstated exact result).

    fallback_text is the pre-draft (previous-iteration) text to use if
    stripping empties the draft entirely — NOT draft_text itself, since
    that would silently let a wholly-fabricated one-sentence claim through
    unfiltered."""
    allowed_numbers = set(_NUMBER_RE.findall(source_text))

    # Split on sentence-ending punctuation, keeping it simple — this text
    # is always a short one-line headline or 2-3 sentence summary, not
    # prose needing a real sentence tokenizer.
    sentences = re.split(r"(?<=[.!?])\s+", draft_text.strip())
    kept = []
    for sentence in sentences:
        numbers_in_sentence = set(_NUMBER_RE.findall(sentence))
        fabricated = numbers_in_sentence - allowed_numbers
        if fabricated:
            continue  # drop this sentence entirely
        kept.append(sentence)

    result = " ".join(kept).strip()
    return result if result else fallback_text


def _truthfully_addable(missing_keywords: list[str], candidate_skills: list[str]) -> list[str]:
    """A missing ATS keyword is only safe to add if it's a real skill the
    candidate already listed — this is the guardrail against fabricating
    experience. Simple substring match in either direction catches most
    real cases (e.g. keyword "sql" matches skill "Oracle PL/SQL")."""
    skills_lower = [s.lower() for s in candidate_skills]
    addable = []
    for kw in missing_keywords:
        kw_lower = kw.lower()
        if any(kw_lower in s or s in kw_lower for s in skills_lower):
            addable.append(kw)
    return addable


def _draft_tailored_text(
    job: JobListing,
    profile: CandidateProfile,
    current_headline: str,
    current_summary: str,
    keywords_to_weave_in: list[str],
) -> tuple[str, str, str]:
    """Asks the LLM to rephrase (never fabricate) the headline/summary to
    literally include the given keywords, since the candidate already has
    them as real skills (enforced by _truthfully_addable before this is
    ever called)."""
    prompt = f"""You are a senior technical recruiter tailoring a candidate's
resume for one specific role. Rewrite the headline and summary below to:

1. Naturally include these exact keywords, since the candidate genuinely
   has these skills already (just make sure the literal words appear):
   {", ".join(keywords_to_weave_in)}
2. Lead with active, ownership-focused language ("built", "designed",
   "shipped") instead of passive responsibility language ("responsible
   for") — but ONLY reframe verbs and structure, never add a number,
   percentage, or outcome that isn't already stated in the candidate's
   own text below. If the original text has no metric, the rewrite must
   not have one either.
3. Mirror the tone and priorities of the target job posting below, so the
   summary reads like it was written for this specific role, not generic.

Target job: {job.title} at {job.company}
Job posting (for tone/priorities only — do not copy claims from it):
{job.description[:600]}

Current headline: {current_headline}
Current summary: {current_summary}

Do NOT invent any experience, skill, metric, percentage, or claim not
already true of this candidate. Only rephrase/reorder/emphasize what's
real, and add the literal keyword text.

Respond in exactly this format:
HEADLINE: <max 220 chars>
SUMMARY: <2-3 sentences>
REASONING: <one sentence on what changed and why>
"""
    try:
        llm = get_llm(max_tokens=300)
        resp = llm.invoke(prompt).content
    except Exception as e:
        return current_headline, current_summary, f"Could not draft tailored text: {e}"

    headline, summary, reasoning = current_headline, current_summary, ""
    for line in resp.splitlines():
        if line.startswith("HEADLINE:"):
            headline = line.split(":", 1)[1].strip()
        elif line.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
        elif line.startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()
    return headline, summary, reasoning


def _draft_recommendation(job: JobListing, profile: CandidateProfile, missing: list[str]) -> str:
    """Only the recommendation text needs LLM judgment (how to truthfully
    phrase adding a missing keyword) — the scoring itself stays deterministic
    above this call, so a slow/failed LLM call here degrades gracefully to a
    plain list rather than losing the actual ATS score."""
    prompt = f"""A resume is missing these exact keywords that an ATS system
is scanning for, based on the job "{job.title}" at {job.company}:
{", ".join(missing[:15])}

Candidate's real skills: {", ".join(profile.skills[:15])}

In 1-2 sentences, suggest which of these missing keywords the candidate
could truthfully add to their resume (only if it's a real, adjacent skill
they already have — never invent experience), and where. If none apply
truthfully, say so plainly.
"""
    try:
        llm = get_llm(max_tokens=150)
        return llm.invoke(prompt).content.strip()
    except Exception as e:
        return f"Missing keywords: {', '.join(missing[:15])}. (LLM recommendation unavailable: {e})"
