"""Runs the existing LangGraph pipeline (agents/graph.py) and writes the result
into RunState. This is the only place that bridges the FastAPI layer to the
pipeline — routers never call agents/graph.py directly."""
from __future__ import annotations

import os

from agents.graph import PipelineState, build_graph, set_progress_hook
from api.run_state import run_state
from llm import get_llm
from profile_loader import load_resume_text, parse_profile


def run_pipeline_job() -> None:
    """Runs synchronously inside a background thread/task. Never raises past
    this point — errors are captured into RunState.set_error so the API layer
    can report them without crashing the server process."""
    run_state.set_running()
    set_progress_hook(lambda done, total: run_state.set_progress("Ranking jobs", done, total))
    try:
        llm = get_llm()
        resume_text = load_resume_text()
        run_state.set_progress("Parsing candidate profile")
        profile = parse_profile(resume_text, llm)
        run_state.set_progress("Searching job sources")

        graph = build_graph()
        state = PipelineState(
            query=os.getenv("JOB_SEARCH_QUERY", "Full Stack Developer"),
            location=os.getenv("JOB_SEARCH_LOCATION", ""),
            remote_ok=os.getenv("JOB_SEARCH_REMOTE_OK", "true").lower() == "true",
            profile=profile,
        )
        result = graph.invoke(state)

        run_state.set_done(
            ranked_jobs=result["ranked_jobs"],
            profile_drafts=result["profile_drafts"],
            profile=profile,
            new_applications_count=len(result["new_applications"]),
        )
    except Exception as e:
        run_state.set_error(str(e))
