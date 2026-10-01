"""Route pasted URLs to a source adapter and enforce per-source quotas."""

from __future__ import annotations

from jobfather_crawler.logging_conf import get_logger
from jobfather_crawler.sources import SourceAdapter

log = get_logger(__name__)


def resolve_source(url: str, registry: dict[str, SourceAdapter]) -> str | None:
    """Return the configured source key that owns *url*, else None."""
    for key, adapter in registry.items():
        if adapter.matches(url):
            return key
    return None


def plan_urls(
    urls: list[str],
    registry: dict[str, SourceAdapter],
    *,
    max_jobs_total: int | None = None,
) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Group URLs by source, enforcing per-source and global caps.

    Returns ``(grouped, unroutable)`` where *unroutable* is a list of
    ``(url, reason)`` pairs for URLs no source claims.
    """
    grouped: dict[str, list[str]] = {}
    unroutable: list[tuple[str, str]] = []

    for url in urls:
        key = resolve_source(url, registry)
        if key is None:
            unroutable.append((url, "no configured source matches this host"))
            continue
        grouped.setdefault(key, []).append(url)

    # Per-source caps ------------------------------------------------------
    for key, items in list(grouped.items()):
        cap = registry[key].spec.max_jobs
        if len(items) > cap:
            log.warning(
                "[%s] %d URLs supplied, per-source cap is %d - truncating",
                key,
                len(items),
                cap,
            )
            grouped[key] = items[:cap]

    # Global ceiling -------------------------------------------------------
    if max_jobs_total is not None:
        remaining = max_jobs_total
        for key in list(grouped.keys()):
            items = grouped[key]
            if remaining <= 0:
                log.warning("[%s] skipped entirely - global ceiling of %d reached", key, max_jobs_total)
                grouped.pop(key)
                continue
            if len(items) > remaining:
                log.warning(
                    "[%s] truncating %d -> %d to respect the global ceiling of %d",
                    key,
                    len(items),
                    remaining,
                    max_jobs_total,
                )
                grouped[key] = items[:remaining]
            remaining -= len(grouped[key])

    return grouped, unroutable
