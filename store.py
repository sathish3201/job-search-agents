"""Simple JSON-backed store for tracked applications. No DB dependency
needed at this scale.

Per-user since multi-user support was added: one file per user, at
data/applications/{user_id}.json, rather than a single shared
data/applications.json. This is a structural ownership guarantee (which
file you opened), not a filter condition that could be forgotten at some
call site — deliberately chosen over "one shared file plus a user_id
field" for that reason. It also sidesteps a real data-model question: once
two users can independently apply to the same job listing, "job X applied
to by user A" and "job X applied to by user B" need to be two independent
records, not one dedupe_key-keyed row shared between them — one file per
user makes dedupe_key safely user-scoped again without a composite key."""
from __future__ import annotations

import json
import os
import threading

from models import Application

DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "data", "applications")

# Guards every read-modify-write cycle across the whole process. Needed
# because rank_jobs_node now persists each job the instant it's ranked (see
# graph.py's _persist_if_qualifying), called concurrently from the ranker's
# ThreadPoolExecutor workers — each ApplicationStore() instance does a fresh
# load-then-save, so two threads racing here could silently lose one
# thread's write (last save wins, load-before-save means neither sees the
# other's update). One process-wide lock, shared across all users' files —
# slightly over-serializes concurrent writes to two different users' files,
# but that's a minor throughput cost, never a correctness issue, and keeps
# this simple rather than introducing per-file locking.
_lock = threading.Lock()


def _path_for(user_id: int | str) -> str:
    return os.path.join(DEFAULT_DIR, f"{user_id}.json")


class ApplicationStore:
    def __init__(self, user_id: int | str = "local", path: str | None = None):
        self.user_id = user_id
        self.path = path or _path_for(user_id)
        self._apps: dict[str, Application] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self._apps = {k: Application.model_validate(v) for k, v in raw.items()}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({k: v.model_dump(mode="json") for k, v in self._apps.items()}, f, indent=2)

    def upsert(self, app: Application) -> None:
        with _lock:
            self._load()  # pick up any writes made by another thread since __init__
            self._apps[app.dedupe_key] = app
            self.save()

    def get(self, dedupe_key: str) -> Application | None:
        return self._apps.get(dedupe_key)

    def all(self) -> list[Application]:
        return list(self._apps.values())

    def seen_keys(self) -> set[str]:
        with _lock:
            self._load()
            return set(self._apps.keys())
