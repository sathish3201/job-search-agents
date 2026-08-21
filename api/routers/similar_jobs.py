"""Semantic search over previously-ranked jobs, backed by the ChromaDB
vector store populated in the background after each pipeline run."""
from __future__ import annotations

from fastapi import APIRouter, Query

from agents.vector_store import VectorStore
from cache import SqliteCache

router = APIRouter(prefix="/api/similar-jobs", tags=["similar-jobs"])

_cache = SqliteCache()
_vector_store = VectorStore(cache=_cache)


@router.get("")
def find_similar_jobs(query: str = Query(..., min_length=1), top_k: int = 5):
    return {"matches": _vector_store.find_similar(query, top_k=top_k)}
