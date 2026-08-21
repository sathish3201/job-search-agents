"""The main LangGraph pipeline wiring together search -> shortlist -> rank -> track -> profile-draft.

Flow:
    search_jobs -> shortlist_jobs (fast embedding filter) -> rank_jobs (cached LLM
    scoring, parallel per job) -> update_tracker -> draft_profile_updates -> END

Each node delegates to a dedicated module (embedding_filter.py, ranker.py) rather
than containing the logic inline — the graph's only job is orchestration.
"""
from __future__ import annotations

import operator
from typing import Annotated

from pydantic import BaseModel

from langgraph.graph import StateGraph, START, END

from agents.embedding_filter import EmbeddingFilter
from agents.ranker import LLMRanker
from cache import SqliteCache
from llm import get_llm
from models import Application, ApplicationStatus, CandidateProfile, JobListing, ProfileDraft, RankedJob
from sources import active_sources
from store import ApplicationStore

_cache = SqliteCache()
_embedding_filter = EmbeddingFilter(cache=_cache)
_ranker = LLMRanker(cache=_cache)

MIN_FIT_SCORE = 70  # jobs scoring below this are dropped entirely — not shown, not tracked

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
    shortlisted_jobs: list[JobListing] = []
    ranked_jobs: Annotated[list[RankedJob], operator.add] = []
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


def shortlist_jobs_node(state: PipelineState) -> dict:
    """Fast embedding pass (fastembed, local ONNX, no LLM call) narrows raw_jobs
    down to the top matches by cosine similarity before the slow LLM ranker runs.
    This is the main speed lever: LLM calls only happen for the shortlist, not
    every raw result."""
    print(f"[shortlist_jobs] starting with {len(state.raw_jobs)} raw jobs", flush=True)
    if _stage_hook:
        _stage_hook(f"Shortlisting {len(state.raw_jobs)} jobs by relevance")
    if not state.raw_jobs:
        return {"shortlisted_jobs": []}

    shortlisted = _embedding_filter.shortlist(state.raw_jobs, state.profile, top_k=8)
    print(f"[shortlist_jobs] {len(state.raw_jobs)} raw -> {len(shortlisted)} shortlisted for LLM ranking", flush=True)
    return {"shortlisted_jobs": shortlisted}


def rank_jobs_node(state: PipelineState) -> dict:
    """LLM-scores only the shortlisted jobs, via LLMRanker (agents/ranker.py),
    which itself is cached per (job, resume) hash in SQLite — a repeat run over
    the same jobs costs zero LLM calls. Jobs scoring below MIN_FIT_SCORE are
    dropped here so they never reach the tracker, the report, or the UI."""
    if not state.shortlisted_jobs:
        return {"ranked_jobs": []}

    if _stage_hook:
        _stage_hook(f"Ranking {len(state.shortlisted_jobs)} jobs")
    ranked = _ranker.rank_all(state.shortlisted_jobs, state.profile, on_progress=_progress_hook)
    before = len(ranked)
    ranked = [r for r in ranked if r.fit_score >= MIN_FIT_SCORE]
    print(f"[rank_jobs] {before} ranked -> {len(ranked)} kept (fit_score >= {MIN_FIT_SCORE})")
    return {"ranked_jobs": ranked}


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
    resp = llm.invoke(prompt).content

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
    builder.add_node("shortlist_jobs", shortlist_jobs_node)
    builder.add_node("rank_jobs", rank_jobs_node)
    builder.add_node("update_tracker", update_tracker_node)
    builder.add_node("draft_profile_updates", draft_profile_updates_node)

    builder.add_edge(START, "search_jobs")
    builder.add_edge("search_jobs", "shortlist_jobs")
    builder.add_edge("shortlist_jobs", "rank_jobs")
    builder.add_edge("rank_jobs", "update_tracker")
    builder.add_edge("update_tracker", "draft_profile_updates")
    builder.add_edge("draft_profile_updates", END)

    return builder.compile()
