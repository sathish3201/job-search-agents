"""Apify-backed job source: runs Apify actors (scrapers) against configured
target sites and normalizes their output into JobListing objects.

GATED, NOT ALWAYS-ON — same risk category as agents/automation.py: scraping
LinkedIn/Naukri (even through a third-party service like Apify) violates
both platforms' Terms of Service, and accounts/IPs used for this can get
flagged. This source only activates if BOTH APIFY_API_KEY is set AND
APIFY_ALLOW_SCRAPING=true is explicitly set — presence of the key alone is
not enough, matching the "explicit opt-in, not silent default" pattern
used for the LinkedIn/Naukri login automation.

Extensible by design (Open/Closed): new target sites are added via
APIFY_TARGETS in .env, not by editing this file. Each target is
"actor_id|label" (e.g. "apify/linkedin-jobs-scraper|linkedin"), comma-
separated. Add a URL/actor by adding a config line, not a code change.
"""
from __future__ import annotations

import os
import time

from cache import SqliteCache, content_hash
from models import JobListing
from sources.base import JobSource

_SLOW_CALL_SECONDS = 3.0

# How long a cached Apify scrape result stays valid before a repeat search
# re-runs the actor. Job listings go stale fast (postings close), but Apify
# actor runs cost real credits, so a same-day repeat run within this window
# should not re-pay for the same search. Timestamped inside the cached
# value itself (same pattern as scripts/scrape_portfolio.py) since
# cache.SqliteCache has no native TTL support.
_CACHE_TTL_SECONDS = 6 * 60 * 60


class ApifyTarget:
    """One configured (actor, platform label) pair — the unit of extension
    the user adds more of via APIFY_TARGETS, without touching this file."""

    def __init__(self, actor_id: str, label: str):
        self.actor_id = actor_id
        self.label = label


def _parse_targets(raw: str) -> list[ApifyTarget]:
    targets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "|" not in entry:
            print(f"[apify] skipping malformed APIFY_TARGETS entry (expected 'actor_id|label'): {entry}")
            continue
        actor_id, label = entry.split("|", 1)
        targets.append(ApifyTarget(actor_id.strip(), label.strip()))
    return targets


def _default_run_input(query: str, location: str, limit: int) -> dict:
    """Fallback input shape for an actor with no entry in _RUN_INPUT_BUILDERS
    below. Reasonable common field names, but not guaranteed to match any
    given actor's real schema — check the actor's input schema
    (client.build(build_id).get().input_schema) before adding a new one, the
    way linkedin/naukri were verified here, and add a builder for it."""
    return {"query": query, "location": location, "maxItems": limit}


def _linkedin_run_input(query: str, location: str, limit: int) -> dict:
    """Verified against valig/linkedin-jobs-scraper's actual input schema
    (title, location, limit — no required fields) with a real run: 5/5 jobs
    returned successfully. Previously pointed at bebity/linkedin-jobs-
    scraper, which requires a paid rental this account doesn't have (fails
    every call with a 403 — see git history). valig's actor was found via
    the Apify store, confirmed accessible with this account's key, and its
    output field names (title, companyName, location, url, description,
    id) already match _normalize()'s existing fallback chains exactly, no
    output-mapping changes needed."""
    return {
        "title": query,
        "location": location or "India",
        "limit": limit,
    }


def _naukri_run_input(query: str, location: str, limit: int) -> dict:
    """Verified against muhammetakkurtt/naukri-job-scraper's actual input
    schema (required: maxJobs; keyword/cities are optional filters). Real
    constraints confirmed by actual calls, not documented in the schema's
    field descriptions:
      - maxJobs must be >= 50 server-side.
      - cities takes Naukri's internal numeric location-ID codes, not free-
        text city names — passing "India" or "Bengaluru" directly gets
        rejected. There's no public id-lookup table shipped with the actor.
      - keyword must stay a clean, unmodified search phrase — folding
        location into it (e.g. "AI Engineer Hyderabad, India") was
        confirmed by a real side-by-side test to silently degrade results
        to generic/unrelated postings (47,327 correctly-matched results
        for "AI Engineer" alone vs. Java/SAP/voice-process noise once
        location text was appended). Location is dropped rather than
        risk-fed into keyword; this actor has no working location filter
        given the cities-ID constraint above, so results are unfiltered by
        location until a custom scraper replaces this actor."""
    return {"maxJobs": max(limit, 50), "keyword": query}


# Maps actor_id -> a function building that actor's specific run input.
# Keyed by actor_id (not label) so two different actors sharing a label
# could still each get correct input construction. Extend this alongside
# APIFY_TARGETS when adding a genuinely new actor with its own schema — an
# actor not listed here falls back to _default_run_input, which is a guess.
_RUN_INPUT_BUILDERS = {
    "valig/linkedin-jobs-scraper": _linkedin_run_input,
    "muhammetakkurtt/naukri-job-scraper": _naukri_run_input,
}


