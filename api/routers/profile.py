"""Endpoints for the candidate's parsed profile, drafted platform updates,
and resume upload."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile

import resume_store
from agents.resume_vector_store import ResumeVectorStore
from api.run_state import get_run_state
from api.schemas import ProfileResponse
from auth import get_current_user
from db import UserRow
from document_parser import DocumentParseError, extract_text
from llm import get_llm
from models import CandidateProfile, ProfileDraft
from profile_loader import parse_profile

# Only these get a stored original file — a left-pane PDF/DOCX viewer has
# nothing meaningful to render for a .txt/.md upload, so those are skipped
# entirely rather than stored and then having nowhere to show them.
_ORIGINAL_FILE_EXTENSIONS = {"pdf", "docx"}

_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

router = APIRouter(prefix="/api/profile", tags=["profile"])

_resume_vector_store = ResumeVectorStore()


@router.get("", response_model=ProfileResponse)
def get_profile(current_user: UserRow = Depends(get_current_user)):
    run_state = get_run_state(current_user.id)
    if run_state.profile is None:
        raise HTTPException(status_code=404, detail="No profile loaded yet — upload a resume first.")
    return ProfileResponse(profile=run_state.profile)


@router.get("/drafts", response_model=list[ProfileDraft])
def get_profile_drafts(current_user: UserRow = Depends(get_current_user)):
    return get_run_state(current_user.id).profile_drafts


@router.post("/upload", response_model=ProfileResponse)
def upload_resume(file: UploadFile, current_user: UserRow = Depends(get_current_user)):
    """Accepts a PDF/DOCX/TXT/MD resume, extracts its text, LLM-parses it
    into a CandidateProfile, stores that on this user's RunState (so the
    next pipeline run uses it — see api/pipeline_runner.py), upserts a
    resume vector for this user, and returns the parsed profile
    immediately so the frontend can show "here's what we extracted"
    without a second round-trip."""
    content = file.file.read()
    try:
        resume_text = extract_text(file.filename or "resume", content)
    except DocumentParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    llm = get_llm()
    profile: CandidateProfile = parse_profile(resume_text, llm, user_id=current_user.id)

    ext = resume_store.extension_of(file.filename or "")
    if ext in _ORIGINAL_FILE_EXTENSIONS:
        stored_ext = resume_store.save_original(current_user.id, file.filename, content)
        profile.original_file_ext = stored_ext
        profile.original_file_stored_at = datetime.now(timezone.utc).isoformat()

    run_state = get_run_state(current_user.id)
    run_state.profile = profile
    # Not part of a completed pipeline run yet, so no set_done() call here
    # (that would incorrectly mark status "done" with no ranked jobs) — but
    # the profile itself is saved on the next real pipeline run's set_done(),
    # and pipeline_runner.py checks run_state.profile before falling back
    # to RESUME_MD_PATH, so this takes effect immediately for the next run
    # regardless.

    try:
        _resume_vector_store.upsert_resume(current_user.id, current_user.email, profile)
    except Exception as e:
        # Resume-vector indexing is a nice-to-have (candidate-similarity
        # feature, not yet exposed via any endpoint) — a failure here must
        # never block the user from seeing their successfully-parsed
        # profile.
        print(f"[profile.upload] resume vector upsert failed: {e}", flush=True)

    return ProfileResponse(profile=profile)


@router.get("/original-file")
def get_original_file(current_user: UserRow = Depends(get_current_user)):
    """Serves this user's stored original resume bytes — no user_id in the
    URL/query, resolved from the auth token only, so one user can never
    fetch another's file by guessing an id."""
    result = resume_store.load_original(current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="No original file on record for this account.")
    content, ext = result
    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    return Response(content=content, media_type=media_type)
