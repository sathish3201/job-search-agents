"""Runs the existing LangGraph pipeline (agents/graph.py) and writes the
result into one user's RunState. This is the only place that bridges the
FastAPI layer to the pipeline — routers never call agents/graph.py
directly.

user_id is a plain int/str argument here, not re-resolved from a request
inside this function — see api/routers/pipeline.py's trigger_run for why:
FastAPI's BackgroundTasks.add_task runs after the response is sent,
outside the original request's dependency-injection context, so
Depends(get_current_user) cannot be re-invoked here. The route handler
resolves current_user while the request context still exists, then passes
current_user.id in as a plain value — the only safe way to carry identity
across that boundary. Getting this wrong (re-deriving "who is this" inside
the background task, or accidentally capturing a mutable user object) is
the single most likely way one user's pipeline run could write into
another user's dashboard."""
from __future__ import annotations

import os

from agents.graph import (
    PipelineState,
    build_graph,
    set_live_job_hook,
    set_progress_hook,
    set_stage_hook,
)
from api.run_state import get_run_state
from api.vector_indexer import index_ranked_jobs_job
from llm import get_llm
from profile_loader import load_resume_text, parse_profile


def run_pipeline_job(user_id: int | str = "local", min_ats_score: int = 50) -> None:
    """Runs synchronously inside a background thread/task. Never raises past
    this point — errors are captured into RunState.set_error so the API layer
    can report them without crashing the server process."""
    run_state = get_run_state(user_id)
    run_state.set_running()
    set_progress_hook(lambda done, total: run_state.set_progress("Ranking jobs", done, total))
    set_stage_hook(lambda stage: run_state.set_progress(stage))
    set_live_job_hook(run_state.add_live_job)
    try:
        # A resume already uploaded and parsed via POST /api/profile/upload
        # takes priority — there's no longer one global resume once
        # multiple users exist. load_resume_text()/RESUME_MD_PATH remains
        # only as a local-dev fallback (main.py's CLI path, and the "local"
        # default user_id) for running this without ever hitting the
        # upload endpoint.
        if run_state.profile is not None:
            profile = run_state.profile
        else:
            llm = get_llm()
            resume_text = load_resume_text()
            run_state.set_progress("Loading candidate profile (cached if unchanged since last run)")
            profile = parse_profile(resume_text, llm, user_id=user_id)

        graph = build_graph()
        state = PipelineState(
            query=os.getenv("JOB_SEARCH_QUERY", "Full Stack Developer"),
            location=os.getenv("JOB_SEARCH_LOCATION", ""),
            remote_ok=os.getenv("JOB_SEARCH_REMOTE_OK", "true").lower() == "true",
            profile=profile,
            user_id=user_id,
            min_ats_score=min_ats_score,
        )
        result = graph.invoke(state)

        run_state.set_done(
            ranked_jobs=result["ranked_jobs"],
            ats_passed_jobs=result["ats_passed_jobs"],
            profile_drafts=result["profile_drafts"],
            profile=profile,
            new_applications_count=len(result["new_applications"]),
        )

        # Runs after set_done(), so the frontend already sees "done" and the
        # ranked results — this is best-effort follow-up work, not something
        # the user waits on. index_ranked_jobs_job never raises (see its
        # own try/except), so it can't turn a successful run into an error.
        index_ranked_jobs_job(result["ranked_jobs"], user_id=user_id)
    except Exception as e:
        run_state.set_error(str(e))
