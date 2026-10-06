"""Cosine retrieval over ``resume_chunks`` for one job posting (node 3's input).

Contract with node 3 (Writer)
-----------------------------
:func:`retrieve_for_job` / :func:`retrieve` return a plain list of dicts, in rank
order, each with ``content`` (self-contained chunk text), ``source_file``,
``section``, ``score`` and friends.  Nothing here depends on OpenAI being
reachable at query time beyond the single query embedding.

Ranking
-------
1. ``ORDER BY embedding <=> query_vector`` (pgvector cosine distance over the HNSW
   index) picks ``--candidates`` rows (default ``4 * top_k``).
2. An optional, small skills boost re-ranks those candidates in Python: for every
   job skill that also appears in the chunk's ``skills`` array (case-insensitive),
   ``boost_weight`` is added, capped at ``max_boost``.  Cosine scores sit in
   ``[0, 1]``, so a small additive term nudges near-ties - it never dominates.

    .\\.venv\\Scripts\\python.exe retriever.py --job-id 42 --json
    .\\.venv\\Scripts\\python.exe retriever.py --latest --top-k 8
    .\\.venv\\Scripts\\python.exe retriever.py --text "AI Engineer, RAG, Python, prompt engineering"
"""

from __future__ import annotations

import argparse
import json
import sys

from config import (
    DEFAULT_TOP_K,
    ConfigError,
    check_vector_extension,
    connect,
    embed_texts,
    field,
    job_embedding_text,
    load_settings,
    prepare,
    redact,
    require_tables,
    vector_literal,
)
from ingest_resume import FAKE_MODEL

# Small additive boost per matching skill; 0 disables the boost entirely.
DEFAULT_SKILLS_BOOST = 0.03
# Ceiling for the total boost, so a chunk cannot win on skills alone.
DEFAULT_MAX_BOOST = 0.15
# Rows pulled from the index before re-ranking (more candidates == better recall).
CANDIDATE_MULTIPLIER = 4

JOB_COLUMNS = """
    id, url_hash, title, company, location, seniority, skills,
    description_markdown, content_hash, embedding IS NOT NULL AS has_embedding
"""

CHUNK_COLUMNS = """
    id, source_file, section, chunk_index, chunk_type, organisation, skills,
    content, embedding_model
"""


def fetch_job_by_id(conn, job_id: int) -> dict:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    if row is None:
        raise ConfigError(f"no job with id={job_id}")
    return dict(row)


def fetch_latest_job(conn) -> dict:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {JOB_COLUMNS} FROM jobs ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    if row is None:
        raise ConfigError("the jobs table is empty - crawl or load a posting first")
    return dict(row)


