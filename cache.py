"""SQLite-backed cache for expensive operations (embeddings, LLM ranking calls).
Single responsibility: content-hash -> cached result, nothing else. Both the
embedding filter and the LLM ranker depend on this instead of talking to
sqlite3 directly (Dependency Inversion) — swap the backend later without
touching callers."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from typing import Optional

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "data", "cache.sqlite3")


def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class SqliteCache:
    """Used from a ThreadPoolExecutor (parallel job ranking), so the connection
    is opened with check_same_thread=False and all access is serialized behind
    a lock — sqlite3's own locking handles write safety, this just avoids the
    "created in thread X, used in thread Y" ProgrammingError."""

    def __init__(self, path: str = DEFAULT_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._conn.commit()

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
