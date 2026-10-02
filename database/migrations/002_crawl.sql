-- ---------------------------------------------------------------------------
-- 002_crawl.sql - tables owned by the crawler agent (web-crawler/).
--
--   Applied by:  python -m jobfather_crawler db-init   (from web-crawler/)
--   Owner:       web-crawler  (the resume agent must NOT modify these)
--
-- Safe to re-run: every statement is IF NOT EXISTS.  Migrations are tracked in
-- schema_migrations, which db-init creates automatically.
-- ---------------------------------------------------------------------------

-- ------------------------------------------------------------------ jobs ----
CREATE TABLE IF NOT EXISTS jobs (
    id                        BIGSERIAL PRIMARY KEY,
    source                    TEXT        NOT NULL,
    source_job_id             TEXT,
    url                       TEXT        NOT NULL,
    url_hash                  TEXT        NOT NULL UNIQUE,
    title                     TEXT,
    company                   TEXT,
    location                  TEXT,
    salary                    TEXT,
    employment_type           TEXT,
    seniority                 TEXT,
    posted_date               DATE,
    description_markdown      TEXT,
    skills                    TEXT[]      NOT NULL DEFAULT '{}',
    -- run / request metadata (recorded per row, never used for discovery)
    run_id                    TEXT,
    state                     TEXT,
    work_arrangement          TEXT,
    expected_salary_min       NUMERIC,
    expected_salary_max       NUMERIC,
    expected_salary_currency  TEXT,
    -- change detection + vector
    content_hash              TEXT,
    embedding                 vector(1536),
    crawled_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW cosine index matches OpenAI text-embedding-3-small usage.
CREATE INDEX IF NOT EXISTS jobs_embedding_hnsw
    ON jobs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS jobs_source_idx  ON jobs (source);
CREATE INDEX IF NOT EXISTS jobs_posted_idx  ON jobs (posted_date DESC);
CREATE INDEX IF NOT EXISTS jobs_run_idx     ON jobs (run_id);

-- ------------------------------------------------------------ crawl_runs ----
CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id            TEXT PRIMARY KEY,
    state             TEXT,
    work_arrangement  TEXT,
    expected_salary   JSONB,
    url_count         INTEGER NOT NULL DEFAULT 0,
    staged            INTEGER NOT NULL DEFAULT 0,
    failed            INTEGER NOT NULL DEFAULT 0,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    config            JSONB
);

-- -------------------------------------------------------- crawl_failures ----
CREATE TABLE IF NOT EXISTS crawl_failures (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT,
    url         TEXT,
    source      TEXT,
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_failures_run_idx ON crawl_failures (run_id);