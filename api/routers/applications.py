"""Endpoints for viewing and updating the application tracker."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import ApplicationUpdate
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
