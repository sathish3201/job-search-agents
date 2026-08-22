"""Registry of all job sources. Add a connector here to make it available at runtime."""
from sources.adzuna import AdzunaSource
from sources.apify_source import ApifyJobSource
from sources.base import JobSource
from sources.jsearch import JSearchSource
from sources.remotive import RemotiveSource
from sources.scraper_service_source import ScraperServiceSource

ALL_SOURCES: list[JobSource] = [
    AdzunaSource(),
    JSearchSource(),
    RemotiveSource(),
    ApifyJobSource(),
    ScraperServiceSource(),
]


def active_sources() -> list[JobSource]:
    """Only sources with valid keys/config present. This is the "choose dynamically,
    default sensibly" behavior: nothing errors if a key is missing, it just skips."""
    return [s for s in ALL_SOURCES if s.is_configured()]


def sources_by_name(names: list[str]) -> list[JobSource]:
    wanted = {n.lower() for n in names}
    return [s for s in ALL_SOURCES if s.name in wanted]
