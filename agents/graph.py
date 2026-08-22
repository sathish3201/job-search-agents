"""The main LangGraph pipeline wiring together search -> rank -> ATS-check -> track -> profile-draft.

Flow:
    search_jobs -> rank_jobs (cached LLM scoring, parallel per job) ->
    ats_check_jobs (deterministic keyword match, filters to what's shown) ->
    update_tracker -> draft_profile_updates -> END

Resume tailoring (agents/ats_checker.tailor_resume_for_target) is NOT part
of this automatic pipeline — it's on-demand, triggered per job by the user
clicking "Tailor Resume" on a specific job card (see
api/routers/tailor.py), not run automatically for every job on every
search. Tailoring is an LLM call per attempt (up to 3 attempts to reach
ATS_TARGET_SCORE); running it unconditionally for every ranked job on every
search would be pure waste when the user only cares about 1-2 of them.

There was previously an embedding-based shortlist step (fastembed) between
search and rank, meant to cut LLM calls by pre-filtering to the top-K most
relevant jobs before the slow ranking step. Removed: fastembed's ONNX
inference is CPU-only and Render's free tier doesn't have the headroom for
it — a handful of embed() calls could hang for minutes, which looked like
the whole pipeline was stuck. At the actual job volume here (~15/run) the
LLM ranker alone is fast enough (see agents/ranker.py's trimmed prompt)
that a pre-filter isn't worth the infrastructure cost. If job volume grows
significantly, reintroduce filtering via the LLM backend's own embeddings
endpoint instead of a separate CPU-bound library.

Each node delegates to a dedicated module (ranker.py) rather than
containing the logic inline — the graph's only job is orchestration.
"""
from __future__ import annotations

import operator
from typing import Annotated

from pydantic import BaseModel

from langgraph.graph import StateGraph, START, END

from agents.ats_checker import run_ats_check
from agents.ranker import LLMRanker
from cache import SqliteCache
from llm import get_llm
from models import Application, ApplicationStatus, CandidateProfile, JobListing, ProfileDraft, RankedJob
from sources import active_sources
from store import ApplicationStore

_cache = SqliteCache()
_ranker = LLMRanker(cache=_cache)

MIN_FIT_SCORE = 70  # jobs scoring below this are dropped entirely — not shown, not tracked
ATS_FRONTEND_THRESHOLD = 75  # only jobs scoring >= this on ATS keyword match are shown in the UI

# Optional hooks, set by api/pipeline_runner.py before invoking the graph, so
# individual nodes can report live progress. Not LangGraph state fields
# because callables aren't pydantic-serializable; module-level is the
# simplest wiring for a single-process deployment.
#   _progress_hook(done, total) -> None      -- per-job ranking progress
#   _stage_hook(stage_name) -> None          -- coarse "which node is running"
_progress_hook = None
_stage_hook = None


def set_progress_hook(hook) -> None:
    global _progress_hook
    _progress_hook = hook


def set_stage_hook(hook) -> None:
    global _stage_hook
    _stage_hook = hook


