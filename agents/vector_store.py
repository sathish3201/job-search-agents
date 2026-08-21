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

    def _already_indexed(self, dedupe_key: str) -> bool:
        return self._cache.get(f"vec_indexed:{dedupe_key}") is not None

    def _mark_indexed(self, dedupe_key: str) -> None:
        self._cache.set(f"vec_indexed:{dedupe_key}", {"indexed": True})

    def index_ranked_jobs(self, ranked_jobs: list[RankedJob]) -> int:
        """Embeds and stores any ranked jobs not already indexed. Returns the
        count actually embedded (as opposed to skipped-as-duplicate)."""
        to_index = [r for r in ranked_jobs if not self._already_indexed(r.job.dedupe_key)]
        if not to_index:
            return 0

        self._ensure_ready()

        texts = [
            f"{r.job.title} at {r.job.company}. Fit {r.fit_score}/100. {r.tailored_pitch}"
            for r in to_index
        ]
        vectors = [v.tolist() for v in self._embedder.embed(texts)]

        self._collection.upsert(
            ids=[r.job.dedupe_key for r in to_index],
            embeddings=vectors,
            documents=texts,
            metadatas=[
                {
                    "title": r.job.title,
                    "company": r.job.company,
                    "fit_score": r.fit_score,
                    "source": r.job.source,
                    "url": r.job.url,
                }
                for r in to_index
            ],
        )

        for r in to_index:
            self._mark_indexed(r.job.dedupe_key)

        return len(to_index)

    def find_similar(self, query: str, top_k: int = 5) -> list[dict]:
        """Semantic search over previously-ranked jobs. Used to answer things
        like "what past jobs looked like this one" without re-ranking."""
        self._ensure_ready()

        if self._collection.count() == 0:
            return []

        query_vec = next(self._embedder.embed([query])).tolist()
        result = self._collection.query(query_embeddings=[query_vec], n_results=min(top_k, self._collection.count()))

        matches = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for i, doc, meta, dist in zip(ids, docs, metas, distances):
            matches.append({"dedupe_key": i, "document": doc, "metadata": meta, "distance": dist})
        return matches
