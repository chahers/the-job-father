"""Load a staged run into PostgreSQL + pgvector (idempotent upsert).

Split out from the crawler so a run can be re-loaded any number of times
without re-crawling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jobfather_crawler.dedup import normalize_url, url_hash
from jobfather_crawler.embeddings import embed_texts, embedding_text
from jobfather_crawler.logging_conf import get_logger
from jobfather_crawler.models import JobPosting
from jobfather_crawler.staging import load_staged_jobs

log = get_logger(__name__)

UPSERT_SQL = """
INSERT INTO jobs (
    source, source_job_id, url, url_hash, title, company, location, salary,
    employment_type, seniority, posted_date, description_markdown, skills,
    run_id, state, work_arrangement,
    expected_salary_min, expected_salary_max, expected_salary_currency,
    content_hash, embedding, crawled_at, updated_at
) VALUES (
    %(source)s, %(source_job_id)s, %(url)s, %(url_hash)s, %(title)s, %(company)s, %(location)s,
    %(salary)s, %(employment_type)s, %(seniority)s, %(posted_date)s, %(description_markdown)s,
    %(skills)s, %(run_id)s, %(state)s, %(work_arrangement)s,
    %(expected_salary_min)s, %(expected_salary_max)s, %(expected_salary_currency)s,
    %(content_hash)s, %(embedding)s::vector, %(crawled_at)s, now()
)
ON CONFLICT (url_hash) DO UPDATE SET
    source = EXCLUDED.source,
    source_job_id = EXCLUDED.source_job_id,
    title = EXCLUDED.title,
    company = EXCLUDED.company,
    location = EXCLUDED.location,
    salary = EXCLUDED.salary,
    employment_type = EXCLUDED.employment_type,
    seniority = EXCLUDED.seniority,
    posted_date = EXCLUDED.posted_date,
    description_markdown = EXCLUDED.description_markdown,
    skills = EXCLUDED.skills,
    run_id = EXCLUDED.run_id,
    state = EXCLUDED.state,
    work_arrangement = EXCLUDED.work_arrangement,
    expected_salary_min = EXCLUDED.expected_salary_min,
    expected_salary_max = EXCLUDED.expected_salary_max,
    expected_salary_currency = EXCLUDED.expected_salary_currency,
    content_hash = EXCLUDED.content_hash,
    embedding = COALESCE(EXCLUDED.embedding, jobs.embedding),
    crawled_at = EXCLUDED.crawled_at,
    updated_at = now()
RETURNING (xmax = 0) AS inserted
"""

RUN_SQL = """
INSERT INTO crawl_runs (run_id, state, work_arrangement, expected_salary, url_count, staged, failed,
                        started_at, finished_at, config)
VALUES (%(run_id)s, %(state)s, %(work_arrangement)s, %(expected_salary)s::jsonb, %(url_count)s,
        %(staged)s, %(failed)s, %(started_at)s, %(finished_at)s, %(config)s::jsonb)
ON CONFLICT (run_id) DO UPDATE SET
    staged = EXCLUDED.staged, failed = EXCLUDED.failed, finished_at = EXCLUDED.finished_at,
    url_count = EXCLUDED.url_count