class PipelineState(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    query: str
    location: str
    remote_ok: bool
    profile: CandidateProfile

    raw_jobs: list[JobListing] = []
    ranked_jobs: Annotated[list[RankedJob], operator.add] = []
    # Separate field, not filtered in place: ranked_jobs uses operator.add as
    # its reducer (concatenates rather than replaces across node returns), so
    # a node that wants to narrow the list down — ats_check_jobs_node
    # filtering to ATS_FRONTEND_THRESHOLD — must write to its own field
    # instead of returning a smaller ranked_jobs and accidentally
    # concatenating onto the original.
    ats_passed_jobs: list[RankedJob] = []
    new_applications: list[Application] = []
    profile_drafts: list[ProfileDraft] = []


def search_jobs_node(state: PipelineState) -> dict:
    print("[search_jobs] starting", flush=True)
    sources = active_sources()
    if not sources:
        print("[search_jobs] No job sources configured — add API keys to .env (see .env.example).", flush=True)
        return {"raw_jobs": []}

    all_jobs: list[JobListing] = []
    for src in sources:
        if _stage_hook:
            _stage_hook(f"Searching {src.name}")
        print(f"[search_jobs] calling {src.name}...", flush=True)
        jobs = src.search(state.query, state.location, state.remote_ok, limit=15)
        print(f"[search_jobs] {src.name}: {len(jobs)} results", flush=True)
        all_jobs.extend(jobs)

    # De-duplicate across sources by (title, company) as a cheap heuristic,
    # on top of each source's own external_id uniqueness.
    seen = set()
    deduped = []
    for job in all_jobs:
        key = (job.title.lower().strip(), job.company.lower().strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(job)

    return {"raw_jobs": deduped}


def _persist_if_qualifying(ranked: RankedJob) -> None:
    """Writes one job to the application tracker the instant it's ranked,
    if it clears MIN_FIT_SCORE. Called from LLMRanker's on_ranked hook, so
    results survive a mid-run process restart — previously everything was
    held in memory until update_tracker_node ran at the very end, which
    meant a container restart (Render free tier can do this) silently threw
    away an entire run's worth of completed LLM calls with no error shown,
    just a reset to "idle" with empty results. Idempotent: ApplicationStore
    upserts by dedupe_key, so update_tracker_node re-running over the same
    jobs afterward is a harmless no-op, not a double-write."""
    if ranked.fit_score < MIN_FIT_SCORE:
        return
    store = ApplicationStore()
    key = ranked.job.dedupe_key
    if key in store.seen_keys():
        return
    store.upsert(
        Application(
            dedupe_key=key,
            job=ranked.job,
            status=ApplicationStatus.FOUND,
            fit_score=ranked.fit_score,
        )
    )


def rank_jobs_node(state: PipelineState) -> dict:
    """LLM-scores every raw job, via LLMRanker (agents/ranker.py), which itself
    is cached per (job, resume) hash in SQLite — a repeat run over the same
    jobs costs zero LLM calls. Jobs scoring below MIN_FIT_SCORE are dropped
    here so they never reach the tracker, the report, or the UI. Each result
    is also persisted to disk the moment it's produced (see
    _persist_if_qualifying) rather than waiting for the whole batch."""
    if not state.raw_jobs:
        return {"ranked_jobs": []}

    if _stage_hook:
        _stage_hook(f"Ranking {len(state.raw_jobs)} jobs")
    ranked = _ranker.rank_all(
        state.raw_jobs, state.profile, on_progress=_progress_hook, on_ranked=_persist_if_qualifying
    )
    before = len(ranked)
    ranked = [r for r in ranked if r.fit_score >= MIN_FIT_SCORE]
    print(f"[rank_jobs] {before} ranked -> {len(ranked)} kept (fit_score >= {MIN_FIT_SCORE})")
    return {"ranked_jobs": ranked}


def ats_check_jobs_node(state: PipelineState) -> dict:
    """Runs the deterministic ATS keyword check (agents/ats_checker.py)
    against every job that already cleared MIN_FIT_SCORE, filtering down to
    only jobs at or above ATS_FRONTEND_THRESHOLD into ats_passed_jobs --
    that's what the frontend actually displays, not the full ranked_jobs
    list. A job can be a strong LLM-judged fit (fit_score) but still fail
    here if the resume never uses the literal keywords a real ATS scans
    for; that's the whole point of a separate check. No LLM call unless a
    job has missing keywords (see ats_checker.run_ats_check's own
    short-circuit)."""
    if not state.ranked_jobs:
        return {"ats_passed_jobs": []}

    if _stage_hook:
        _stage_hook(f"ATS-checking {len(state.ranked_jobs)} jobs")

    passed = []
    for r in state.ranked_jobs:
        ats = run_ats_check(r.job, state.profile)
        r.ats_score = ats.ats_score
        r.ats_keywords_found = ats.keywords_found
        r.ats_keywords_missing = ats.keywords_missing
        r.ats_recommendation = ats.recommendation
        if ats.ats_score >= ATS_FRONTEND_THRESHOLD:
            passed.append(r)

    print(
        f"[ats_check] {len(state.ranked_jobs)} ranked -> {len(passed)} kept "
        f"(ats_score >= {ATS_FRONTEND_THRESHOLD})",
        flush=True,
    )
    return {"ats_passed_jobs": passed}


def update_tracker_node(state: PipelineState) -> dict:
    store = ApplicationStore()
    seen = store.seen_keys()
    new_apps = []

    for ranked in state.ranked_jobs:
        key = ranked.job.dedupe_key
        if key in seen:
            continue  # already tracked from a previous run
        app = Application(
            dedupe_key=key,
            job=ranked.job,
            status=ApplicationStatus.FOUND,
            fit_score=ranked.fit_score,
        )
        store.upsert(app)
        new_apps.append(app)

    print(f"[update_tracker] {len(new_apps)} new jobs added to tracker (data/applications.json)")
    return {"new_applications": new_apps}


def draft_profile_updates_node(state: PipelineState) -> dict:
    """Looks at top-ranked jobs' required skills vs. the candidate's profile and
    drafts headline/summary suggestions. SAFE MODE ONLY: this writes suggestions
    to a review file — nothing is posted to LinkedIn/Naukri automatically.
    See agents/automation.py for the opt-in automation path."""
    top_jobs = [r for r in state.ranked_jobs if r.fit_score >= 60][:10]
    if not top_jobs:
        return {"profile_drafts": []}

    all_missing = set()
    for r in top_jobs:
        all_missing.update(r.missing_skills)

    llm = get_llm()
    prompt = f"""Based on these frequently-requested skills across top-matching jobs
that the candidate is currently missing from their profile: {", ".join(sorted(all_missing)) or "none"}

And the candidate's current profile:
Headline: {state.profile.headline}
Summary: {state.profile.summary}

Draft an improved LinkedIn headline (max 220 chars) and a 3-4 sentence summary
that better positions the candidate for these jobs, staying 100% truthful to
their actual background (do not invent skills/experience they don't have —
only rephrase/emphasize/reorder what's real). If a missing skill is something
they could truthfully claim as "familiar with" based on adjacent experience,
you may mention it as a learning-in-progress area, never as an expert skill.

Respond in this exact format:
HEADLINE: <text>
SUMMARY: <text>
REASONING: <why these changes, referencing the specific market signal>
"""
    try:
        resp = llm.invoke(prompt).content
    except Exception as e:
        # Same reasoning as ranker.py's per-job try/except: a transient LLM
        # failure on this cosmetic follow-up step must never wipe out the
        # ranked jobs that already succeeded upstream.
        print(f"[draft_profile_updates] LLM call failed ({e}); skipping profile draft", flush=True)
        return {"profile_drafts": []}

    headline, summary, reasoning = state.profile.headline, state.profile.summary, ""
    for line in resp.splitlines():
        if line.startswith("HEADLINE:"):
            headline = line.split(":", 1)[1].strip()
        elif line.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
        elif line.startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()

    draft = ProfileDraft(
        platform="linkedin",
        headline=headline,
        summary=summary,
        reasoning=reasoning or resp,
        based_on_trend=f"Missing skills across top {len(top_jobs)} matches: {', '.join(sorted(all_missing))}",
    )
    return {"profile_drafts": [draft]}


def build_graph():
    builder = StateGraph(PipelineState)
    builder.add_node("search_jobs", search_jobs_node)
    builder.add_node("rank_jobs", rank_jobs_node)
    builder.add_node("ats_check_jobs", ats_check_jobs_node)
    builder.add_node("update_tracker", update_tracker_node)
    builder.add_node("draft_profile_updates", draft_profile_updates_node)

    builder.add_edge(START, "search_jobs")
    builder.add_edge("search_jobs", "rank_jobs")
    builder.add_edge("rank_jobs", "ats_check_jobs")
    builder.add_edge("ats_check_jobs", "update_tracker")
    builder.add_edge("update_tracker", "draft_profile_updates")
    builder.add_edge("draft_profile_updates", END)

    return builder.compile()
