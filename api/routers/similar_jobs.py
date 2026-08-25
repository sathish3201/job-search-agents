"""Semantic search over previously-ranked jobs, backed by the ChromaDB
vector store populated in the background after each pipeline run."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from agents.vector_store import VectorStore
from auth import get_current_user
from cache import SqliteCache
from db import UserRow

router = APIRouter(prefix="/api/similar-jobs", tags=["similar-jobs"])

_cache = SqliteCache()
_vector_store = VectorStore(cache=_cache)


@router.get("")
def find_similar_jobs(
    query: str = Query(..., min_length=1),
    top_k: int = 5,
    current_user: UserRow = Depends(get_current_user),
):
    # Requires login for consistency with every other route (confirmed
    # decision), even though each row's embedded fit_score/pitch is
    # already scoped to one user via the user_id filter in find_similar —
    # there's no "shared, non-personal" version of this endpoint anymore
    # once vector_store.py rows carry per-user fit scores.
    return {"matches": _vector_store.find_similar(query, top_k=top_k, user_id=current_user.id)}
