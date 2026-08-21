"""Fast embedding-based pre-filter: narrows N raw jobs down to the top-K most
relevant before they ever reach the (slow) LLM ranker. Single responsibility:
similarity ranking only — it doesn't score fit quality or write pitches, that's
LLMRanker's job (agents/ranker.py). This is what actually saves time: instead
of running an LLM call per job, we embed once per job (cheap, local, fast via
fastembed's ONNX models) and only send the shortlist to the LLM.
"""
from __future__ import annotations

import os

from fastembed import TextEmbedding

from cache import SqliteCache, content_hash
from models import CandidateProfile, JobListing

_MODEL_NAME = "BAAI/bge-small-en-v1.5"  # small, fast, good enough for this shortlist task


class EmbeddingFilter:
    def __init__(self, cache: SqliteCache | None = None):
        self._model = TextEmbedding(model_name=_MODEL_NAME)
        self._cache = cache or SqliteCache()

    def _embed_cached(self, text: str) -> list[float]:
        key = "embed:" + content_hash(_MODEL_NAME, text)
        cached = self._cache.get(key)
        if cached is not None:
            return cached["vector"]
        vector = next(self._model.embed([text])).tolist()
        self._cache.set(key, {"vector": vector})
        return vector

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

    def shortlist(
        self, jobs: list[JobListing], profile: CandidateProfile, top_k: int = 15
    ) -> list[JobListing]:
        if len(jobs) <= top_k:
            return jobs  # nothing to filter, skip embedding work entirely

        profile_text = f"{profile.headline}. {profile.summary}. Skills: {', '.join(profile.skills)}"
        profile_vec = self._embed_cached(profile_text)

        scored = []
        for job in jobs:
            job_text = f"{job.title} at {job.company}. {job.description[:800]}"
            job_vec = self._embed_cached(job_text)
            scored.append((self._cosine(profile_vec, job_vec), job))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [job for _, job in scored[:top_k]]
