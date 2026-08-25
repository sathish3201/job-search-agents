"""ChromaDB-backed store of per-user resume vectors — one embedding per
uploaded resume, for candidate/resume similarity ("find similar
candidates"), not RAG over resume content.

Separate from agents/vector_store.py, which stays exactly as-is (embeds
ranked JOB listings, a different collection, a different purpose — not
merged, not conflated).

Design decision worth being explicit about: a resume is one small document
(typically well under ~1-2k tokens). Unlike a multi-document RAG corpus,
where chunking + top-k retrieval earns its keep because there's enough
text and enough documents that semantic chunk retrieval beats "the whole
thing," chunking a single resume mostly reproduces "the whole resume" with
extra machinery and a real risk of losing cross-section context (e.g. a
chunk boundary splitting "5 years experience" from the skill it
modifies). What's actually useful here is one embedding per resume, used
to find similar candidates — so this store does exactly that: one vector
per user, not a chunked index."""
from __future__ import annotations

import os

from models import CandidateProfile

_COLLECTION_NAME = "user_resumes"
_CHROMA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "chroma")
_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def _embed_text_for(profile: CandidateProfile) -> str:
    """The distilled summary/skills/target_roles from parse_profile(),
    not raw resume_raw_text — matches the embedder's ~512-token comfort
    zone and reuses the structured distillation already produced, rather
    than truncating raw text at an arbitrary cutoff."""
    return (
        f"{profile.headline}. {profile.summary} "
        f"Skills: {', '.join(profile.skills)}. "
        f"Target roles: {', '.join(profile.target_roles)}."
    )


class ResumeVectorStore:
    def __init__(self):
        self._client = None
        self._collection = None
        self._embedder = None

    def _ensure_ready(self) -> None:
        if self._collection is not None:
            return

        import chromadb

        self._client = chromadb.PersistentClient(path=_CHROMA_PATH)
        self._collection = self._client.get_or_create_collection(_COLLECTION_NAME)

        from fastembed import TextEmbedding

        self._embedder = TextEmbedding(model_name=_EMBED_MODEL)

    def upsert_resume(self, user_id: int, email: str, profile: CandidateProfile) -> None:
        """Using user_id (not a random id) as the Chroma document ID makes
        re-upload idempotent — a second resume upload just overwrites this
        user's one row, matching the real model here ("one resume per
        user"), not "one row per upload event"."""
        self._ensure_ready()

        text = _embed_text_for(profile)
        vector = next(self._embedder.embed([text])).tolist()

        self._collection.upsert(
            ids=[str(user_id)],
            embeddings=[vector],
            documents=[text],
            metadatas=[
                {
                    "user_id": user_id,
                    "email": email,
                    "name": profile.name,
                    "target_roles": ",".join(profile.target_roles),
                }
            ],
        )

    def find_similar_candidates(self, query_text: str, top_k: int = 5) -> list[dict]:
        """Not exposed via an API endpoint in this phase — the confirmed
        core ask ("easy to find his relevant job... if there are so many
        resumes uploaded") is satisfied by each user having their own
        isolated profile/job-search, which doesn't need this method at
        all. Built for a possible future "find similar candidates" feature
        without speculatively wiring a route for it now."""
        self._ensure_ready()

        if self._collection.count() == 0:
            return []

        query_vec = next(self._embedder.embed([query_text])).tolist()
        result = self._collection.query(
            query_embeddings=[query_vec], n_results=min(top_k, self._collection.count())
        )

        matches = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for i, doc, meta, dist in zip(ids, docs, metas, distances):
            matches.append({"user_id": i, "document": doc, "metadata": meta, "distance": dist})
        return matches
