"""LLM-based fit ranking for a single job against the candidate profile.
Single responsibility: score + explain fit for one (job, profile) pair, with a
SQLite cache in front so re-running the pipeline never re-scores a job it has
already scored against the same resume text."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from cache import SqliteCache, content_hash
from llm import get_llm
from models import CandidateProfile, JobListing, RankedJob

ProgressCallback = Callable[[int, int], None]  # (done, total) -> None
RankedCallback = Callable[[RankedJob], None]  # fires the instant one job finishes


def _looks_like_unfilled_placeholder(text: str) -> bool:
    """Small local models occasionally echo the prompt's own format hint
    verbatim (e.g. literally returning "<up to 5, comma-separated>") instead
    of filling it in. Catch that so it never surfaces as real data — a
    placeholder starting with '<' and ending with '>' is never a legitimate
    skill name or pitch sentence."""
    stripped = text.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def _clean_skill_list(raw: str, max_items: int = 8) -> list[str]:
    """Small local models sometimes degenerate into repeating the same token
    hundreds of times (a known repetition-loop failure mode, especially on
    poor-fit/out-of-domain jobs). De-duplicate and cap so one bad generation
    can't blow up downstream aggregation (e.g. the improvement report)."""
    if _looks_like_unfilled_placeholder(raw):
        return []

    seen = []
    for item in raw.split(","):
        item = item.strip()
        if not item or _looks_like_unfilled_placeholder(item):
            continue
        if item not in seen:
            seen.append(item)
        if len(seen) >= max_items:
            break
    return seen


def _parse_ranking_response(resp: str) -> dict:
    score = 50
    matching, missing, pitch = [], [], ""
    for line in resp.splitlines():
        if line.startswith("SCORE:"):
            digits = "".join(c for c in line.split(":", 1)[1] if c.isdigit())
            if digits:
                score = max(0, min(100, int(digits)))
        elif line.startswith("MATCHING_SKILLS:"):
            matching = _clean_skill_list(line.split(":", 1)[1])
        elif line.startswith("MISSING_SKILLS:"):
            missing = _clean_skill_list(line.split(":", 1)[1])
        elif line.startswith("PITCH:"):
            candidate_pitch = line.split(":", 1)[1].strip()
            pitch = "" if _looks_like_unfilled_placeholder(candidate_pitch) else candidate_pitch
    return {"score": score, "matching": matching, "missing": missing, "pitch": pitch}


class LLMRanker:
    def __init__(self, cache: SqliteCache | None = None, max_workers: int = 2):
        """max_workers=2 by default: the phone backend (llama-server with
        --parallel 1) serves one request at a time, so higher worker counts
        just queue on the server without speeding anything up. The SQLite
        cache in front of this (see _rank_one) is what actually makes repeat
        runs fast — cached jobs skip the LLM call and the thread pool entirely."""
        self._cache = cache or SqliteCache()
        self._max_workers = max_workers

    def _rank_one(self, job: JobListing, profile: CandidateProfile) -> RankedJob:
        cache_key = "rank:" + content_hash(job.dedupe_key, profile.resume_raw_text)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return RankedJob(
                job=job,
                fit_score=cached["score"],
                reasoning=cached.get("reasoning", ""),
                matching_skills=cached["matching"],
                missing_skills=cached["missing"],
                tailored_pitch=cached["pitch"],
            )

        # Keep the prompt lean: this call runs once per job against a
        # phone-hosted model, so every extra hundred input/output tokens is
        # real wall-clock time. Trim the job description to its first ~500
        # chars (title/summary line, usually enough to judge fit) and ask for
        # a short pitch (1-2 sentences) instead of a full paragraph.
        llm = get_llm(max_tokens=220)
        prompt = f"""Score this candidate's fit for the job. Be terse.

CANDIDATE: {profile.headline}. Skills: {", ".join(profile.skills[:12])}. {profile.years_experience}y exp.

JOB: {job.title} at {job.company}. {job.description[:500]}

Respond in exactly this format, nothing else:
SCORE: <0-100>
MATCHING_SKILLS: <up to 5, comma-separated>
MISSING_SKILLS: <up to 5, comma-separated>
PITCH: <1-2 sentences, first person, why this candidate fits>
"""
        try:
            resp = llm.invoke(prompt).content
        except Exception as e:
            # A stuck/failed connection to the LLM backend (e.g. a stalled
            # ngrok tunnel) must not take the whole batch down with it —
            # one job fails to rank, the rest still get a fair shot. Not
            # cached, so a transient failure gets retried on the next run.
            print(f"[ranker] {job.title} @ {job.company}: LLM call failed ({e}); scoring 0", flush=True)
            return RankedJob(
                job=job,
                fit_score=0,
                reasoning=f"Ranking failed: {e}",
                matching_skills=[],
                missing_skills=[],
                tailored_pitch="",
            )

        parsed = _parse_ranking_response(resp)
        self._cache.set(cache_key, {**parsed, "reasoning": resp})

        return RankedJob(
            job=job,
            fit_score=parsed["score"],
            reasoning=resp,
            matching_skills=parsed["matching"],
            missing_skills=parsed["missing"],
            tailored_pitch=parsed["pitch"],
        )

    def rank_all(
        self,
        jobs: list[JobListing],
        profile: CandidateProfile,
        on_progress: Optional[ProgressCallback] = None,
        on_ranked: Optional[RankedCallback] = None,
    ) -> list[RankedJob]:
        """on_progress(done, total) fires after each job finishes ranking —
        each call can be a slow remote LLM round-trip, so callers (the API
        layer) use this to report live progress instead of a flat "running"
        with no feedback for minutes.

        on_ranked(job) fires with each individual RankedJob the instant it's
        produced, before the whole batch finishes — used to persist results
        to disk incrementally (see graph.py's rank_jobs_node) so a mid-run
        process restart (Render free tier can do this) loses at most the
        one job in flight, not the entire run's work."""
        if not jobs:
            return []

        total = len(jobs)
        done = 0
        ranked: list[RankedJob] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = [pool.submit(self._rank_one, job, profile) for job in jobs]
            for future in futures:
                result = future.result()
                ranked.append(result)
                if on_ranked:
                    on_ranked(result)
                done += 1
                if on_progress:
                    on_progress(done, total)

        ranked.sort(key=lambda r: r.fit_score, reverse=True)
        return ranked
