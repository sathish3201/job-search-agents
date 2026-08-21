"""Endpoints for the candidate's parsed profile and drafted platform updates."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.run_state import run_state
from api.schemas import ProfileResponse
from models import ProfileDraft

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
def get_profile():
    if run_state.profile is None:
        raise HTTPException(status_code=404, detail="No profile loaded yet — run the pipeline first.")
    return ProfileResponse(profile=run_state.profile)


@router.get("/drafts", response_model=list[ProfileDraft])
def get_profile_drafts():
    return run_state.profile_drafts
