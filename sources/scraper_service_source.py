"""Custom job-source URLs, scraped in-process via agents/url_scraper.py.

Originally called out to a separate job-scraper-service microservice (to
keep Playwright/Chromium off the main API's Render instance). Merged back
in once BROWSERLESS_WS_URL made the browser itself remote — see
agents/url_scraper.py's module docstring for the full reasoning. Renamed
class kept as ScraperServiceSource / name "scraper_service" for
continuity with existing cached data and .env var names, even though
"service" now means "module" rather than "separate deployment".

Gated on having at least one target URL configured — no API key/service
URL gate needed anymore since there's no separate service to authenticate
to. URLs come from SCRAPER_SERVICE_URLS (comma-separated), the env-var-
backed MVP for what will later be the right-side URL-management panel —
that panel will write to a store this reads from instead, without this
class's contract changing (Open/Closed)."""
from __future__ import annotations

import os

from agents.url_scraper import scrape_url
from models import JobListing
from sources.base import JobSource


class ScraperServiceSource(JobSource):
    name = "scraper_service"

    def is_configured(self) -> bool:
        return bool(self._target_urls())

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
            listings.extend(scrape_url(url))
            if len(listings) >= limit:
                break
        return listings[:limit]
