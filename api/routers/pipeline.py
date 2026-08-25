"""Endpoints for triggering and checking the job-search pipeline run."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from api.pipeline_runner import run_pipeline_job
from api.run_state import get_run_state
from api.schemas import RunResult, RunStatus
from auth import get_current_user
from db import UserRow
from models import RankedJob

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/run", response_model=RunStatus)
def trigger_run(
    background_tasks: BackgroundTasks,
    min_ats_score: int = Query(50, ge=50, le=100),
    current_user: UserRow = Depends(get_current_user),
):
    run_state = get_run_state(current_user.id)
    if run_state.status == "running":
        return RunStatus(status="running", message="A run is already in progress.")
    # current_user.id is resolved here, inside the request's dependency-
    # injection context, and passed as a plain int into the background
    # task — BackgroundTasks.add_task runs after the response is sent,
    # outside that context, so Depends(get_current_user) cannot be
    # re-invoked inside run_pipeline_job itself. This is the one place in
    # the whole multi-user change most likely to leak one user's data into
    # another's if done wrong (e.g. passing the mutable current_user
    # object instead of the id, or forgetting this step).
    background_tasks.add_task(run_pipeline_job, user_id=current_user.id, min_ats_score=min_ats_score)
    return RunStatus(status="running", message="Pipeline started.")


@router.get("/status", response_model=RunStatus)
def get_status(current_user: UserRow = Depends(get_current_user)):
    run_state = get_run_state(current_user.id)
    return RunStatus(status=run_state.status, message=run_state.message)


@router.get("/result", response_model=RunResult)
def get_result(current_user: UserRow = Depends(get_current_user)):
    run_state = get_run_state(current_user.id)
    return RunResult(
        ranked_jobs=run_state.ranked_jobs,
        ats_passed_jobs=run_state.ats_passed_jobs,
        profile_drafts=run_state.profile_drafts,
        new_applications_count=run_state.new_applications_count,
    )


@router.get("/live", response_model=list[RankedJob])
def get_live_jobs(current_user: UserRow = Depends(get_current_user)):
    """Jobs that have qualified so far in the currently-running (or just-
    finished) pipeline — poll this during a run for incremental display
    instead of waiting for /result, which is only populated once the whole
    run completes."""
    return get_run_state(current_user.id).live_jobs