class ApifyJobSource(JobSource):
    name = "apify"

    def __init__(self, cache: SqliteCache | None = None):
        self._cache = cache or SqliteCache()

    def is_configured(self) -> bool:
        has_key = bool(os.getenv("APIFY_API_KEY"))
        opted_in = os.getenv("APIFY_ALLOW_SCRAPING", "false").lower() == "true"
        if has_key and not opted_in:
            print(
                "[apify] APIFY_API_KEY is set but APIFY_ALLOW_SCRAPING is not 'true' — "
                "skipping. LinkedIn/Naukri scraping violates their ToS; this source stays "
                "off until you explicitly opt in.",
                flush=True,
            )
        return has_key and opted_in

    def _targets(self) -> list[ApifyTarget]:
        raw = os.getenv("APIFY_TARGETS", "")
        return _parse_targets(raw)

    def search(self, query: str, location: str, remote_ok: bool, limit: int = 25) -> list[JobListing]:
        targets = self._targets()
        if not targets:
            print("[apify] APIFY_TARGETS is empty — nothing to scrape. "
                  "Add entries like 'actor_id|label' to .env.", flush=True)
            return []

        from apify_client import ApifyClient

        client = ApifyClient(os.getenv("APIFY_API_KEY"))
        all_jobs: list[JobListing] = []

        for target in targets:
            jobs = self._run_target(client, target, query, location, limit)
            print(f"[apify] {target.label} ({target.actor_id}): {len(jobs)} results", flush=True)
            all_jobs.extend(jobs)

        return all_jobs

    def _run_target(
        self, client, target: ApifyTarget, query: str, location: str, limit: int
    ) -> list[JobListing]:
        # Cache key covers the actor + search params, not individual jobs —
        # an Apify actor call is billed per run regardless of how many jobs
        # come back, so the unit worth avoiding a repeat of is the whole
        # search, not per-job dedup (that already happens via JobListing's
        # own dedupe_key downstream in the pipeline).
        cache_key = "apify_search:" + content_hash(target.actor_id, query, location, str(limit))
        cached = self._cache.get(cache_key)
        if cached is not None and (time.time() - cached["fetched_at"]) < _CACHE_TTL_SECONDS:
            age_min = int((time.time() - cached["fetched_at"]) / 60)
            print(
                f"[apify] {target.label} ({target.actor_id}): cache hit "
                f"({len(cached['jobs'])} jobs, {age_min}m old) — skipping actor run",
                flush=True,
            )
            return [JobListing(**job) for job in cached["jobs"]]

        build_input = _RUN_INPUT_BUILDERS.get(target.actor_id, _default_run_input)
        if build_input is _default_run_input:
            print(
                f"[apify] {target.actor_id} has no verified input schema mapping — "
                "using a generic guess, which may not match this actor's real fields. "
                "Check its input schema and add an entry to _RUN_INPUT_BUILDERS.",
                flush=True,
            )
        run_input = build_input(query, location, limit)

        call_start = time.time()
        print(f"[apify] {target.label} ({target.actor_id}): actor run starting...", flush=True)
        try:
            run = client.actor(target.actor_id).call(run_input=run_input)
            # apify-client's Run is a pydantic model (attribute access), not
            # a dict — confirmed by a real failed call during development
            # (TypeError: 'Run' object is not subscriptable). run["..."]
            # would have raised on every single successful actor call.
            items = list(client.dataset(run.default_dataset_id).iterate_items())
            elapsed = time.time() - call_start
            tag = "SLOW" if elapsed > _SLOW_CALL_SECONDS else "ok"
            print(
                f"[apify] {target.label} ({target.actor_id}): actor run took {elapsed:.1f}s [{tag}]",
                flush=True,
            )
        except Exception as e:
            elapsed = time.time() - call_start
            print(
                f"[apify] {target.label} ({target.actor_id}) run FAILED after {elapsed:.1f}s: {e}",
                flush=True,
            )
            return []

        jobs = [self._normalize(item, target) for item in items[:limit]]
        self._cache.set(
            cache_key,
            {"fetched_at": time.time(), "jobs": [j.model_dump() for j in jobs]},
        )
        return jobs

    def _normalize(self, item: dict, target: ApifyTarget) -> JobListing:
        """Apify actors don't share a single output schema — this maps the
        common field name variants seen across job-scraper actors. Fields
        verified against a real run's actual output (not guessed) are noted
        inline; unverified fallbacks are best-effort for actors not yet
        confirmed. If a given actor uses different keys, adjust the .get()
        fallback chains here (single place to fix per-actor quirks, not
        per-caller)."""
        title = item.get("title") or item.get("jobTitle") or item.get("position") or ""
        company = item.get("company") or item.get("companyName") or item.get("employer") or "Unknown"
        location = item.get("location") or item.get("jobLocation") or ""
        # jdURL verified against a real muhammetakkurtt/naukri-job-scraper
        # run; the others are unverified guesses for other actors.
        url = item.get("jdURL") or item.get("url") or item.get("jobUrl") or item.get("link") or ""
        description = item.get("jobDescription") or item.get("description") or ""
        external_id = str(item.get("jobId") or item.get("id") or url or f"{title}:{company}")

        return JobListing(
            source=f"apify:{target.label}",
            external_id=external_id,
            title=title,
            company=company,
            location=location,
            remote="remote" in location.lower() if location else False,
            description=description,
            url=url,
        )
