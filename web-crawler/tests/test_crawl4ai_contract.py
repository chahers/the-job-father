"""Contract test for the Crawl4AI API surface this project wires against.

Skipped automatically when crawl4ai is not installed, so the rest of the suite
stays runnable in a bare environment.
"""

import pytest

crawl4ai = pytest.importorskip("crawl4ai")

REQUIRED_SYMBOLS = (
    "AsyncWebCrawler",
    "BrowserConfig",
    "CrawlerRunConfig",
    "CacheMode",
    "MemoryAdaptiveDispatcher",
    "RateLimiter",
)


def test_required_symbols_exist():
    missing = [name for name in REQUIRED_SYMBOLS if not hasattr(crawl4ai, name)]
    assert missing == []


def test_browser_config_accepts_cdp_kwargs():
    config = crawl4ai.BrowserConfig(
        browser_type="chromium",
        viewport_width=1366,
        viewport_height=900,
        verbose=False,
        headless=False,
        browser_mode="custom",
        use_managed_browser=True,
        cdp_url="http://localhost:9222",
    )
    assert config.cdp_url == "http://localhost:9222"


def test_browser_config_accepts_managed_profile_kwargs():
    config = crawl4ai.BrowserConfig(
        browser_type="chromium",
        viewport_width=1366,
        viewport_height=900,
        verbose=False,
        headless=False,
        use_managed_browser=True,
        user_data_dir=".profiles/test",
    )
    assert config.use_managed_browser is True


def test_run_config_accepts_our_kwargs():
    from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    schema = {
        "name": "S",
        "baseSelector": "body",
        "fields": [{"name": "title", "selector": "h1", "type": "text"}],
    }
    config = crawl4ai.CrawlerRunConfig(
        cache_mode=crawl4ai.CacheMode.BYPASS,
        page_timeout=45000,
        wait_until="domcontentloaded",
        wait_for="h1",
        js_code=None,
        extraction_strategy=JsonCssExtractionStrategy(schema),
        markdown_generator=DefaultMarkdownGenerator(),
        excluded_tags=["nav", "footer"],
        locale="en-MY",
        timezone_id="Asia/Kuala_Lumpur",
        check_robots_txt=True,
        screenshot=False,
        verbose=False,
    )
    assert config.page_timeout == 45000


def test_dispatchers_accept_our_kwargs():
    rate_limiter = crawl4ai.RateLimiter(
        base_delay=(1.0, 3.0), max_delay=60.0, max_retries=3, rate_limit_codes=[429, 503]
    )
    monitor = crawl4ai.CrawlerMonitor()
    assert (
        crawl4ai.MemoryAdaptiveDispatcher(
            memory_threshold_percent=75.0, rate_limiter=rate_limiter, monitor=monitor
        )
        is not None
    )
    assert (
        crawl4ai.SemaphoreDispatcher(
            max_session_permit=2, rate_limiter=rate_limiter, monitor=monitor
        )
        is not None
    )


def test_pruning_content_filter_is_available():
    from crawl4ai.content_filter_strategy import (
        PruningContentFilterLXML as PruningContentFilter,
    )

    assert PruningContentFilter(threshold=0.48) is not None


def test_project_wiring_builds_against_installed_crawl4ai():
    """The exact configs the crawler builds must be accepted by crawl4ai."""
    from jobfather_crawler.config import load_runtime
    from jobfather_crawler.crawler import _browser_config, _dispatcher, _load_crawl4ai, _run_config
    from jobfather_crawler.models import RunRequest
    from jobfather_crawler.sources import build_registry

    runtime = load_runtime()
    c4a = _load_crawl4ai()
    registry = build_registry(runtime.sources_raw)
    request = RunRequest(run_id="contract", urls=["https://my.hiredly.com/jobs/x"])

    for name in ("hiredly", "jobstreet", "linkedin", "indeed"):
        adapter = registry[name]
        assert _browser_config(c4a, runtime, adapter) is not None
        assert _run_config(c4a, runtime, adapter, request) is not None
        assert _dispatcher(c4a, runtime, adapter) is not None
