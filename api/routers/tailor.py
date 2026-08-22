"""On-demand resume tailoring: POST one job's dedupe_key, get back an
ATS-tuned headline/summary draft for that specific job. Triggered by the
user clicking "Tailor Resume" on a job card — not run automatically for
every job on every search (see agents/graph.py's module docstring for why)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agents.ats_checker import tailor_resume_for_target
from api.run_state import run_state
from api.schemas import TailorResumeRequest
from models import TailoredResume

router = APIRouter(prefix="/api/jobs", tags=["tailor"])

ATS_TARGET_SCORE = 90


@router.post("/tailor-resume", response_model=TailoredResume | None)
def tailor_resume(request: TailorResumeRequest):
    if run_state.profile is None:
        raise HTTPException(status_code=404, detail="No profile loaded yet — run a search first.")

    job_match = next(
        (r for r in run_state.ats_passed_jobs if r.job.dedupe_key == request.dedupe_key), None
    )
    if job_match is None:
        raise HTTPException(status_code=404, detail="Job not found in the current results.")

    return tailor_resume_for_target(job_match.job, run_state.profile, target_score=ATS_TARGET_SCORE)