def search_chunks(conn, vector_literal_value: str, candidates: int) -> list[dict]:
    """The vector search itself: cosine distance, nearest first."""
    from psycopg.rows import dict_row

    sql = f"""
        SELECT {CHUNK_COLUMNS},
               1 - (embedding <=> %s::vector) AS score
        FROM resume_chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (vector_literal_value, vector_literal_value, candidates))
        return [dict(row) for row in cur.fetchall()]


def normalise_skills(values) -> set[str]:
    """Lower-cased, trimmed skill names for comparison."""
    if not values:
        return set()
    return {str(value).strip().lower() for value in values if str(value).strip()}


def rerank(
    candidates: list[dict],
    job_skills,
    *,
    weight: float = DEFAULT_SKILLS_BOOST,
    max_boost: float = DEFAULT_MAX_BOOST,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    """Add the skills bonus, sort, and cut to *top_k*.

    Every candidate keeps both numbers: ``score`` (pure cosine similarity, so you
    can see what the vector search thought) and ``final_score`` (what decided the
    order).  Ties break on ``source_file`` then ``chunk_index`` so output is stable.
    """
    wanted = normalise_skills(job_skills)
    for candidate in candidates:
        matched = sorted(wanted & normalise_skills(candidate.get("skills"))) if wanted else []
        candidate["matched_skills"] = matched
        candidate["boost"] = (
            min(weight * len(matched), max_boost) if (weight > 0 and wanted) else 0.0
        )
        candidate["score"] = round(float(candidate["score"]), 6)
        candidate["final_score"] = round(candidate["score"] + candidate["boost"], 6)

    candidates.sort(
        key=lambda c: (-c["final_score"], c["source_file"], c["chunk_index"])
    )
    return candidates[:top_k]


def retrieve(
    conn,
    settings,
    *,
    job=None,
    query_text: str | None = None,
    top_k: int | None = None,
    candidates: int | None = None,
    skills_boost: float = DEFAULT_SKILLS_BOOST,
    max_boost: float = DEFAULT_MAX_BOOST,
    fake: bool = False,
) -> list[dict]:
    """Rank resume chunks against a job.  Pass either *job* or *query_text*.

    This is the function node 3 (Writer) should call; it needs exactly one query
    embedding per job, then one SQL round-trip.
    """
    top_k = top_k or settings.top_k

    if job is not None:
        text = job_embedding_text(job)
        job_skills = list(field(job, "skills") or [])
    else:
        text = prepare(query_text)
        job_skills = []

    if not text.strip():
        raise ConfigError(
            "nothing to search for - pass --job-id / --latest, or --text with a job description"
        )

    query_vector = embed_texts([text], settings, fake=fake)[0]
    pool_size = candidates or max(top_k * CANDIDATE_MULTIPLIER, top_k)
    pool = search_chunks(conn, vector_literal(query_vector), pool_size)
    return rerank(
        pool, job_skills, weight=skills_boost, max_boost=max_boost, top_k=top_k
    )


def retrieve_for_job(conn, job, settings=None, **kwargs) -> list[dict]:
    """Convenience wrapper: a ``jobs`` row (dict) -> ranked chunks."""
    return retrieve(conn, settings or load_settings(), job=job, **kwargs)


def retrieve_for_job_id(conn, job_id: int, settings=None, **kwargs) -> list[dict]:
    """Convenience wrapper: a job id -> ranked chunks."""
    return retrieve(
        conn, settings or load_settings(), job=fetch_job_by_id(conn, job_id), **kwargs
    )


def dummy_vector_rows(results: list[dict]) -> int:
    """How many results carry test vectors, so the CLI can warn about them."""
    return sum(
        1 for row in results if row.get("embedding_model") == FAKE_MODEL
    )


def render(results: list[dict], *, job=None, full_text: bool = False) -> str:
    """Human-readable ranking, the shape node 3 will receive."""
    lines: list[str] = []
    if job is not None:
        lines.append(
            f"job      : #{field(job, 'id')} {field(job, 'title')} @ {field(job, 'company')}"
        )
        skills = field(job, "skills") or []
        if skills:
            lines.append(f"skills   : {', '.join(map(str, skills))}")
    if not results:
        lines.append("(no chunks matched - is resume_chunks empty or unembedded?)")
        return "\n".join(lines)

    lines.append("")
    for rank, chunk in enumerate(results, 1):
        matched = ", ".join(chunk["matched_skills"]) or "-"
        lines.append(
            f"{rank:>2}. score={chunk['final_score']:.4f} "
            f"(cosine={chunk['score']:.4f} +boost={chunk['boost']:.2f})"
        )
        lines.append(
            f"    {chunk['source_file']} | {chunk['section']} | chunk #{chunk['chunk_index']}"
        )
        lines.append(f"    matched skills: {matched}")
        body = chunk["content"]
        if not full_text:
            body = body if len(body) <= 220 else body[:220].rstrip() + " ..."
        for row in body.splitlines():
            lines.append(f"      {row}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="retriever.py",
        description="Rank resume_chunks for one job posting (node 3's input).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  retriever.py --job-id 42 --top-k 8\n"
            "  retriever.py --latest --json\n"
            "  retriever.py --text \"AI Engineer, RAG, LangChain, Python\" --full-text\n"
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job-id", type=int, help="job to retrieve for (by jobs.id)")
    source.add_argument("--latest", action="store_true", help="use the newest jobs row")
    source.add_argument("--text", help="retrieve with a raw query string instead of a job row")
    parser.add_argument("--top-k", type=int, help="chunks to return (default: RETRIEVAL_TOP_K=6)")
    parser.add_argument(
        "--candidates",
        type=int,
        help=f"rows pulled from the index before re-ranking (default: top_k x {CANDIDATE_MULTIPLIER})",
    )
    parser.add_argument(
        "--skills-boost",
        type=float,
        default=DEFAULT_SKILLS_BOOST,
        help=f"bonus per matching job skill (default: {DEFAULT_SKILLS_BOOST})",
    )
    parser.add_argument("--no-skills-boost", action="store_true", help="pure vector similarity")
    parser.add_argument("--full-text", action="store_true", help="print full chunk text")
    parser.add_argument("--json", action="store_true", help="machine-readable output for node 3")
    parser.add_argument(
        "--fake-embeddings",
        action="store_true",
        help="embed the query with dummy vectors (no API key; ranking is meaningless)",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        with connect(settings) as conn:
            check_vector_extension(conn)
            require_tables(conn, "resume_chunks")

            job = None
            if args.job_id is not None:
                job = fetch_job_by_id(conn, args.job_id)
            elif args.latest:
                job = fetch_latest_job(conn)

            results = retrieve(
                conn,
                settings,
                job=job,
                query_text=args.text,
                top_k=args.top_k,
                candidates=args.candidates,
                skills_boost=0.0 if args.no_skills_boost else args.skills_boost,
                fake=args.fake_embeddings,
            )

        if args.json:
            payload = {
                "job": None
                if job is None
                else {
                    "id": field(job, "id"),
                    "title": field(job, "title"),
                    "company": field(job, "company"),
                    "skills": field(job, "skills"),
                },
                "chunks": results,
            }
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(f"database : {redact(settings.dsn)}")
            print(render(results, job=job, full_text=args.full_text))

        if dummy_vector_rows(results):
            print(
                "\n[!] these chunks carry DUMMY test vectors, so the ranking is not "
                "semantic.  Run ingest_resume.py with a real key to fix that."
            )
        return 0

    except ConfigError as exc:
        print(f"\n[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())