"""API-facing response models. Kept separate from models.py (the domain models)
so the HTTP contract can evolve independently of the internal pipeline types."""
from __future__ import annotations

from pydantic import BaseModel

from models import Application, CandidateProfile, ProfileDraft, RankedJob, TailoredResume


class RunStatus(BaseModel):
    status: str  # "idle" | "running" | "done" | "error"
    message: str = ""


class RunResult(BaseModel):
    ranked_jobs: list[RankedJob]
    # Jobs shown on the dashboard: cleared both fit_score and the ATS
    # keyword-match threshold. ranked_jobs is the superset, kept for the
    # improvement report and application tracker.
    ats_passed_jobs: list[RankedJob]
    profile_drafts: list[ProfileDraft]
    new_applications_count: int


class TailorResumeRequest(BaseModel):
    dedupe_key: str  # identifies which job in ats_passed_jobs to tailor for


class ApplicationUpdate(BaseModel):
    status: str
    note: str = ""


class ApplyRequest(BaseModel):
    # Explicit typed confirmation, mirroring agents/automation.py's CLI
    # confirmation-phrase pattern — a click alone isn't enough friction for
    # an action this risky (real submission under the user's name).
    confirmation_phrase: str


class ApplyResponse(BaseModel):
    success: bool
    message: str
    applied_url: str = ""


class SkillGap(BaseModel):
    skill: str
    frequency: int  # how many top jobs listed this as missing
    sample_jobs: list[str]  # job titles that requested it


class ImprovementReport(BaseModel):
    top_missing_skills: list[SkillGap]
    average_fit_score: float
    strongest_matching_skills: list[str]
    summary: str


class ProfileResponse(BaseModel):
    profile: CandidateProfile
