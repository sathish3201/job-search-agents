"""Simple JSON-backed store for tracked applications. No DB dependency needed at this scale."""
from __future__ import annotations

import json
import os
import threading

from models import Application

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "data", "applications.json")

# Guards every read-modify-write cycle across the whole process. Needed
# because rank_jobs_node now persists each job the instant it's ranked (see
# graph.py's _persist_if_qualifying), called concurrently from the ranker's
# ThreadPoolExecutor workers — each ApplicationStore() instance does a fresh
# load-then-save, so two threads racing here could silently lose one
# thread's write (last save wins, load-before-save means neither sees the
# other's update). A single process-wide lock serializes those cycles.
_lock = threading.Lock()


class ApplicationStore:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
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
