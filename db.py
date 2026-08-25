"""SQLite-backed user account store. Single responsibility: email/hashed-
password rows, nothing else — password hashing itself lives in auth.py,
not here. Kept in a separate file from cache.py's data/cache.sqlite3 on
purpose: cache is disposable/rebuildable (delete it, nothing is lost
except re-computed LLM calls), user accounts are not — keeping them in
separate files means a "clear the cache to fix a bug" operation can never
accidentally touch account data."""
from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "data", "auth.sqlite3")


@dataclass
class UserRow:
    id: int
    email: str
    hashed_password: str
    created_at: str


class UserStore:
    """Same check_same_thread=False + lock-around-every-call pattern as
    cache.py's SqliteCache — FastAPI can serve requests from multiple
    threads, and sqlite3 connections aren't thread-safe to use
    concurrently without one."""

    def __init__(self, path: str = DEFAULT_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            self._conn.commit()

    def create_user(self, email: str, hashed_password: str) -> int:
        """Raises sqlite3.IntegrityError if the email already exists —
        callers (api/routers/auth.py) catch this and turn it into a 409,
        rather than this layer knowing about HTTP status codes."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO users (email, hashed_password, created_at) VALUES (?, ?, ?)",
                (email.lower().strip(), hashed_password, created_at),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_by_email(self, email: str) -> Optional[UserRow]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, hashed_password, created_at FROM users WHERE email = ?",
                (email.lower().strip(),),
            ).fetchone()
        return UserRow(*row) if row else None

    def get_by_id(self, user_id: int) -> Optional[UserRow]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, hashed_password, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return UserRow(*row) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
