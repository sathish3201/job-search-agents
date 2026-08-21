"""JobSource interface: every connector implements search() and returns JobListing objects."""
from __future__ import annotations

from abc import ABC, abstractmethod

from models import JobListing


class JobSource(ABC):
    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this source has the env vars/keys it needs to run."""

    @abstractmethod
    def search(self, query: str, location: str, remote_ok: bool, limit: int = 25) -> list[JobListing]:
        """Return normalized job listings. Must not raise on empty results."""
