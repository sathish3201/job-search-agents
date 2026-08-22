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

import functools
import operator
import time
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
REMOTIVE_SEARCH_LIMIT = 9  # keep the fastest source capped so a run has fewer jobs to rank overall
DEFAULT_SEARCH_LIMIT = 15  # other sources (Apify, Adzuna, etc.) keep the previous cap

# Anything slower than this gets an explicit "still running" style timing
# line — the whole point is that neither the user nor the operator should
# ever wonder "is this stuck?" for more than a few seconds without a log
# line telling them what's in flight and how long it's taken so far.
SLOW_CALL_THRESHOLD_SECONDS = 3.0


def _timed(label: str):
    """Wraps a call, printing how long it took, AND pushing the same status
    through _stage_hook so the frontend (which polls run_state.progress,
    fed by this hook) shows live per-step timing too, not just the CLI/
    server log. Anything over SLOW_CALL_THRESHOLD_SECONDS gets flagged
    explicitly so a slow-but-working step (LLM tunnel latency, an Apify
    actor run, a Playwright render) is visibly distinguished from a hang,
    for both the person watching the terminal and the person watching the
    dashboard."""
    start = time.time()
    print(f"[timing] {label}: starting...", flush=True)
    if _stage_hook:
        _stage_hook(f"{label}: running...")

    def _done(extra: str = ""):
        elapsed = time.time() - start
        tag = "SLOW" if elapsed > SLOW_CALL_THRESHOLD_SECONDS else "ok"
        suffix = f" — {extra}" if extra else ""
        print(f"[timing] {label}: done in {elapsed:.1f}s [{tag}]{suffix}", flush=True)
        if _stage_hook:
            slow_note = " (slow)" if tag == "SLOW" else ""
            _stage_hook(f"{label}: done in {elapsed:.1f}s{slow_note}{suffix}")
        return elapsed

    return _done


def timed_stage(label: str):
    """Decorator for graph nodes: logs start/end + elapsed time around the
    whole node, on top of whatever the node already prints internally."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state):
            done = _timed(f"node:{label}")
            try:
                return fn(state)
            finally:
                done()

        return wrapper

    return decorator

# Optional hooks, set by api/pipeline_runner.py before invoking the graph, so
# individual nodes can report live progress. Not LangGraph state fields
# because callables aren't pydantic-serializable; module-level is the
# simplest wiring for a single-process deployment.
#   _progress_hook(done, total) -> None      -- per-job ranking progress
#   _stage_hook(stage_name) -> None          -- coarse "which node is running"
#   _live_job_hook(RankedJob) -> None        -- one job fully qualified (fit
#                                                + ATS both checked), for
#                                                incremental frontend display
_progress_hook = None
_stage_hook = None
_live_job_hook = None


def set_progress_hook(hook) -> None:
    global _progress_hook
    _progress_hook = hook


def set_stage_hook(hook) -> None:
    global _stage_hook
    _stage_hook = hook


def set_live_job_hook(hook) -> None:
    global _live_job_hook
    _live_job_hook = hook


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


@timed_stage("search_jobs")
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
        # Remotive capped lower than other sources: it's the fastest, always-on
        # source with no rate/cost concern, but a smaller cap here means fewer
        # jobs overall to rank per run, which is the actual lever for making a
        # full run finish faster against the slow LLM ranking step.
        limit = REMOTIVE_SEARCH_LIMIT if src.name == "remotive" else DEFAULT_SEARCH_LIMIT
        done = _timed(f"source:{src.name}")
        jobs = src.search(state.query, state.location, state.remote_ok, limit=limit)
        done(f"{len(jobs)} results")
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


def _persist_if_qualifying(ranked: RankedJob, profile: CandidateProfile) -> None:
    """Called from LLMRanker's on_ranked hook the instant one job finishes
    LLM scoring (not after the whole batch). Does three things per
    qualifying job, all live rather than batched at the end:
      1. Runs the ATS keyword check for this one job immediately, so
         ats_score is available right away instead of waiting for a
         separate batch node after every job in the run has been ranked.
      2. Persists to the application tracker (disk-backed) — survives a
         mid-run process restart, see the original comment this replaced.
      3. Fires _live_job_hook so the API layer can stream this one job to
         the frontend immediately, rather than the UI showing nothing
         until the entire run finishes (ChatGPT-style incremental display,
         not a single batched reveal at the end)."""
    if ranked.fit_score < MIN_FIT_SCORE:
        return

    ats = run_ats_check(ranked.job, profile)
    ranked.ats_score = ats.ats_score
    ranked.ats_keywords_found = ats.keywords_found
    ranked.ats_keywords_missing = ats.keywords_missing
    ranked.ats_recommendation = ats.recommendation

    store = ApplicationStore()
    key = ranked.job.dedupe_key
    already_seen = key in store.seen_keys()
    if not already_seen:
        store.upsert(
            Application(
                dedupe_key=key,
                job=ranked.job,
                status=ApplicationStatus.FOUND,
                fit_score=ranked.fit_score,
            )
        )

    if ats.ats_score >= ATS_FRONTEND_THRESHOLD and _live_job_hook:
        _live_job_hook(ranked)


@timed_stage("rank_jobs")
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

    rank_start = time.time()

    def on_ranked(ranked_job: RankedJob) -> None:
        elapsed = time.time() - rank_start
        tag = "SLOW" if elapsed > SLOW_CALL_THRESHOLD_SECONDS else "ok"
        title = ranked_job.job.title[:50]
        print(
            f"[timing] rank_job: {title!r} scored "
            f"fit={ranked_job.fit_score} at t={elapsed:.1f}s [{tag}]",
            flush=True,
        )
        if _stage_hook:
            slow_note = " (slow LLM call)" if tag == "SLOW" else ""
            _stage_hook(f"Ranked {title!r} — fit={ranked_job.fit_score}{slow_note}")
        _persist_if_qualifying(ranked_job, state.profile)

    ranked = _ranker.rank_all(
        state.raw_jobs, state.profile, on_progress=_progress_hook, on_ranked=on_ranked
    )
    before = len(ranked)
    ranked = [r for r in ranked if r.fit_score >= MIN_FIT_SCORE]
    print(f"[rank_jobs] {before} ranked -> {len(ranked)} kept (fit_score >= {MIN_FIT_SCORE})")
    return {"ranked_jobs": ranked}


@timed_stage("ats_check_jobs")
def ats_check_jobs_node(state: PipelineState) -> dict:
    """Filters ranked_jobs down to ats_passed_jobs. The ATS check itself
    already ran per-job inside _persist_if_qualifying (streamed live to the
    frontend as each job was scored), so this node just re-derives the same
    filter from the ats_score already stamped onto each RankedJob — no
    redundant LLM/ATS work here, just the final authoritative list the rest
    of the graph (update_tracker, draft_profile_updates) reads from."""
    if not state.ranked_jobs:
        return {"ats_passed_jobs": []}

    if _stage_hook:
        _stage_hook(f"Finalizing {len(state.ranked_jobs)} ranked jobs")

    passed = [r for r in state.ranked_jobs if r.ats_score >= ATS_FRONTEND_THRESHOLD]
    print(
        f"[ats_check] {len(state.ranked_jobs)} ranked -> {len(passed)} kept "
        f"(ats_score >= {ATS_FRONTEND_THRESHOLD})",
        flush=True,
    )
    return {"ats_passed_jobs": passed}


@timed_stage("update_tracker")
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


@timed_stage("draft_profile_updates")
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
