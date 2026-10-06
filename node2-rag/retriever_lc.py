"""LangChain version of the retriever: the main one.

Does the same job as retriever.py (job in, best resume chunks out), but the
embedding and the vector search are done by LangChain.

LangChain does : OpenAIEmbeddings (text -> numbers) and
                 PGVectorStore (find the closest rows in resume_chunks)
Plain Python   : reading the job row, the skills bonus, printing
                 (reused from retriever.py so both files agree)
"""
from __future__ import annotations

import argparse
import json
import sys

from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGEngine, PGVectorStore

from config import (
    ConfigError,
    check_vector_extension,
    connect,
    field,
    job_embedding_text,
    load_settings,
    prepare,
    redact,
    require_tables,
    strip_driver,
)
from retriever import (
    CANDIDATE_MULTIPLIER,
    DEFAULT_MAX_BOOST,
    DEFAULT_SKILLS_BOOST,
    dummy_vector_rows,
    fetch_job_by_id,
    fetch_latest_job,
    render,
    rerank,
)

# Columns of resume_chunks that LangChain hands back as metadata.
METADATA_COLUMNS = [
    "source_file", "section", "chunk_index", "chunk_type",
    "organisation", "skills", "embedding_model",
]


def build_store(settings):
    """Connect LangChain to the resume_chunks table."""
    plain = strip_driver(settings.database_url)
    url = "postgresql+asyncpg://" + plain.split("://", 1)[1]
    engine = PGEngine.from_connection_string(url=url)
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model, api_key=settings.openai_api_key
    )
    return PGVectorStore.create_sync(
        engine=engine,
        embedding_service=embeddings,
        table_name="resume_chunks",
        id_column="id",
        content_column="content",
        embedding_column="embedding",
        metadata_columns=METADATA_COLUMNS,
    )


def to_candidates(pairs) -> list[dict]:
    """Turn LangChain results (Document, distance) into plain dicts."""
    rows = []
    for doc, distance in pairs:
        meta = doc.metadata
        rows.append({
            "source_file": meta.get("source_file"),
            "section": meta.get("section"),
            "chunk_index": meta.get("chunk_index") or 0,
            "chunk_type": meta.get("chunk_type"),
            "organisation": meta.get("organisation"),
            "skills": meta.get("skills") or [],
            "embedding_model": meta.get("embedding_model"),
            "content": doc.page_content,
            # LangChain gives a distance (lower is closer); similarity = 1 - distance
            "score": 1 - float(distance),
        })
    return rows


def retrieve_lc(
    store,
    settings,
    *,
    job=None,
    query_text=None,
    top_k=None,
    candidates=None,
    skills_boost=DEFAULT_SKILLS_BOOST,
    max_boost=DEFAULT_MAX_BOOST,
) -> list[dict]:
    """Rank resume chunks for one job. Pass either job (a jobs row) or query_text."""
    top_k = top_k or settings.top_k

    if job is not None:
        text = job_embedding_text(job)
        job_skills = list(field(job, "skills") or [])
    else:
        text = prepare(query_text)
        job_skills = []

    if not text.strip():
        raise ConfigError("nothing to search for - pass --job-id / --latest, or --text")

    pool_size = candidates or max(top_k * CANDIDATE_MULTIPLIER, top_k)
    pairs = store.similarity_search_with_score(text, k=pool_size)
    return rerank(
        to_candidates(pairs), job_skills,
        weight=skills_boost, max_boost=max_boost, top_k=top_k,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank resume chunks for a job (LangChain)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job-id", type=int, help="job to use (by jobs.id)")
    source.add_argument("--latest", action="store_true", help="use the newest jobs row")
    source.add_argument("--text", help="search with raw text instead of a job row")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--candidates", type=int, default=None)
    parser.add_argument("--skills-boost", type=float, default=DEFAULT_SKILLS_BOOST)
    parser.add_argument("--no-skills-boost", action="store_true")
    parser.add_argument("--full-text", action="store_true")
    parser.add_argument("--json", action="store_true", help="output for the Writer node")
    args = parser.parse_args()

    try:
        settings = load_settings(require_key=True)
        with connect(settings) as conn:
            check_vector_extension(conn)
            require_tables(conn, "resume_chunks")

            job = None
            if args.job_id is not None:
                job = fetch_job_by_id(conn, args.job_id)
            elif args.latest:
                job = fetch_latest_job(conn)

            store = build_store(settings)
            results = retrieve_lc(
                store, settings,
                job=job, query_text=args.text,
                top_k=args.top_k, candidates=args.candidates,
                skills_boost=0.0 if args.no_skills_boost else args.skills_boost,
            )

        if args.json:
            payload = {
                "job": None if job is None else {
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
            print("\n[!] some chunks carry DUMMY test vectors; re-run ingest_resume.py with a real key.")
        return 0

    except ConfigError as exc:
        print(f"\n[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())