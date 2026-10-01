"""Staging: write a whole run to disk before anything touches Postgres."""

from __future__ import annotations

import json
from pathlib import Path

from jobfather_crawler import markdown as md
from jobfather_crawler.dedup import job_slug
from jobfather_crawler.logging_conf import get_logger
from jobfather_crawler.models import CrawlReport, JobPosting, RunRequest, UrlOutcome

log = get_logger(__name__)


class StageWriter:
    """Owns the on-disk layout of a single run directory."""

    def __init__(self, base_dir: str | Path, run_id: str) -> None:
        self.run_id = run_id
        self.run_dir = Path(base_dir) / "runs" / run_id
        self.jobs_dir = self.run_dir / "jobs"
        self.html_dir = self.run_dir / "raw_html"
        self.dead_letter_path = self.run_dir / "dead_letter.jsonl"
        self.manifest_path = self.run_dir / "manifest.json"
        for directory in (self.run_dir, self.jobs_dir, self.html_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    def write_request(self, request: RunRequest) -> Path:
        path = self.run_dir / "run_request.json"
        path.write_text(request.to_json(), encoding="utf-8")
        return path

    def write_job(
        self,
        job: JobPosting,
        *,
        raw_html: str | None = None,
        save_html: str = "failures",
    ) -> str:
        slug = job_slug(job.source, job.source_job_id, job.url)
        (self.jobs_dir / f"{slug}.json").write_text(job.to_json(), encoding="utf-8")

        meta = job.to_dict()
        meta.pop("description_markdown", None)
        (self.jobs_dir / f"{slug}.md").write_text(
            md.assemble_document(meta, job.description_markdown), encoding="utf-8"
        )

        if raw_html and save_html == "always":
            (self.html_dir / f"{slug}.html").write_text(raw_html, encoding="utf-8")
        return slug

    def write_html(self, slug: str, raw_html: str) -> None:
        (self.html_dir / f"{slug}.html").write_text(raw_html, encoding="utf-8")

    def write_dead_letter(self, outcome: UrlOutcome) -> None:
        line = json.dumps(outcome.to_dict(), ensure_ascii=False)
        with self.dead_letter_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def write_manifest(self, report: CrawlReport, extra: dict | None = None) -> Path:
        payload = json.loads(report.to_json())
        if extra:
            payload["meta"] = extra
        self.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return self.manifest_path


def load_staged_jobs(run_dir: str | Path) -> list[JobPosting]:
    """Read back every ``jobs/*.json`` produced by a run."""
    jobs_dir = Path(run_dir) / "jobs"
    if not jobs_dir.exists():
        raise FileNotFoundError(f"no jobs directory in {run_dir}")
    jobs: list[JobPosting] = []
    for path in sorted(jobs_dir.glob("*.json")):
        jobs.append(JobPosting.model_validate_json(path.read_text(encoding="utf-8")))
    return jobs


def find_latest_run(base_dir: str | Path) -> str | None:
    runs_dir = Path(base_dir) / "runs"
    if not runs_dir.exists():
        return None
    runs = sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
    return runs[-1].name if runs else None
