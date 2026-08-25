"""API-facing response models. Kept separate from models.py (the domain models)
so the HTTP contract can evolve independently of the internal pipeline types."""
from __future__ import annotations

from pydantic import BaseModel

from models import (
    Application,
    CandidateProfile,
    ChatMessage,
    ProfileDraft,
    RankedJob,
    TailoredDraft,
    TailoredResume,
)


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


class RescoreRequest(BaseModel):
    # Live re-score of user-edited headline/summary text against the same
    # job's ATS keywords — lets the user tweak wording in the Tailored
    # Resume panel and see the score change before downloading, without
    # a second LLM call (see agents/ats_checker.py's check_keyword_presence,
    # reused here as-is — deterministic, so this is fast enough for
    # near-keystroke-speed re-scoring).
    dedupe_key: str
    headline: str
    summary: str


class RescoreResponse(BaseModel):
    ats_score: int
    keywords_found: list[str]
    keywords_missing: list[str]


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


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserPublic(BaseModel):
    id: int
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class StartTailorSessionRequest(BaseModel):
    dedupe_key: str


class StartTailorSessionResponse(BaseModel):
    session_id: str
    draft: TailoredDraft
    messages: list[ChatMessage]
    # 404s client-side if the user has no original file on record (txt/md
    # upload, or no upload at all) — the frontend left pane falls back
    # gracefully rather than this field being conditionally omitted.
    original_file_url: str


class TailorChatRequest(BaseModel):
    session_id: str
    message: str
    target_section_id: str | None = None


class TailorChatResponse(BaseModel):
    reply: str
    draft: TailoredDraft
    tailored_pdf_url: str


class ExportResumeRequest(BaseModel):
    session_id: str
    format: str  # "pdf" | "docx"
