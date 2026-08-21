"""JSearch (via RapidAPI) connector — aggregates LinkedIn/Indeed/Naukri/Glassdoor listings legally."""
from __future__ import annotations

import os

import httpx

from models import JobListing
from sources.base import JobSource


class JSearchSource(JobSource):
    name = "jsearch"
    HOST = "jsearch.p.rapidapi.com"

    def is_configured(self) -> bool:
        return bool(os.getenv("RAPIDAPI_KEY"))

    def search(self, query: str, location: str, remote_ok: bool, limit: int = 25) -> list[JobListing]:
        api_key = os.getenv("RAPIDAPI_KEY")
        headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": self.HOST}
        params = {
            "query": f"{query} in {location}",
            "page": "1",
            "num_pages": "1",
            "remote_jobs_only": "true" if remote_ok else "false",
        }

        try:
            resp = httpx.get(
                "https://jsearch.p.rapidapi.com/search",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            print(f"[jsearch] request failed: {e}")
            return []

        listings = []
        for item in resp.json().get("data", [])[:limit]:
            listings.append(
                JobListing(
                    source=self.name,
                    external_id=item.get("job_id", ""),
                    title=item.get("job_title", ""),
                    company=item.get("employer_name", "Unknown"),
                    location=item.get("job_city") or item.get("job_country", ""),
                    remote=bool(item.get("job_is_remote")),
                    description=item.get("job_description", "") or "",
                    url=item.get("job_apply_link", ""),
                    posted_date=item.get("job_posted_at_datetime_utc"),
                    salary_min=item.get("job_min_salary"),
                    salary_max=item.get("job_max_salary"),
                )
            )
        return listings