"""

FAILURE_SQL = """
INSERT INTO crawl_failures (run_id, url, source, error, attempts)
VALUES (%(run_id)s, %(url)s, %(source)s, %(error)s, %(attempts)s)
"""


@dataclass
class LoadStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    embedded: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)


def apply_schema(dsn: str, sql_path: str | Path) -> None:
    """Run db/001_init.sql (needs CREATE EXTENSION privileges once)."""
    import psycopg

    statements = Path(sql_path).read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(statements)
    log.info("schema applied from %s", sql_path)


def _embedding_literal(vector: list[float] | None) -> str | None:
    """pgvector accepts a text literal like ``[0.1,0.2,...]``."""
    if not vector:
        return None
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


def _row(job: JobPosting, embedding: str | None) -> dict:
    salary = job.expected_salary
    return {
        "source": job.source,
        "source_job_id": job.source_job_id,
        "url": job.url,
        "url_hash": job.url_hash or url_hash(normalize_url(job.url)),
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "salary": job.salary,
        "employment_type": job.employment_type,
        "seniority": job.seniority,
        "posted_date": job.posted_date,
        "description_markdown": job.description_markdown,
        "skills": list(job.skills),
        "run_id": job.run_id,
        "state": job.state,
        "work_arrangement": job.work_arrangement,
        "expected_salary_min": salary.min if salary else None,
        "expected_salary_max": salary.max if salary else None,
        "expected_salary_currency": salary.currency if salary else None,
        "content_hash": job.content_hash,
        "embedding": embedding,
        "crawled_at": job.crawled_at,
    }


def load_run(
    dsn: str,
    run_dir: str | Path,
    *,
    embed: bool = True,
    api_key: str = "",
    embedding_model: str = "text-embedding-3-small",
) -> LoadStats:
    """Upsert every staged job in *run_dir* into Postgres."""
    import psycopg

    jobs = load_staged_jobs(run_dir)
    stats = LoadStats()
    if not jobs:
        log.warning("no staged jobs found in %s", run_dir)
        return stats

    embeddings: list[str | None] = [None] * len(jobs)
    if embed:
        try:
            vectors = embed_texts(
                [embedding_text(job) for job in jobs], model=embedding_model, api_key=api_key
            )
            embeddings = [_embedding_literal(vector) for vector in vectors]
            stats.embedded = len(vectors)
        except Exception as exc:  # noqa: BLE001 - loading must still succeed
            stats.errors.append(f"embedding failed: {exc}")
            log.error("embedding failed; loading without vectors: %s", exc)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for job, embedding in zip(jobs, embeddings):
                cur.execute(UPSERT_SQL, _row(job, embedding))
                row = cur.fetchone()
                if row and row[0]:
                    stats.inserted += 1
                else:
                    stats.updated += 1
        conn.commit()

    log.info(
        "loaded run: %d inserted, %d updated, %d embedded",
        stats.inserted,
        stats.updated,
        stats.embedded,
    )
    return stats


def record_failures(dsn: str, run_id: str, run_dir: str | Path) -> int:
    """Mirror the run's dead_letter.jsonl into the crawl_failures table."""
    import json

    import psycopg

    dead_letter = Path(run_dir) / "dead_letter.jsonl"
    if not dead_letter.exists():
        return 0

    rows = []
    for line in dead_letter.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(
            {
                "run_id": run_id,
                "url": payload.get("url"),
                "source": payload.get("source"),
                "error": payload.get("error"),
                "attempts": int(payload.get("attempts") or 0),
            }
        )

    if not rows:
        return 0
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(FAILURE_SQL, rows)
        conn.commit()
    log.info("recorded %d failure(s) for run %s", len(rows), run_id)
    return len(rows)


def record_run(
    dsn: str,
    *,
    run_id: str,
    state: str | None = None,
    work_arrangement: str | None = None,
    expected_salary: dict | None = None,
    url_count: int = 0,
    staged: int = 0,
    failed: int = 0,
    started_at=None,
    finished_at=None,
    config: dict | None = None,
) -> None:
    """Insert/update the crawl_runs audit row for a run."""
    import json

    import psycopg

    payload = {
        "run_id": run_id,
        "state": state,
        "work_arrangement": work_arrangement,
        "expected_salary": json.dumps(expected_salary) if expected_salary else None,
        "url_count": url_count,
        "staged": staged,
        "failed": failed,
        "started_at": started_at,
        "finished_at": finished_at,
        "config": json.dumps(config) if config else None,
    }
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(RUN_SQL, payload)
        conn.commit()
    log.info("recorded crawl_runs row for %s", run_id)