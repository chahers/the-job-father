from jobfather_crawler.registry import plan_urls, resolve_source
from jobfather_crawler.sources import build_registry

SOURCES = {
    "hiredly": {"url_patterns": ["hiredly.com"], "max_jobs": 2, "max_concurrency": 8},
    "jobstreet": {
        "url_patterns": ["jobstreet.com"],
        "max_jobs": 3,
        "needs_cdp": True,
        "dispatcher": "semaphore",
    },
}


def _registry():
    return build_registry(SOURCES)


def test_resolve_source():
    registry = _registry()
    assert resolve_source("https://my.hiredly.com/jobs/x", registry) == "hiredly"
    assert resolve_source("https://my.jobstreet.com/job/1", registry) == "jobstreet"
    assert resolve_source("https://example.com/job/1", registry) is None


def test_spec_flags_are_parsed():
    adapter = _registry()["jobstreet"]
    assert adapter.needs_cdp is True
    assert adapter.spec.dispatcher == "semaphore"
    assert adapter.spec.max_jobs == 3


def test_parse_spec_maps_yaml_schema_key():
    registry = build_registry(
        {
            "demo": {
                "url_patterns": ["x.com"],
                "schema": {"name": "S", "baseSelector": "body", "fields": []},
            }
        }
    )
    assert registry["demo"].spec.css_schema["name"] == "S"


def test_job_id_from_url_per_source():
    registry = build_registry(
        {
            "jobstreet": {"url_patterns": ["jobstreet.com"]},
            "indeed": {"url_patterns": ["indeed.com"]},
            "linkedin": {"url_patterns": ["linkedin.com"]},
            "hiredly": {"url_patterns": ["hiredly.com"]},
        }
    )
    assert (
        registry["jobstreet"].job_id_from_url("https://my.jobstreet.com/job/91234567?type=standard")
        == "91234567"
    )
    assert (
        registry["indeed"].job_id_from_url("https://malaysia.indeed.com/viewjob?jk=abc123&from=serp")
        == "abc123"
    )
    assert (
        registry["linkedin"].job_id_from_url("https://www.linkedin.com/jobs/view/4123456789/")
        == "4123456789"
    )
    assert registry["hiredly"].job_id_from_url("https://my.hiredly.com/jobs/acme-engineer-1") == (
        "acme-engineer-1"
    )


def test_plan_urls_enforces_per_source_cap():
    grouped, unroutable = plan_urls(
        [f"https://hiredly.com/jobs/{i}" for i in range(5)], _registry(), max_jobs_total=250
    )
    assert len(grouped["hiredly"]) == 2
    assert unroutable == []


def test_plan_urls_enforces_global_ceiling():
    urls = [f"https://hiredly.com/jobs/{i}" for i in range(2)]
    urls += [f"https://my.jobstreet.com/job/{i}" for i in range(3)]
    grouped, _ = plan_urls(urls, _registry(), max_jobs_total=3)
    assert len(grouped["hiredly"]) == 2
    assert len(grouped["jobstreet"]) == 1


def test_plan_urls_reports_unroutable():
    grouped, unroutable = plan_urls(["https://example.com/x"], _registry())
    assert grouped == {}
    assert len(unroutable) == 1
    assert unroutable[0][0] == "https://example.com/x"
