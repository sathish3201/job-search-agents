"""Session start/message/preview/export endpoints for the interactive,
chat-driven resume-tailoring dialog. New infrastructure, separate from
api/routers/tailor.py's existing headline/summary-only endpoints (which
stay as-is — see agents/draft_builder.py, which reuses
tailor_resume_for_target as the headline/summary seed for a session's
initial draft, not a replacement)."""
from __future__ import annotations

import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response

import resume_store
from agents.draft_builder import build_initial_draft
from agents.resume_renderer import render_docx, render_pdf
from agents.tailor_agent import run_turn
from api.run_state import get_run_state
from api.schemas import (
    ExportResumeRequest,
    StartTailorSessionRequest,
    StartTailorSessionResponse,
    TailorChatRequest,
    TailorChatResponse,
)
from auth import get_current_user
from db import UserRow
from models import ChatMessage, TailorSession

router = APIRouter(prefix="/api/tailor-chat", tags=["tailor-chat"])

# Process-wide registry, one TailorSession per session_id — same shape as
# api/run_state.py's get_run_state() registry. Not persisted to disk (see
# plan's scope decision #3): losing an in-progress edit session on a
# server restart is an acceptable, low-frequency inconvenience.
_sessions: dict[str, TailorSession] = {}
_sessions_lock = threading.Lock()

_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _get_owned_session(session_id: str, user_id) -> TailorSession:
    """Ownership check: session_id alone isn't enough to prove access —
    without this, one user could reach another's session by guessing/
    reusing a session_id string. No user_id in the URL for this same
    reason (matches api/routers/profile.py's /original-file pattern)."""
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None or session.user_id != str(user_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@router.post("/start", response_model=StartTailorSessionResponse)
def start_session(request: StartTailorSessionRequest, current_user: UserRow = Depends(get_current_user)):
    run_state = get_run_state(current_user.id)
    if run_state.profile is None:
        raise HTTPException(status_code=404, detail="No profile loaded yet — upload a resume first.")

    job_match = next(
        (r for r in run_state.ranked_jobs if r.job.dedupe_key == request.dedupe_key), None
    )
    if job_match is None:
        raise HTTPException(status_code=404, detail="Job not found in the current results.")

    draft = build_initial_draft(job_match.job, run_state.profile)
    session_id = uuid.uuid4().hex
    session = TailorSession(session_id=session_id, user_id=str(current_user.id), dedupe_key=request.dedupe_key, draft=draft)
    with _sessions_lock:
        _sessions[session_id] = session

    original_file_url = "/api/profile/original-file"
    return StartTailorSessionResponse(
        session_id=session_id, draft=draft, messages=[], original_file_url=original_file_url
    )


@router.post("/message", response_model=TailorChatResponse)
def send_message(request: TailorChatRequest, current_user: UserRow = Depends(get_current_user)):
    session = _get_owned_session(request.session_id, current_user.id)
    run_state = get_run_state(current_user.id)

    job_match = next(
        (r for r in run_state.ranked_jobs if r.job.dedupe_key == session.dedupe_key), None
    )
    job_description = job_match.job.description if job_match else session.draft.target_title
    resume_raw_text = run_state.profile.resume_raw_text if run_state.profile else ""

    reply, updated_draft = run_turn(
        session.draft, session.messages, request.message, request.target_section_id,
        job_description, resume_raw_text,
    )
    session.messages.append(ChatMessage(role="user", content=request.message, target_section_id=request.target_section_id))
    session.messages.append(ChatMessage(role="agent", content=reply))
    session.draft = updated_draft
    session.draft.version += 1

    tailored_pdf_url = f"/api/tailor-chat/preview-pdf/{session.session_id}?v={session.draft.version}"
    return TailorChatResponse(reply=reply, draft=session.draft, tailored_pdf_url=tailored_pdf_url)


@router.get("/preview-pdf/{session_id}")
def get_preview_pdf(session_id: str, current_user: UserRow = Depends(get_current_user)):
    session = _get_owned_session(session_id, current_user.id)
    run_state = get_run_state(current_user.id)
    candidate_name = run_state.profile.name if run_state.profile else "Candidate"
    pdf_bytes = render_pdf(session.draft, candidate_name)
    return Response(content=pdf_bytes, media_type="application/pdf")


@router.post("/export")
def export_resume(request: ExportResumeRequest, current_user: UserRow = Depends(get_current_user)):
    session = _get_owned_session(request.session_id, current_user.id)
    run_state = get_run_state(current_user.id)
    candidate_name = run_state.profile.name if run_state.profile else "Candidate"

    if request.format not in _MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="format must be 'pdf' or 'docx'.")

    content = render_pdf(session.draft, candidate_name) if request.format == "pdf" else render_docx(session.draft, candidate_name)
    resume_store.save_generated(current_user.id, session.dedupe_key, request.format, content)

    filename = f"tailored-resume-{session.draft.target_title.replace(' ', '-').lower()}.{request.format}"
    return Response(
        content=content,
        media_type=_MEDIA_TYPES[request.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{session_id}")
def delete_session(session_id: str, current_user: UserRow = Depends(get_current_user)):
    _get_owned_session(session_id, current_user.id)  # raises 404 if not owned
    with _sessions_lock:
        _sessions.pop(session_id, None)
    return {"deleted": True}
