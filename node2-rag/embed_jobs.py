"""Fill ``jobs.embedding`` for rows that need it.

This is the ONE thing node 2 is allowed to write in the crawler's table: the
``embedding`` column.  Nothing else there is ever touched - in particular
``updated_at`` is left alone, because the crawler uses it as "the posting last
changed" and a backfill is not a change to the posting.

Which rows are selected (default)
---------------------------------
* ``embedding IS NULL``  - the agreed contract: the crawler never inserts a row
  that already has a vector, so these are rows the crawler legitimately left for
  node 2 (it was run with ``--no-embed``, or an embedding call failed).
* a row node 2 has embedded before whose ``content_hash`` has since changed.

That second case exists because of a real gap in the crawler loader: it upserts
``embedding = COALESCE(EXCLUDED.embedding, jobs.embedding)``, so when a posting's
text changes and the crawler runs without embeddings, the OLD vector survives and
silently describes stale text.  ``job_embedding_state`` (node 2's own table)
records the ``content_hash`` each vector was computed from, so this command can
notice that.  Vectors the crawler wrote itself have no state row and are therefore
never second-guessed.

``--only-missing`` restores the narrow contract (``embedding IS NULL`` only).

Usage (from node2-rag/)
-----------------------
    .\\.venv\\Scripts\\python.exe embed_jobs.py --dry-run          # what/who/how much, no writes
    .\\.venv\\Scripts\\python.exe embed_jobs.py --job-id 42        # one row
    .\\.venv\\Scripts\\python.exe embed_jobs.py --limit 5 --fake-embeddings   # plumbing test
    .\\.venv\\Scripts\\python.exe embed_jobs.py                    # all pending rows
"""

from __future__ import annotations

import argparse
import sys

from config import (
    PRICE_PER_1M_TOKENS,
    ConfigError,
    check_vector_extension,
    connect,
    embed_texts,
    estimate_cost_usd,
    estimate_tokens,
    job_embedding_text,
    load_settings,
    redact,
    require_tables,
    vector_literal,
)
from ingest_resume import FAKE_MODEL

SELECT_COLUMNS = """
    j.id, j.url_hash, j.title, j.company, j.location, j.seniority, j.skills,
    j.description_markdown, j.content_hash,
    (j.embedding IS NULL) AS embedding_missing,
    s.content_hash AS state_content_hash,
    s.model        AS state_model
"""

# Only rows the crawler left empty, plus rows node 2 itself embedded whose text
# changed since - or which hold dummy test vectors (s.model = the fake model), so
# a real run always replaces them.  Both signals come from node 2's own table,
# never from the crawler's data.
PENDING_WHERE = """
    j.embedding IS NULL
    OR (
        s.url_hash IS NOT NULL
        AND (s.content_hash IS DISTINCT FROM j.content_hash OR s.model = %s)
    )
"""

MISSING_ONLY_WHERE = "j.embedding IS NULL"


def select_jobs(
    conn, *, only_missing: bool = False, job_id: int | None = None, limit: int | None = None
) -> list[dict]:
    """Jobs that need a vector, oldest id first (stable, resumable order)."""
    from psycopg.rows import dict_row

    params: list = []
    if job_id is not None:
        where = "j.id = %s"
        params.append(job_id)
    elif only_missing:
        where = MISSING_ONLY_WHERE
    else:
        where = PENDING_WHERE
        params.append(FAKE_MODEL)

    sql = f"""
        SELECT {SELECT_COLUMNS}
        FROM jobs j
        LEFT JOIN job_embedding_state s ON s.url_hash = j.url_hash
        WHERE {where}
        ORDER BY j.id
    """
    if limit:
        sql += "\nLIMIT %s"
        params.append(limit)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def reason(row: dict) -> str:
    """Why this row was selected (for the report)."""
    if row.get("embedding_missing"):
        return "no vector"
    if row.get("state_model") == FAKE_MODEL:
        return "dummy test vector"
    return "text changed since node 2 embedded it"


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
# content_hash is re-checked in the WHERE clause: if the crawler re-loaded the
# posting between our SELECT and this UPDATE, the row no longer matches and we
# skip it rather than storing a vector for text that is already out of date.
UPDATE_EMBEDDING_SQL = """
UPDATE jobs
   SET embedding = %s::vector
 WHERE id = %s
   AND content_hash IS NOT DISTINCT FROM %s
"""

