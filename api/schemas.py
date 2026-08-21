"""API-facing response models. Kept separate from models.py (the domain models)
so the HTTP contract can evolve independently of the internal pipeline types."""
from __future__ import annotations

from pydantic import BaseModel

from models import Application, CandidateProfile, ProfileDraft, RankedJob


class RunStatus(BaseModel):
    status: str  # "idle" | "running" | "done" | "error"
    message: str = ""


class RunResult(BaseModel):
    ranked_jobs: list[RankedJob]
    profile_drafts: list[ProfileDraft]
    new_applications_count: int


class ApplicationUpdate(BaseModel):
    status: str
    note: str = ""


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
