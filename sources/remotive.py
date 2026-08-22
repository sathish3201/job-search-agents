"""Remotive public job API — free, no key required, remote-focused listings.

Default-off, opt-in via REMOTIVE_ENABLED=true — LinkedIn (Apify) and Naukri
(Apify) are the default active sources per user direction; Remotive only
runs when explicitly approved, same "off unless opted in" gating style as
sources/apify_source.py's APIFY_ALLOW_SCRAPING (though Remotive itself
carries none of the ToS/scraping risk that gate exists for — this is
purely about which sources run by default, not a safety gate)."""
from __future__ import annotations

import os

import httpx

from models import JobListing
from sources.base import JobSource


class RemotiveSource(JobSource):
    name = "remotive"

    def is_configured(self) -> bool:
        return os.getenv("REMOTIVE_ENABLED", "false").lower() == "true"

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
