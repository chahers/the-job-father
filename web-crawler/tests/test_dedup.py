from jobfather_crawler import dedup


def test_normalize_strips_tracking_and_www():
    messy = "https://www.Hiredly.com/jobs/acme-software-engineer-123/?utm_source=x&utm_campaign=y#apply"
    assert dedup.normalize_url(messy) == "https://hiredly.com/jobs/acme-software-engineer-123"


def test_normalize_keeps_meaningful_query_sorted():
    url = "https://malaysia.indeed.com/viewjob?jk=abc123&from=serp&vjs=3"
    assert dedup.normalize_url(url) == "https://malaysia.indeed.com/viewjob?jk=abc123&vjs=3"


def test_url_hash_is_stable_across_variants():
    a = "https://my.jobstreet.com/job/91234567?type=standard"
    b = "https://www.my.jobstreet.com/job/91234567/"
    assert dedup.url_hash(a) == dedup.url_hash(b)


def test_url_hash_differs_for_different_jobs():
    assert dedup.url_hash("https://my.jobstreet.com/job/1") != dedup.url_hash(
        "https://my.jobstreet.com/job/2"
    )


def test_content_hash_ignores_whitespace_and_case():
    a = dedup.content_hash("Senior Engineer", "Acme", "Build  APIs\n\nwith Python")
    b = dedup.content_hash("senior engineer", "acme", "build apis with python")
    assert a == b


def test_job_slug_prefers_source_job_id():
    slug = dedup.job_slug("jobstreet", "91234567", "https://my.jobstreet.com/job/91234567")
    assert slug == "jobstreet-91234567"


def test_job_slug_falls_back_to_url_hash():
    slug = dedup.job_slug("hiredly", None, "https://hiredly.com/jobs/x")
    assert slug.startswith("hiredly-")
    assert len(slug) > len("hiredly-")
