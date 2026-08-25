"""Background indexing of ranked jobs into the vector store, run as a
FastAPI BackgroundTask after api/pipeline_runner.py's run_pipeline_job()
already returned its result — embedding never happens inside a user-facing
request (see agents/vector_store.py's docstring for why)."""
from __future__ import annotations

from agents.vector_store import VectorStore
from cache import SqliteCache
from models import RankedJob
from store import ApplicationStore

_cache = SqliteCache()
_vector_store = VectorStore(cache=_cache)


def index_ranked_jobs_job(ranked_jobs: list[RankedJob], user_id: int | str = "local") -> None:
    """Runs in the background after a pipeline run. Skips jobs already
    present in the application tracker's seen-keys set (the same SQLite-
    backed "have I processed this job title before" check used elsewhere)
    on top of VectorStore's own per-job dedup, so a repeat run over mostly
    the same job listings does near-zero embedding work."""
    store = ApplicationStore(user_id)
    already_tracked = store.seen_keys()

    # Jobs freshly tracked this run are exactly the ones worth indexing —
    # they're new since the last run. Re-running over jobs that were
    # already tracked before this run is harmless (VectorStore's own
    # _already_indexed check skips them) but filtering here avoids even
    # constructing the embed-input text for jobs we know are stale.
    new_jobs = [r for r in ranked_jobs if r.job.dedupe_key in already_tracked]

    if not new_jobs:
        return

    try:
        count = _vector_store.index_ranked_jobs(new_jobs, user_id=user_id)
        print(f"[vector_indexer] indexed {count} newly-tracked jobs into ChromaDB", flush=True)
    except Exception as e:
        # Indexing is a nice-to-have, not core to the pipeline's job — a
        # failure here must never surface as a pipeline error to the user.
        print(f"[vector_indexer] failed: {e}", flush=True)
