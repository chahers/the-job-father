"""Registry of job-board source adapters, built from config/sources.yaml."""

from __future__ import annotations

from typing import Any

from jobfather_crawler.sources.base import SourceAdapter, SourceSpec, parse_spec
from jobfather_crawler.sources.hiredly import HiredlyAdapter
from jobfather_crawler.sources.indeed import IndeedAdapter
from jobfather_crawler.sources.jobstreet import JobStreetAdapter
from jobfather_crawler.sources.linkedin import LinkedInAdapter

ADAPTER_CLASSES: dict[str, type[SourceAdapter]] = {
    "jobstreet": JobStreetAdapter,
    "hiredly": HiredlyAdapter,
    "indeed": IndeedAdapter,
    "linkedin": LinkedInAdapter,
}

__all__ = [
    "ADAPTER_CLASSES",
    "SourceAdapter",
    "SourceSpec",
    "build_registry",
    "parse_spec",
]


def build_registry(sources_raw: dict[str, dict[str, Any]]) -> dict[str, SourceAdapter]:
    """Instantiate one adapter per configured source."""
    registry: dict[str, SourceAdapter] = {}
    for name, raw in (sources_raw or {}).items():
        spec = parse_spec(name, raw)
        adapter_cls = ADAPTER_CLASSES.get(name, SourceAdapter)
        registry[name] = adapter_cls(spec)
    return registry
