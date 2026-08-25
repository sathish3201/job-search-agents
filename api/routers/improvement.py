"""Endpoint for the aggregated skill-gap / improvement report."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.improvement import build_improvement_report
from api.run_state import get_run_state
from api.schemas import ImprovementReport
from auth import get_current_user
from db import UserRow

router = APIRouter(prefix="/api/improvement", tags=["improvement"])


@router.get("", response_model=ImprovementReport)
def get_improvement_report(current_user: UserRow = Depends(get_current_user)):
    return build_improvement_report(get_run_state(current_user.id).ranked_jobs)