STATE_SQL = """
INSERT INTO job_embedding_state (url_hash, content_hash, model, embedded_at)
VALUES (%s, %s, %s, now())
ON CONFLICT (url_hash) DO UPDATE SET
    content_hash = EXCLUDED.content_hash,
    model        = EXCLUDED.model,
    embedded_at  = now()
"""


def write_vectors(conn, rows, vectors, *, model: str, commit_every: int = 100) -> tuple[int, int]:
    """Write one vector per row; returns ``(written, skipped)``.

    *skipped* counts rows whose text changed underneath us (or has no url_hash).
    """
    written = skipped = 0
    with conn.cursor() as cur:
        for index, (row, vector) in enumerate(zip(rows, vectors), start=1):
            url_hash = row.get("url_hash")
            if not url_hash:
                skipped += 1  # without url_hash there is nothing to track
                continue
            cur.execute(
                UPDATE_EMBEDDING_SQL,
                (vector_literal(vector), row["id"], row.get("content_hash")),
            )
            if cur.rowcount != 1:
                skipped += 1
                continue
            cur.execute(STATE_SQL, (url_hash, row.get("content_hash"), model))
            written += 1
            if commit_every and index % commit_every == 0:
                conn.commit()
        conn.commit()
    return written, skipped


def describe(rows: list[dict], preview: int = 8) -> None:
    """Print what is about to be embedded, with a token/cost preview."""
    missing = sum(1 for row in rows if row.get("embedding_missing"))
    stale = sum(1 for row in rows if not row.get("embedding_missing"))
    print(f"\n{len(rows)} job(s) need a vector: {missing} without one, {stale} stale/dummy")

    texts = [job_embedding_text(row) for row in rows]
    tokens = sum(estimate_tokens(text) for text in texts)
    print(
        f"estimated input: ~{tokens} tokens ~= ${estimate_cost_usd(tokens):.4f} "
        f"at ${PRICE_PER_1M_TOKENS}/1M"
    )

    print()
    for row in rows[:preview]:
        title = (row.get("title") or "(no title)")[:52]
        company = (row.get("company") or "")[:24]
        print(f"  #{row['id']:<6} {title:<52} {company:<24} [{reason(row)}]")
    if len(rows) > preview:
        print(f"  ... and {len(rows) - preview} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="embed_jobs.py",
        description="Fill jobs.embedding for rows the crawler left empty (node 2's only write in `jobs`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  embed_jobs.py --dry-run          show the work + cost estimate, write nothing\n"
            "  embed_jobs.py --limit 5 --fake-embeddings       plumbing test\n"
            "  embed_jobs.py --job-id 42       backfill exactly one posting\n"
            "  embed_jobs.py --only-missing    strict contract: embedding IS NULL only\n"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="report only; no API calls, no writes")
    parser.add_argument(
        "--fake-embeddings",
        action="store_true",
        help="dummy vectors (no API key); they are marked as dummy and replaced on a real run",
    )
    parser.add_argument("--only-missing", action="store_true", help="ignore stale rows")
    parser.add_argument("--job-id", type=int, help="backfill a single posting")
    parser.add_argument("--limit", type=int, metavar="N", help="at most N postings this run")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        print(f"database : {redact(settings.dsn)}")
        print(f"model    : {settings.embedding_model}")

        with connect(settings) as conn:
            check_vector_extension(conn)
            require_tables(conn, "jobs", "job_embedding_state")

            rows = select_jobs(
                conn,
                only_missing=args.only_missing,
                job_id=args.job_id,
                limit=args.limit,
            )
            if not rows:
                print("\nnothing to do: every selected job already has a current vector.")
                return 0

            describe(rows)

            if args.dry_run:
                print("\n[dry run] nothing was written.")
                return 0

            if not args.fake_embeddings and not settings.has_api_key:
                load_settings(require_key=True)  # raises ConfigError with the fix

            print(f"\nembedding {len(rows)} job(s)...")

            def progress(done: int, total: int) -> None:
                print(f"  {done}/{total}", end="\r")

            vectors = embed_texts(
                [job_embedding_text(row) for row in rows],
                settings,
                fake=args.fake_embeddings,
                progress=progress,
            )
            print()

            model = FAKE_MODEL if args.fake_embeddings else settings.embedding_model
            written, skipped = write_vectors(conn, rows, vectors, model=model)

        print(f"\n{written} row(s) updated, {skipped} skipped (text changed mid-run)")
        if args.fake_embeddings:
            print(
                f"[!] DUMMY vectors written and marked '{FAKE_MODEL}'; a real run will\n"
                "    pick them up again, so nothing is left silently wrong."
            )
        return 0

    except ConfigError as exc:
        print(f"\n[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())