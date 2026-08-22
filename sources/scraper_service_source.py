"""Custom job-source URLs, scraped via the job-scraper-service microservice
(separate Render deployment, owns the Playwright/Chromium dependency so the
main API's 512MB tier doesn't need it — see job-scraper-service/app.py).

Gated the same way as ApifyJobSource: needs both the service URL and its
API key present, off otherwise. URLs to scrape come from
SCRAPER_SERVICE_URLS (comma-separated), the env-var-backed MVP for what
will later be the right-side URL-management panel — that panel will write
to a store this reads from instead, without this class's contract
changing (Open/Closed)."""
from __future__ import annotations

import os
import time

import httpx

from models import JobListing
from sources.base import JobSource

_SLOW_CALL_SECONDS = 3.0


class ScraperServiceSource(JobSource):
    name = "scraper_service"

    def is_configured(self) -> bool:
        return bool(self._service_url() and self._api_key() and self._target_urls())

    def _service_url(self) -> str:
        return os.getenv("SCRAPER_SERVICE_URL", "").rstrip("/")

    def _api_key(self) -> str:
        return os.getenv("SCRAPER_SERVICE_API_KEY", "")

    def _target_urls(self) -> list[str]:
        raw = os.getenv("SCRAPER_SERVICE_URLS", "")
        return [u.strip() for u in raw.split(",") if u.strip()]

    def search(self, query: str, location: str, remote_ok: bool, limit: int = 25) -> list[JobListing]:
        # This source doesn't run a keyword search against a single API —
        # it scrapes a fixed set of user-configured URLs and returns
        # whatever job postings each one yields. Query/location filtering
        # already happens downstream (fit ranking, ATS check), so no
        # attempt is made to filter here — that would just duplicate logic
        # the ranking stage does better with an LLM.
        listings: list[JobListing] = []
        for url in self._target_urls():
            listings.extend(self._scrape_one(url))
            if len(listings) >= limit:
                break
        return listings[:limit]

    def _scrape_one(self, url: str) -> list[JobListing]:
        start = time.time()
        print(f"[scraper_service] scraping {url}...", flush=True)
        try:
            resp = httpx.post(
                f"{self._service_url()}/scrape",
                json={"url": url},
                headers={"Authorization": f"Bearer {self._api_key()}"},
                timeout=60,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            elapsed = time.time() - start
            print(f"[scraper_service] request FAILED after {elapsed:.1f}s for {url}: {e}", flush=True)
            return []

        elapsed = time.time() - start
        tag = "SLOW" if elapsed > _SLOW_CALL_SECONDS else "ok"
        print(f"[scraper_service] {url}: response in {elapsed:.1f}s [{tag}]", flush=True)

        data = resp.json()
        if data.get("error"):
            print(f"[scraper_service] scrape error for {url}: {data['error']}", flush=True)
            return []

        return [
            JobListing(
                source=self.name,
                external_id=job["external_id"],
                title=job["title"],
                company=job.get("company") or "Unknown",
                location=job.get("location", ""),
                remote=False,
                description=job.get("description", ""),
                url=job["url"],
                posted_date=None,
                salary_min=None,
                salary_max=None,
            )
            for job in data.get("jobs", [])
        ]
