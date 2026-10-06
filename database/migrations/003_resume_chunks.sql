-- ---------------------------------------------------------------------------
-- 003_resume_chunks.sql - tables owned by the resume RAG agent (node2-rag/).
--
--   Applied by:  python -m jobfather_crawler db-init   (from web-crawler/)
--                python db_init.py                     (from node2-rag/)
--   Owner:       node2-rag  (no other agent may modify these tables)
--
--   resume_chunks        master-resume experience split into retrievable chunks
--   job_embedding_state  bookkeeping for the one `jobs` column node 2 writes
--
-- Safe to re-run: every statement is IF NOT EXISTS, and migrations are tracked in
-- schema_migrations so each file is executed exactly once.
--
-- NOTE ON `jobs` OWNERSHIP
--   node 2 only ever writes the `embedding` column of `jobs`.  The crawler fills
--   that column at insert time and does NOT reset it to NULL when a posting's
--   text changes (see web-crawler/.../loader.py: COALESCE(EXCLUDED.embedding,
--   jobs.embedding)).  job_embedding_state therefore records which content_hash
--   the vector in `jobs.embedding` was computed from, so a backfill can tell a
--   *stale* vector from a *missing* one without touching the crawler's table.
-- ---------------------------------------------------------------------------

-- pgvector is shared (001_extensions.sql); this is a no-op once it is installed.
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------- resume_chunks ----
CREATE TABLE IF NOT EXISTS resume_chunks (
    id              BIGSERIAL PRIMARY KEY,
    source_file     TEXT        NOT NULL,
    section         TEXT        NOT NULL DEFAULT '',
    chunk_index     INTEGER     NOT NULL DEFAULT 0,
    chunk_type      TEXT,
    organisation    TEXT,
    skills          TEXT[]      NOT NULL DEFAULT '{}',
    content         TEXT        NOT NULL,
    content_hash    TEXT        NOT NULL,
    embedding       vector(1536),
    embedding_model TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- one row per (file, chunk text): re-running the ingest updates in place
    CONSTRAINT resume_chunks_source_hash_key UNIQUE (source_file, content_hash)
);

-- HNSW cosine index matches OpenAI text-embedding-3-small usage (same as jobs).
CREATE INDEX IF NOT EXISTS resume_chunks_embedding_hnsw
    ON resume_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS resume_chunks_source_idx ON resume_chunks (source_file);
-- lets the retriever use the `skills && ...` overlap boost without a seq scan
CREATE INDEX IF NOT EXISTS resume_chunks_skills_idx
    ON resume_chunks USING gin (skills);

-- ---------------------------------------------------- job_embedding_state ----
CREATE TABLE IF NOT EXISTS job_embedding_state (
    url_hash     TEXT PRIMARY KEY,
    content_hash TEXT,
    model        TEXT NOT NULL,
    embedded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);