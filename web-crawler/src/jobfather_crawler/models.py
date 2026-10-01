"""Pydantic models for the run contract and the canonical staged job."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SavePolicy = Literal["never", "failures", "always"]
OutcomeStatus = Literal["staged", "failed", "skipped"]
WorkArrangement = Literal["remote", "hybrid", "onsite"]
SalaryPeriod = Literal["monthly", "annual", "daily"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_run_id() -> str:
    return utcnow().strftime("%Y%m%dT%H%M%SZ")


class ExpectedSalary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min: float | None = None
    max: float | None = None
    currency: str = "MYR"
    period: SalaryPeriod = "monthly"


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    force_refresh: bool = False
    max_concurrency: int | None = None
    save_html: SavePolicy = "failures"


class RunRequest(BaseModel):
    """The single, serialisable contract between the frontend/CLI and the crawler."""

    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(default_factory=default_run_id)
    state: str | None = None
    work_arrangement: WorkArrangement | None = None
    expected_salary: ExpectedSalary | None = None
    urls: list[str]
    options: RunOptions = Field(default_factory=RunOptions)

    @field_validator("urls")
    @classmethod
    def _clean_urls(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            url = (raw or "").strip()
            if not url or url.startswith("#"):
                continue
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError(f"not an http(s) URL: {url!r}")
            if url in seen:
                continue
            seen.add(url)
            cleaned.append(url)
        if not cleaned:
            raise ValueError("run request contains no usable URLs")
        return cleaned

    @classmethod
    def from_file(cls, path: str | Path) -> "RunRequest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class JobPosting(BaseModel):
    """Canonical staged job - the agreed ingestion schema."""

    model_config = ConfigDict(extra="ignore")

    source: str
    source_job_id: str | None = None
    url: str
    url_hash: str = ""
    title: str | None = None
    company: str | None = None
    location: str | None = None
    salary: str | None = None
    employment_type: str | None = None
    seniority: str | None = None
    posted_date: date | None = None
    description_markdown: str = ""
    skills: list[str] = Field(default_factory=list)

    # run / request metadata recorded on every row
    run_id: str | None = None
    state: str | None = None
    work_arrangement: str | None = None
    expected_salary: ExpectedSalary | None = None

    content_hash: str | None = None
    crawled_at: datetime = Field(default_factory=utcnow)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class UrlOutcome(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    source: str | None = None
    status: OutcomeStatus
    error: str | None = None
    attempts: int = 0
    slug: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CrawlReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    run_dir: str
    started_at: datetime
    finished_at: datetime | None = None
    outcomes: list[UrlOutcome] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)

    def recount(self) -> "CrawlReport":
        counts = {"staged": 0, "failed": 0, "skipped": 0}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        counts["total"] = len(self.outcomes)
        self.counts = counts
        return self

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
