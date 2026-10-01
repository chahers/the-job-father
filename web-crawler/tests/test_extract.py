import json
from datetime import date, datetime, timedelta, timezone

from conftest import FIXTURES

from jobfather_crawler import extract
from jobfather_crawler.models import ExpectedSalary, RunRequest

REQUEST = RunRequest(
    run_id="test-run",
    state="Kuala Lumpur",
    work_arrangement="hybrid",
    expected_salary=ExpectedSalary(min=6000, max=9000),
    urls=["https://my.hiredly.com/jobs/acme-senior-software-engineer"],
)

URL = "https://my.hiredly.com/jobs/acme-senior-software-engineer"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_finds_jobposting_json_ld():
    node = extract.find_job_posting_ld(_html("hiredly_job.html"))
    assert node is not None
    assert node["title"] == "Senior Software Engineer"


def test_returns_none_without_json_ld():
    assert extract.find_job_posting_ld(_html("challenge_page.html")) is None


def test_ld_to_fields_maps_core_fields():
    fields = extract.ld_to_fields(extract.find_job_posting_ld(_html("hiredly_job.html")))
    assert fields["title"] == "Senior Software Engineer"
    assert fields["company"] == "Acme Tech Sdn Bhd"
    assert fields["location"] == "Kuala Lumpur, WP Kuala Lumpur, MY"
    assert fields["salary"] == "MYR 9,000 - 13,000 / month"
    assert fields["employment_type"] == "FULL_TIME"
    assert fields["source_job_id"] == "ACME-12345"
    assert "Python" in fields["skills"]


def test_build_job_posting_from_json_ld():
    fields = extract.ld_to_fields(extract.find_job_posting_ld(_html("hiredly_job.html")))
    job = extract.build_job_posting(
        fields, url=URL, source="hiredly", request=REQUEST, source_job_id="acme-senior-software-engineer"
    )
    assert job.title == "Senior Software Engineer"
    assert job.company == "Acme Tech Sdn Bhd"
    assert job.posted_date == date(2026, 9, 25)
    assert job.seniority == "Senior"
    assert job.state == "Kuala Lumpur"
    assert job.work_arrangement == "hybrid"
    assert job.expected_salary.min == 6000
    assert job.url_hash
    assert job.content_hash
    assert "## About the role" in job.description_markdown
    assert extract.validate_posting(job) is None


def test_challenge_page_is_rejected():
    fields = extract.ld_to_fields({"@type": "JobPosting"})  # empty-ish
    job = extract.build_job_posting(fields, url=URL, source="hiredly", request=REQUEST)
    reason = extract.validate_posting(job)
    assert reason is not None
    assert "title" in reason


def test_parse_posted_date_variants():
    assert extract.parse_posted_date("2026-09-25") == date(2026, 9, 25)
    assert extract.parse_posted_date("2026-09-25T00:00:00.000Z") == date(2026, 9, 25)
    today = datetime.now(timezone.utc).date()
    assert extract.parse_posted_date("3 days ago") == today - timedelta(days=3)
    assert extract.parse_posted_date("30+ days ago") == today - timedelta(days=30)
    assert extract.parse_posted_date("Posted 2 weeks ago") == today - timedelta(weeks=2)
    assert extract.parse_posted_date("yesterday") == today - timedelta(days=1)
    assert extract.parse_posted_date("not a date") is None


def test_infer_seniority():
    assert extract.infer_seniority("Senior Software Engineer") == "Senior"
    assert extract.infer_seniority("Junior Developer") == "Junior"
    assert extract.infer_seniority("Backend Engineer") is None


def test_extracted_to_fields_from_json_string():
    payload = json.dumps(
        {"title": "Data Engineer", "company": "Beta", "description_html": "<p>hello world</p>"}
    )
    fields = extract.extracted_to_fields(payload)
    assert fields["title"] == "Data Engineer"
    assert fields["company"] == "Beta"
    assert fields["description_html"] == "<p>hello world</p>"


def test_extracted_to_fields_handles_list_and_garbage():
    assert extract.extracted_to_fields('[{"title": "Only"}]')["title"] == "Only"
    assert extract.extracted_to_fields("not json") == {}
    assert extract.extracted_to_fields(None) == {}
