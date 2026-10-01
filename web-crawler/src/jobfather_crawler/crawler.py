"""Crawl4AI orchestration: browser/run config, dispatchers, extraction, staging.

Crawl4AI is imported lazily (see :func:`_load_crawl4ai`) so every other module -
and the whole unit-test suite - works without the heavy browser stack installed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jobfather_crawler import dedup
from jobfather_crawler import extract
from jobfather_crawler import retry as retry_mod
from jobfather_crawler.config import Runtime
from jobfather_crawler.logging_conf import get_logger
from jobfather_crawler.models import CrawlReport, RunRequest, UrlOutcome, utcnow
from jobfather_crawler.registry import plan_urls
from jobfather_crawler.sources import SourceAdapter, build_registry
from jobfather_crawler.staging import StageWriter

log = get_logger(__name__)


class CrawlConfigurationError(RuntimeError):
    """Raised when the run cannot start (e.g. CDP requested but not reachable)."""


def _load_crawl4ai() -> SimpleNamespace:
    """Import the Crawl4AI surface we use, with small cross-version shims."""
    import crawl4ai as c4a

    symbols: dict[str, Any] = {}
    for name in ("AsyncWebCrawler", "BrowserConfig", "CrawlerRunConfig", "CacheMode"):
        symbol = getattr(c4a, name, None)
        if symbol is None:
            raise CrawlConfigurationError(f"crawl4ai is missing required symbol '{name}'")
        symbols[name] = symbol

    for name in ("MemoryAdaptiveDispatcher", "SemaphoreDispatcher", "RateLimiter", "CrawlerMonitor"):
        symbols[name] = getattr(c4a, name, None)

    if symbols["RateLimiter"] is None:
        from crawl4ai.async_dispatcher import RateLimiter

        symbols["RateLimiter"] = RateLimiter
    if symbols["MemoryAdaptiveDispatcher"] is None:
        from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher

        symbols["MemoryAdaptiveDispatcher"] = MemoryAdaptiveDispatcher

    try:  # prefer the fast lxml implementation; older releases ship only the BS4 one
        from crawl4ai.content_filter_strategy import (
            PruningContentFilterLXML as PruningContentFilter,
        )
    except ImportError:  # pragma: no cover - version shim
        from crawl4ai.content_filter_strategy import PruningContentFilter
    symbols["PruningContentFilter"] = PruningContentFilter

    from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    symbols["JsonCssExtractionStrategy"] = JsonCssExtractionStrategy
    symbols["DefaultMarkdownGenerator"] = DefaultMarkdownGenerator
    return SimpleNamespace(**symbols)


def _browser_config(c4a: SimpleNamespace, runtime: Runtime, adapter: SourceAdapter):
    """CDP-attached Chrome for anti-bot sources; persistent profile otherwise."""
    browser_doc = runtime.doc.browser
    common = {
        "browser_type": "chromium",
        "viewport_width": browser_doc.viewport_width,
        "viewport_height": browser_doc.viewport_height,
        "verbose": False,
    }

    if adapter.needs_cdp:
        cdp_url = runtime.cdp_url
        if not cdp_url:
            raise CrawlConfigurationError(
                f"[{adapter.name}] needs a CDP-attached browser but CRAWLER_CDP_URL is empty"
            )
        return c4a.BrowserConfig(
            **common,
            headless=False,
            browser_mode="custom",
            use_managed_browser=True,
            cdp_url=cdp_url,
        )

    return c4a.BrowserConfig(
        **common,
        headless=runtime.env.crawler_headless,
        use_managed_browser=True,
        user_data_dir=str(runtime.user_data_dir),
    )


def _run_config(c4a: SimpleNamespace, runtime: Runtime, adapter: SourceAdapter, request: RunRequest):
    markdown_doc = runtime.doc.markdown
    browser_doc = runtime.doc.browser

    strategy = (
        c4a.JsonCssExtractionStrategy(adapter.spec.css_schema)
        if adapter.spec.css_schema
        else None
    )
    content_filter = (
        c4a.PruningContentFilter(threshold=markdown_doc.pruning_threshold)
        if markdown_doc.content_filter == "pruning"
        else None
    )
    force = bool(request.options.force_refresh or runtime.doc.run.force_refresh)

    return c4a.CrawlerRunConfig(
        cache_mode=c4a.CacheMode.BYPASS if force else c4a.CacheMode.ENABLED,
        page_timeout=browser_doc.page_timeout_ms,
        wait_until=browser_doc.wait_until,
        wait_for=adapter.spec.wait_for,
        js_code=adapter.spec.js_code,
        extraction_strategy=strategy,
        markdown_generator=c4a.DefaultMarkdownGenerator(content_filter=content_filter),
        excluded_tags=list(markdown_doc.excluded_tags),
        locale=browser_doc.locale,
        timezone_id=browser_doc.timezone_id,
        check_robots_txt=True,
        screenshot=False,
        verbose=False,
    )


def _dispatcher(c4a: SimpleNamespace, runtime: Runtime, adapter: SourceAdapter):
    limiter_cfg = runtime.doc.dispatcher.rate_limiter
    rate_limiter = c4a.RateLimiter(
        base_delay=tuple(limiter_cfg.base_delay),
        max_delay=limiter_cfg.max_delay,
        max_retries=limiter_cfg.max_retries,
        rate_limit_codes=list(limiter_cfg.rate_limit_codes),
    )
    monitor = c4a.CrawlerMonitor() if c4a.CrawlerMonitor else None

    if adapter.spec.dispatcher == "semaphore" and c4a.SemaphoreDispatcher is not None:
        return c4a.SemaphoreDispatcher(
            max_session_permit=adapter.spec.max_concurrency,
            rate_limiter=rate_limiter,
            monitor=monitor,
        )

    return c4a.MemoryAdaptiveDispatcher(
        memory_threshold_percent=runtime.doc.dispatcher.memory_threshold_percent,
        rate_limiter=rate_limiter,
        monitor=monitor,
    )


# --------------------------------------------------------------------------- #
# result -> JobPosting
# --------------------------------------------------------------------------- #
def _result_markdown(result: Any) -> str:
    """Handle both ``str`` and MarkdownGenerationResult-shaped objects."""
    markdown = getattr(result, "markdown", None)
    if markdown is None:
        return ""
    if isinstance(markdown, str):
        return markdown
    for attribute in ("fit_markdown", "raw_markdown"):
        value = getattr(markdown, attribute, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(markdown)


def _resolve_fields(result: Any, adapter: SourceAdapter) -> dict[str, Any]:
    """Prefer schema.org JSON-LD, fall back to Crawl4AI's CSS extraction."""
    if adapter.spec.prefer_json_ld:
        node = extract.find_job_posting_ld(getattr(result, "html", None))
        if node:
            return extract.ld_to_fields(node)
    return extract.extracted_to_fields(getattr(result, "extracted_content", None))


