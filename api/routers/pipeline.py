"""Endpoints for triggering and checking the job-search pipeline run."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from api.pipeline_runner import run_pipeline_job
from api.run_state import run_state
from api.schemas import RunResult, RunStatus

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/run", response_model=RunStatus)
def trigger_run(background_tasks: BackgroundTasks):
    if run_state.status == "running":
        return RunStatus(status="running", message="A run is already in progress.")
    background_tasks.add_task(run_pipeline_job)
    return RunStatus(status="running", message="Pipeline started.")


@router.get("/status", response_model=RunStatus)
def get_status():
    return RunStatus(status=run_state.status, message=run_state.message)


@router.get("/result", response_model=RunResult)
def get_result():
    return RunResult(
        ranked_jobs=run_state.ranked_jobs,
        profile_drafts=run_state.profile_drafts,
        new_applications_count=run_state.new_applications_count,
    )
