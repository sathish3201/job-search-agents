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

# Below this, tailoring would need to fabricate skills to close the gap —
# the fabrication guard in agents/ats_checker.py already strips anything
# not truthfully present in the resume, so a low-scoring job would just
# burn an LLM call and come back barely changed. Per user request: only
# offer tailoring for jobs already reasonably close (>= 70).
TAILOR_ATS_MIN = 70


@router.post("/tailor-resume", response_model=TailoredResume | None)
def tailor_resume(request: TailorResumeRequest):
    if run_state.profile is None:
        raise HTTPException(status_code=404, detail="No profile loaded yet — run a search first.")

    # Looks up from the full ranked_jobs list, not just ats_passed_jobs —
    # the dashboard now shows every fit-passed job regardless of ATS
    # score, so a job the user is looking at may not be in the old
    # (now dashboard-unused) ats_passed_jobs list.
    job_match = next(
        (r for r in run_state.ranked_jobs if r.job.dedupe_key == request.dedupe_key), None
    )
    if job_match is None:
        raise HTTPException(status_code=404, detail="Job not found in the current results.")

    if job_match.ats_score < TAILOR_ATS_MIN:
        raise HTTPException(
            status_code=400,
            detail=(
                f"ATS score {job_match.ats_score} is below {TAILOR_ATS_MIN} — tailoring is only "
                "offered for jobs already reasonably close to avoid drafting text that would need "
                "fabricated skills to close the gap."
            ),
        )

    return tailor_resume_for_target(job_match.job, run_state.profile, target_score=ATS_TARGET_SCORE)