def _job_from_result(result: Any, url: str, source: str, adapter: SourceAdapter, request: RunRequest):
    fields = _resolve_fields(result, adapter)
    source_job_id = adapter.job_id_from_url(url) or fields.get("source_job_id")
    job = extract.build_job_posting(
        fields,
        url=url,
        source=source,
        request=request,
        fallback_markdown=_result_markdown(result),
        source_job_id=source_job_id,
    )
    return job


def _process_result(
    result: Any,
    url: str,
    source: str,
    adapter: SourceAdapter,
    request: RunRequest,
    writer: StageWriter,
    save_policy: str,
) -> tuple[UrlOutcome | None, str | None]:
    """Return ``(outcome, error)`` for one crawl result."""
    if not getattr(result, "success", False):
        return None, (getattr(result, "error_message", None) or "crawl failed")

    try:
        job = _job_from_result(result, url, source, adapter, request)
    except Exception as exc:  # noqa: BLE001 - surfaced as a failure reason
        return None, f"{type(exc).__name__}: {exc}"

    reason = extract.validate_posting(job)
    if reason:
        return None, reason

    slug = writer.write_job(job, raw_html=getattr(result, "html", None), save_html=save_policy)
    return UrlOutcome(url=url, source=source, status="staged", slug=slug), None


