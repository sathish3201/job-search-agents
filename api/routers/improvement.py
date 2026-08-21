"""Endpoint for the aggregated skill-gap / improvement report."""
from __future__ import annotations

from fastapi import APIRouter

from api.improvement import build_improvement_report
from api.run_state import run_state
from api.schemas import ImprovementReport

router = APIRouter(prefix="/api/improvement", tags=["improvement"])


@router.get("", response_model=ImprovementReport)
def get_improvement_report():
    return build_improvement_report(run_state.ranked_jobs)
