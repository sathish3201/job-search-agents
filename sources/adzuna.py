"""Adzuna job search connector. https://developer.adzuna.com/"""
from __future__ import annotations

import os

import httpx

from models import JobListing
from sources.base import JobSource


class AdzunaSource(JobSource):
    name = "adzuna"

    def is_configured(self) -> bool:
        return bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))

    def search(self, query: str, location: str, remote_ok: bool, limit: int = 25) -> list[JobListing]:
        app_id = os.getenv("ADZUNA_APP_ID")
        app_key = os.getenv("ADZUNA_APP_KEY")
        country = os.getenv("ADZUNA_COUNTRY", "in")

        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": limit,
            "what": query,
            "where": location,
            "content-type": "application/json",
        }

        try:
            resp = httpx.get(url, params=params, timeout=15)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"[adzuna] request failed: {e}")
            return []

        listings = []
        for item in resp.json().get("results", []):
            listings.append(
                JobListing(
                    source=self.name,
                    external_id=str(item.get("id", item.get("redirect_url", ""))),
                    title=item.get("title", ""),
                    company=(item.get("company") or {}).get("display_name", "Unknown"),
                    location=(item.get("location") or {}).get("display_name", ""),
                    remote=False,
                    description=item.get("description", ""),
                    url=item.get("redirect_url", ""),
                    posted_date=item.get("created"),
                    salary_min=item.get("salary_min"),
                    salary_max=item.get("salary_max"),
                )
            )
        return listings
