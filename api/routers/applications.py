"""Endpoints for viewing and updating the application tracker, including
the human-approved apply/discard actions (see api/schemas.py's
ApplyRequest and agents/apply_playwright.py for why this needs a typed
confirmation phrase and only works when this API is running locally)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import ApplicationUpdate, ApplyRequest, ApplyResponse
from models import Application, ApplicationStatus
from store import ApplicationStore

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("", response_model=list[Application])
def list_applications():
    store = ApplicationStore()
    return sorted(store.all(), key=lambda a: a.last_updated, reverse=True)


@router.patch("/{dedupe_key:path}", response_model=Application)
def update_application(dedupe_key: str, update: ApplicationUpdate):
    store = ApplicationStore()
    app = store.get(dedupe_key)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        status = ApplicationStatus(update.status)
    except ValueError:
        valid = [s.value for s in ApplicationStatus]
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid}")

    app.record_event(status, update.note)
    store.upsert(app)
    return app


@router.post("/{dedupe_key:path}/apply", response_model=ApplyResponse)
def apply_to_application(dedupe_key: str, request: ApplyRequest):
    """Submits (or opens for manual completion) a real job application via
    a local headed Playwright browser. HIGH RISK — see
    agents/apply_playwright.py's module docstring. This endpoint will hang
    waiting for terminal input (the confirmation/CAPTCHA-clear prompts) if
    called against a headless/unattended deployment — it must only be
    called against this API running locally, where a human is present to
    interact with the browser and this process's stdin."""
    store = ApplicationStore()
    app = store.get(dedupe_key)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")

    from agents.apply_playwright import apply_to_job

    try:
        result = apply_to_job(app.job.url, request.confirmation_phrase)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result.success:
        app.record_event(ApplicationStatus.APPLIED, note=result.message)
        store.upsert(app)

    return ApplyResponse(success=result.success, message=result.message, applied_url=result.applied_url)


@router.post("/{dedupe_key:path}/discard", response_model=Application)
def discard_application(dedupe_key: str):
    """User chose not to apply — marks discarded_by_user so the dashboard
    can hide it, without conflating this with a recruiter-side rejection
    (ApplicationStatus.REJECTED means something different)."""
    store = ApplicationStore()
    app = store.get(dedupe_key)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")

    app.discarded_by_user = True
    store.upsert(app)
    return app
