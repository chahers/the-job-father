import json

from jobfather_crawler.models import (
    CrawlReport,
    ExpectedSalary,
    JobPosting,
    RunRequest,
    UrlOutcome,
    utcnow,
)
from jobfather_crawler.staging import StageWriter, find_latest_run, load_staged_jobs

RUN_ID = "20260101T000000Z"
REQUEST = RunRequest(run_id=RUN_ID, state="Selangor", urls=["https://x.com/jobs/1"])


def _job(**overrides) -> JobPosting:
    data = {
        "source": "hiredly",
        "source_job_id": "abc-1",
        "url": "https://x.com/jobs/1",
        "url_hash": "h" * 64,
        "title": "Engineer",
        "company": "Acme",
        "description_markdown": "# Role\n\n" + "x" * 200,
        "run_id": RUN_ID,
        "state": "Selangor",
        "work_arrangement": "hybrid",
        "expected_salary": ExpectedSalary(min=1, max=2),
        "content_hash": "c" * 64,
    }
    data.update(overrides)
    return JobPosting(**data)


def test_write_job_and_reload(tmp_path):
    writer = StageWriter(tmp_path, RUN_ID)
    slug = writer.write_job(_job(), raw_html="<html></html>", save_html="never")

    assert slug == "hiredly-abc-1"
    assert (writer.jobs_dir / f"{slug}.json").exists()
    document = (writer.jobs_dir / f"{slug}.md").read_text(encoding="utf-8")
    assert document.startswith("---\n")
    assert "title: Engineer" in document
    assert not (writer.html_dir / f"{slug}.html").exists()

    jobs = load_staged_jobs(writer.run_dir)
    assert len(jobs) == 1
    assert jobs[0].title == "Engineer"
    assert jobs[0].description_markdown.startswith("# Role")


def test_save_html_always(tmp_path):
    writer = StageWriter(tmp_path, RUN_ID)
    slug = writer.write_job(_job(), raw_html="<html>hi</html>", save_html="always")
    saved = (writer.html_dir / f"{slug}.html").read_text(encoding="utf-8")
    assert saved == "<html>hi</html>"


def test_dead_letter_and_manifest(tmp_path):
    writer = StageWriter(tmp_path, RUN_ID)
    writer.write_dead_letter(
        UrlOutcome(url="https://x.com/1", source="hiredly", status="failed", error="404", attempts=2)
    )
    lines = writer.dead_letter_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["error"] == "404"
    assert payload["attempts"] == 2

    report = CrawlReport(run_id=RUN_ID, run_dir=str(writer.run_dir), started_at=utcnow())
    report.recount()
    path = writer.write_manifest(report, extra={"foo": "bar"})
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["meta"]["foo"] == "bar"
    assert manifest["counts"]["total"] == 0


def test_write_request(tmp_path):
    writer = StageWriter(tmp_path, RUN_ID)
    path = writer.write_request(REQUEST)
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == RUN_ID


def test_find_latest_run(tmp_path):
    StageWriter(tmp_path, "20260101T000000Z")
    StageWriter(tmp_path, "20260202T000000Z")
    assert find_latest_run(tmp_path) == "20260202T000000Z"
    assert find_latest_run(tmp_path / "does-not-exist") is None
