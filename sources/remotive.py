"""Remotive public job API — free, no key required, remote-focused listings."""
from __future__ import annotations

import os

import httpx

from models import JobListing
from sources.base import JobSource


class RemotiveSource(JobSource):
    name = "remotive"

    def is_configured(self) -> bool:
        return os.getenv("REMOTIVE_ENABLED", "true").lower() == "true"

    def search(self, query: str, location: str, remote_ok: bool, limit: int = 25) -> list[JobListing]:
        if not remote_ok:
            return []  # Remotive is remote-only; skip if user doesn't want remote roles.

        try:
            resp = httpx.get(
                "https://remotive.com/api/remote-jobs",
                params={"search": query, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"[remotive] request failed: {e}")
            return []

        listings = []
        for item in resp.json().get("jobs", [])[:limit]:
            listings.append(
                JobListing(
                    source=self.name,
                    external_id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    company=item.get("company_name", "Unknown"),
                    location=item.get("candidate_required_location", "Remote"),
                    remote=True,
                    description=item.get("description", ""),
                    url=item.get("url", ""),
                    posted_date=item.get("publication_date"),
                    salary_min=None,
                    salary_max=None,
                )
            )
        return listings
