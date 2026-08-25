"""ChromaDB-backed vector store of ranked jobs, for semantic retrieval of
past agent context (e.g. "what similar jobs have I already ranked well?").

Design constraints that shape this file:
- fastembed's ONNX inference is CPU-only and slow enough to hang a request
  on Render's free tier (this bit us once already — see agents/graph.py's
  docstring on why the old shortlist step was removed). So embedding here
  NEVER runs inside a user-facing request; it's only ever called from
  api/vector_indexer.py's background task, after a pipeline run has
  already returned its result to the user.
- Before embedding a job, skip it if its dedupe_key is already in the
  SQLite application tracker's "already embedded" set (tracked in the same
  SqliteCache used elsewhere) — avoids redundant embedding work on repeat
  runs over the same jobs, mirroring the ranking cache's behavior.
"""
from __future__ import annotations

import os

from cache import SqliteCache
from models import RankedJob

_COLLECTION_NAME = "ranked_jobs"
_CHROMA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "chroma")
_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


class VectorStore:
    def __init__(self, cache: SqliteCache | None = None):
        self._cache = cache or SqliteCache()
        self._client = None
        self._collection = None
        self._embedder = None

    def _ensure_ready(self) -> None:
        """Lazy-init: importing/constructing fastembed and chromadb is itself
        not free, so defer it until the first real background-task call
        rather than paying that cost at module import (server startup)."""
        if self._collection is not None:
            return

        import chromadb

        self._client = chromadb.PersistentClient(path=_CHROMA_PATH)
        self._collection = self._client.get_or_create_collection(_COLLECTION_NAME)

        from fastembed import TextEmbedding

        self._embedder = TextEmbedding(model_name=_EMBED_MODEL)

    def _already_indexed(self, dedupe_key: str, user_id: int | str) -> bool:
        return self._cache.get(f"vec_indexed:{user_id}:{dedupe_key}") is not None

    def _mark_indexed(self, dedupe_key: str, user_id: int | str) -> None:
        self._cache.set(f"vec_indexed:{user_id}:{dedupe_key}", {"indexed": True})

    def index_ranked_jobs(self, ranked_jobs: list[RankedJob], user_id: int | str = "local") -> int:
        """Embeds and stores any ranked jobs not already indexed. Returns the
        count actually embedded (as opposed to skipped-as-duplicate).

        Confirmed real bug, now fixed, in two parts:

        1. The "already indexed" cache-skip check previously keyed only on
           dedupe_key, with no user component — once two users' pipelines
           ranked the same job listing, the second user's run saw it as
           already-indexed and silently skipped embedding it. Fixed by
           folding user_id into that cache key.

        2. The embedded text and Chroma document ID are BOTH inherently
           per-user, not just the skip-check: fit_score and tailored_pitch
           (baked into the embedded text) are computed against one user's
           specific resume — they are not a property of the job listing
           itself. Using dedupe_key alone as the Chroma document ID would
           mean a second user's upsert silently overwrites (not
           supplements) the first user's fit-scored embedding of that same
           job, which is a real data-loss bug distinct from the caching
           bug above. Fixed by using f"{user_id}:{dedupe_key}" as the
           Chroma ID too, so each user gets their own row for a job they
           both encountered, exactly mirroring the vec_indexed cache-key
           fix. This does mean the same job posting can appear multiple
           times in the collection (once per user who's seen it) rather
           than once globally — intentional, since "similar jobs to this
           one, given MY fit score" is what /api/similar-jobs actually
           needs, not a single shared embedding averaged across users."""
        to_index = [r for r in ranked_jobs if not self._already_indexed(r.job.dedupe_key, user_id)]
        if not to_index:
            return 0

        self._ensure_ready()

        texts = [
            f"{r.job.title} at {r.job.company}. Fit {r.fit_score}/100. {r.tailored_pitch}"
            for r in to_index
        ]
        vectors = [v.tolist() for v in self._embedder.embed(texts)]

        self._collection.upsert(
            ids=[f"{user_id}:{r.job.dedupe_key}" for r in to_index],
            embeddings=vectors,
            documents=texts,
            metadatas=[
                {
                    "title": r.job.title,
                    "company": r.job.company,
                    "fit_score": r.fit_score,
                    "source": r.job.source,
                    "url": r.job.url,
                    "user_id": str(user_id),
                }
                for r in to_index
            ],
        )

        for r in to_index:
            self._mark_indexed(r.job.dedupe_key, user_id)

        return len(to_index)

    def find_similar(self, query: str, top_k: int = 5, user_id: int | str = "local") -> list[dict]:
        """Semantic search over previously-ranked jobs. Used to answer things
        like "what past jobs looked like this one" without re-ranking.

        Filtered to this user's own entries via a where= clause on the
        user_id metadata field (set on every row in index_ranked_jobs) —
        each row's fit_score/embedded text reflects one specific user's
        resume, so a match from another user's entry for the "same" job
        wouldn't be a meaningful recommendation for this caller."""
        self._ensure_ready()

        if self._collection.count() == 0:
            return []

        query_vec = next(self._embedder.embed([query])).tolist()
        result = self._collection.query(
            query_embeddings=[query_vec],
            n_results=min(top_k, self._collection.count()),
            where={"user_id": str(user_id)},
        )

        matches = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for i, doc, meta, dist in zip(ids, docs, metas, distances):
            # ids are stored as "{user_id}:{dedupe_key}" (see
            # index_ranked_jobs) — strip the prefix back off so callers
            # get the plain dedupe_key they'd recognize/look up elsewhere.
            dedupe_key = i.split(":", 1)[1] if ":" in i else i
            matches.append({"dedupe_key": dedupe_key, "document": doc, "metadata": meta, "distance": dist})
        return matches
