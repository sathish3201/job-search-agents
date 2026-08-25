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

    # ATS (keyword-matching) score — a different signal from fit_score:
    # fit_score is the LLM's judgment of overall fit, ats_score is "would a
    # literal keyword-scanning ATS pass this resume through" for this job.
    # See agents/ats_checker.py.
    ats_score: int = Field(default=0, ge=0, le=100)
    ats_keywords_found: list[str] = Field(default_factory=list)
    ats_keywords_missing: list[str] = Field(default_factory=list)
    ats_recommendation: str = ""


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
    ats_score: Optional[int] = None
    applied_date: Optional[date] = None
    last_updated: date = Field(default_factory=date.today)
    notes: list[str] = Field(default_factory=list)
    # Set when the user rejects a job from the review dashboard — REJECTED
    # already existed for recruiter-side rejection, so this is a separate
    # boolean rather than overloading that enum value with two different
    # meanings (recruiter said no vs. user chose not to apply).
    discarded_by_user: bool = False

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
    # Pointer to the original uploaded file stored via resume_store.py, not
    # the bytes themselves — RunState snapshots this profile to JSON on
    # every pipeline run (see api/run_state.py), so inlining file bytes
    # here would bloat every snapshot. None for txt/md uploads (nothing to
    # show in a PDF viewer) or profiles predating this field.
    original_file_ext: Optional[str] = None
    original_file_stored_at: Optional[str] = None


class ProfileDraft(BaseModel):
    """LLM-drafted profile update, for human review before posting anywhere."""

    platform: str  # "linkedin" | "naukri"
    headline: str
    summary: str
    reasoning: str
    based_on_trend: str = ""  # what recruiter-action pattern triggered this suggestion


class TailoredResume(BaseModel):
    """An ATS-optimized resume summary/headline draft for a specific target
    job title, iteratively tuned by inserting truthful missing keywords
    until the deterministic ATS score clears the target threshold (see
    agents/ats_checker.py's iterate-to-target loop). Never a full resume
    rewrite — only the summary/headline, since that's what's safe to draft
    without risking fabricated experience claims elsewhere in the resume."""

    target_title: str
    based_on_job_url: str
    original_ats_score: int
    final_ats_score: int
    tailored_headline: str
    tailored_summary: str
    keywords_added: list[str] = Field(default_factory=list)
    reasoning: str
    # Full duplicate of the candidate's original resume text, with only the
    # headline/summary portion swapped for the tailored version — every
    # other section (experience, projects, skills, education) is copied
    # byte-identical from resume_raw_text. Built by
    # agents/ats_checker.build_full_tailored_resume(). See that function's
    # docstring for the splice-vs-fallback strategy.
    full_tailored_resume_text: str = ""


class ResumeSection(BaseModel):
    """One editable unit of a structured resume draft — the granularity
    the interactive tailoring agent's tools operate on (agents/tailor_agent.py)
    and what the frontend's click-to-target references by id. Deliberately
    more granular than CandidateProfile's headline/summary-only model,
    since bullet-level editing is in scope for the chat-driven tailoring
    dialog (unlike TailoredResume above, which only ever touches
    headline/summary)."""

    section_id: str  # stable id, e.g. "experience-0-bullet-2", "summary", "skill-3"
    section_type: str  # "headline" | "summary" | "experience_heading" | "experience_bullet" | "skill" | "education" | "other"
    text: str
    order: int


class TailoredDraft(BaseModel):
    """Structured, editable state of one in-progress interactive tailoring
    session (see agents/draft_builder.py, agents/tailor_agent.py). This —
    not raw text — is what the chat agent's tools mutate and what
    agents/resume_renderer.py turns into PDF/DOCX bytes."""

    dedupe_key: str
    target_title: str
    sections: list[ResumeSection]
    ats_score: int
    keywords_found: list[str] = Field(default_factory=list)
    keywords_missing: list[str] = Field(default_factory=list)
    # Bumped on every accepted edit — the frontend re-fetches the rendered
    # preview PDF only when this changes, rather than on every poll.
    version: int = 0


class ChatMessage(BaseModel):
    role: str  # "user" | "agent"
    content: str
    target_section_id: Optional[str] = None  # set when this message came from a click-to-target


class TailorSession(BaseModel):
    """In-memory-only session state for one (user, job) interactive
    tailoring conversation — see api/routers/tailor_chat.py's process-wide
    registry, mirroring api/run_state.py's get_run_state() pattern. Not
    persisted to disk: losing an in-progress edit session on a server
    restart is an acceptable, low-frequency inconvenience."""

    session_id: str
    user_id: str  # str(user_id) — see tailor_chat.py for why this is normalized to str
    dedupe_key: str
    draft: TailoredDraft
    messages: list[ChatMessage] = Field(default_factory=list)
