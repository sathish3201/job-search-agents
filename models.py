"""Shared pydantic models for jobs, candidate profile, and application tracking."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobListing(BaseModel):
    """A single normalized job posting, regardless of which source it came from."""

    source: str  # "adzuna" | "jsearch" | "remotive" | ...
    external_id: str  # source's own id, used for de-duplication
    title: str
    company: str
    location: str = ""
    remote: bool = False
    description: str = ""
    url: str
    posted_date: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None

    @property
    def dedupe_key(self) -> str:
        return f"{self.source}:{self.external_id}"


class RankedJob(BaseModel):
    """A JobListing plus the ranking agent's assessment."""

    job: JobListing
    fit_score: int = Field(ge=0, le=100)
    reasoning: str
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    tailored_pitch: str = ""  # one-paragraph "why me" for this specific job


class ApplicationStatus(str, Enum):
    FOUND = "found"
    APPLIED = "applied"
    VIEWED_BY_RECRUITER = "viewed_by_recruiter"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"


class Application(BaseModel):
    """One tracked application, evolving over time via recorded recruiter actions."""

    dedupe_key: str
    job: JobListing
    status: ApplicationStatus = ApplicationStatus.FOUND
    fit_score: Optional[int] = None
    applied_date: Optional[date] = None
    last_updated: date = Field(default_factory=date.today)
    notes: list[str] = Field(default_factory=list)

    def record_event(self, status: ApplicationStatus, note: str = "") -> None:
        self.status = status
        self.last_updated = date.today()
        if note:
            self.notes.append(f"{self.last_updated.isoformat()}: {note}")


class CandidateProfile(BaseModel):
    """Parsed-down view of the candidate, used by the ranking + profile-drafting agents."""

    name: str
    headline: str
    summary: str
    skills: list[str]
    years_experience: float
    target_roles: list[str]
    resume_raw_text: str


class ProfileDraft(BaseModel):
    """LLM-drafted profile update, for human review before posting anywhere."""

    platform: str  # "linkedin" | "naukri"
    headline: str
    summary: str
    reasoning: str
    based_on_trend: str = ""  # what recruiter-action pattern triggered this suggestion
