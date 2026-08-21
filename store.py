"""Simple JSON-backed store for tracked applications. No DB dependency needed at this scale."""
from __future__ import annotations

import json
import os

from models import Application

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "data", "applications.json")


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
        self._apps[app.dedupe_key] = app
        self.save()

    def get(self, dedupe_key: str) -> Application | None:
        return self._apps.get(dedupe_key)

    def all(self) -> list[Application]:
        return list(self._apps.values())

    def seen_keys(self) -> set[str]:
        return set(self._apps.keys())