# --------------------------------------------------------------------------- #
# crawl execution
# --------------------------------------------------------------------------- #
async def _crawl_group(
    c4a: SimpleNamespace,
    runtime: Runtime,
    adapter: SourceAdapter,
    urls: list[str],
    request: RunRequest,
    writer: StageWriter,
    save_policy: str,
) -> list[UrlOutcome]:
    """Crawl one source's URLs (one browser profile) with backoff retries."""
    source = adapter.name
    retry_cfg = runtime.doc.retry
    outcomes: list[UrlOutcome] = []
    pending: dict[str, str] = {}
    captured_html: dict[str, str] = {}

    def record(result: Any, url: str, attempt: int) -> None:
        outcome, error = _process_result(
            result, url, source, adapter, request, writer, save_policy
        )
        if outcome is not None:
            outcome.attempts = attempt
            outcomes.append(outcome)
        else:
            pending[url] = error or "unknown error"

    async with c4a.AsyncWebCrawler(config=_browser_config(c4a, runtime, adapter)) as crawler:
        concurrency = adapter.concurrency(runtime.doc.run.default_max_concurrency)
        log.info("[%s] crawling %d URL(s) (concurrency=%d)", source, len(urls), concurrency)
        run_cfg = _run_config(c4a, runtime, adapter, request)

        results = await crawler.arun_many(
            urls=list(urls), config=run_cfg, dispatcher=_dispatcher(c4a, runtime, adapter)
        )
        result_list = results if isinstance(results, list) else [r async for r in results]

        by_norm = {dedup.normalize_url(u): u for u in urls}
        for index, result in enumerate(result_list):
            result_url = getattr(result, "url", None)
            requested = by_norm.get(dedup.normalize_url(result_url)) if result_url else None
            if requested is None:
                requested = urls[index] if index < len(urls) else (result_url or f"unknown-{index}")
            if save_policy != "never":
                html = getattr(result, "html", None)
                if html:
                    captured_html[requested] = html
            record(result, requested, attempt=1)

        attempt = 1
        while pending and attempt < max(retry_cfg.max_attempts, 1):
            queued = dict(pending)
            pending.clear()
            retryable: dict[str, str] = {}
            for url, error in queued.items():
                if retry_mod.classify(error) == "permanent":
                    outcomes.append(
                        UrlOutcome(
                            url=url, source=source, status="failed", error=error, attempts=attempt
                        )
                    )
                else:
                    retryable[url] = error
            if not retryable:
                break

            delay = retry_mod.backoff_delay(
                attempt,
                base=retry_cfg.base_delay_seconds,
                factor=retry_cfg.backoff_factor,
                cap=retry_cfg.max_delay_seconds,
            )
            log.warning("[%s] retrying %d URL(s) in %.1fs", source, len(retryable), delay)
            await asyncio.sleep(delay)
            attempt += 1

            for url in retryable:
                try:
                    result = await crawler.arun(url=url, config=run_cfg)
                except Exception as exc:  # noqa: BLE001
                    pending[url] = f"{type(exc).__name__}: {exc}"
                    continue
                if save_policy != "never":
                    html = getattr(result, "html", None)
                    if html:
                        captured_html[url] = html
                record(result, url, attempt=attempt)

        for url, error in pending.items():
            outcomes.append(
                UrlOutcome(url=url, source=source, status="failed", error=error, attempts=attempt)
            )
            if save_policy == "failures" and captured_html.get(url):
                slug = dedup.job_slug(source, adapter.job_id_from_url(url), url)
                writer.write_html(slug, captured_html[url])

    for outcome in outcomes:
        if outcome.status == "failed":
            writer.write_dead_letter(outcome)
    return outcomes


async def crawl_run(
    request: RunRequest, runtime: Runtime, *, save_html: str | None = None
) -> CrawlReport:
    """Full ingestion run: route -> crawl -> extract -> stage. Never touches the DB."""
    registry = build_registry(runtime.sources_raw)
    grouped, unroutable = plan_urls(
        request.urls, registry, max_jobs_total=runtime.doc.run.max_jobs_total
    )
    save_policy = save_html or request.options.save_html or runtime.doc.run.save_html

    writer = StageWriter(runtime.staging_dir, request.run_id)
    writer.write_request(request)
    report = CrawlReport(run_id=request.run_id, run_dir=str(writer.run_dir), started_at=utcnow())

    for url, reason in unroutable:
        outcome = UrlOutcome(url=url, status="failed", error=reason)
        report.outcomes.append(outcome)
        writer.write_dead_letter(outcome)

    if grouped:
        c4a = _load_crawl4ai()
        for source, urls in grouped.items():
            adapter = registry[source]
            try:
                report.outcomes.extend(
                    await _crawl_group(c4a, runtime, adapter, urls, request, writer, save_policy)
                )
            except Exception as exc:  # noqa: BLE001 - one bad source must not kill the run
                log.exception("[%s] source group failed: %s", source, exc)
                for url in urls:
                    outcome = UrlOutcome(
                        url=url,
                        source=source,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    report.outcomes.append(outcome)
                    writer.write_dead_letter(outcome)
    else:
        log.error("no URLs could be routed to a configured source")

    report.finished_at = utcnow()
    report.recount()
    writer.write_manifest(
        report,
        extra={
            "state": request.state,
            "work_arrangement": request.work_arrangement,
            "expected_salary": (
                request.expected_salary.model_dump() if request.expected_salary else None
            ),
            "requested_urls": len(request.urls),
            "per_source_caps": {key: adapter.spec.max_jobs for key, adapter in registry.items()},
            "max_jobs_total": runtime.doc.run.max_jobs_total,
        },
    )
    return report


def crawl(request: RunRequest, runtime: Runtime, *, save_html: str | None = None) -> CrawlReport:
    """Blocking wrapper used by the CLI."""
    return asyncio.run(crawl_run(request, runtime, save_html=save_html))
