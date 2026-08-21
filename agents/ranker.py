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


def _clean_skill_list(raw: str, max_items: int = 8) -> list[str]:
    """Small local models sometimes degenerate into repeating the same token
    hundreds of times (a known repetition-loop failure mode, especially on
    poor-fit/out-of-domain jobs). De-duplicate and cap so one bad generation
    can't blow up downstream aggregation (e.g. the improvement report)."""
    seen = []
    for item in raw.split(","):
        item = item.strip()
        if item and item not in seen:
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
            pitch = line.split(":", 1)[1].strip()
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

        llm = get_llm()
        prompt = f"""You are a career-fit assessor. Score how well this candidate fits this job.

CANDIDATE PROFILE:
Headline: {profile.headline}
Summary: {profile.summary}
Skills: {", ".join(profile.skills)}
Years of experience: {profile.years_experience}

JOB:
Title: {job.title}
Company: {job.company}
Description: {job.description[:1200]}

Respond in this exact format:
SCORE: <0-100 integer>
MATCHING_SKILLS: <comma-separated, max 6>
MISSING_SKILLS: <comma-separated, max 6>
PITCH: <one paragraph, first person, why this candidate is a strong fit>
"""
        resp = llm.invoke(prompt).content
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
    ) -> list[RankedJob]:
        """on_progress(done, total) fires after each job finishes ranking —
        each call can be a slow remote LLM round-trip, so callers (the API
        layer) use this to report live progress instead of a flat "running"
        with no feedback for minutes."""
        if not jobs:
            return []

        total = len(jobs)
        done = 0
        ranked: list[RankedJob] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = [pool.submit(self._rank_one, job, profile) for job in jobs]
            for future in futures:
                ranked.append(future.result())
                done += 1
                if on_progress:
                    on_progress(done, total)

        ranked.sort(key=lambda r: r.fit_score, reverse=True)
        return ranked
