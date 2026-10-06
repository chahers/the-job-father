"""Ingest ``master_resume/*.md`` into the ``resume_chunks`` table.

What this does
--------------
1. Parse every master-resume file into sections (a ``#`` heading + its metadata
   lines + its bullets).
2. Split each section's bullets into groups (default 2 per chunk) and render one
   *self-contained* chunk per group: the heading and the ``Type / Organisation /
   Skills`` lines are repeated inside every chunk.  Repeating them is the point -
   they are the keyword carriers that retrieval matches against a job posting.

   Example chunk text::

       # Internal Policy RAG Chatbot
       Type: Work project
       Organisation: MRCB (AI Engineer, Nov 2025 - Present, 1-year contract)
       Skills: Python, RAG, Google ADK, Gemini Flash, ...

       - Built the Python backend for an internal RAG chatbot over 60+ policy PDFs ...

3. Embed the chunks and upsert them.  ``content_hash`` (sha256 of the chunk text)
   decides what needs embedding, so re-running after a small edit only pays for
   the chunks that actually changed.

Placeholders
------------
``[ADD: ...]`` lines in the master files are TODO notes, not content.  They are
reported and skipped so they can never be embedded or reach the Writer.

Usage (from node2-rag/)
-----------------------
    .\\.venv\\Scripts\\python.exe ingest_resume.py --dry-run            # plan only, no API, no writes
    .\\.venv\\Scripts\\python.exe ingest_resume.py --fake-embeddings    # plumbing test (dummy vectors)
    .\\.venv\\Scripts\\python.exe ingest_resume.py --status             # what is stored today
    .\\.venv\\Scripts\\python.exe ingest_resume.py                      # real ingest (needs OPENAI_API_KEY)
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from config import (
    MASTER_RESUME_DIR,
    PRICE_PER_1M_TOKENS,
    ConfigError,
    check_vector_extension,
    connect,
    content_hash,
    embed_texts,
    estimate_cost_usd,
    estimate_tokens,
    load_settings,
    redact,
    require_tables,
    vector_literal,
)

# Value stored in resume_chunks.embedding_model for dummy vectors.  A row carrying
# this value is treated as "not really embedded", so a later real ingest replaces
# it instead of skipping it (otherwise test vectors would live forever).
FAKE_MODEL = "fake-deterministic"

META_KEYS = {
    "type": "chunk_type",
    "organisation": "organisation",
    "organization": "organisation",
    "skills": "skills",
}


@dataclass(slots=True)
class Chunk:
    source_file: str
    section: str
    chunk_index: int
    chunk_type: str | None
    organisation: str | None
    skills: tuple[str, ...]
    content: str
    content_hash: str

    @property
    def key(self) -> tuple[str, str]:
        """Identity of a chunk: same file + same text == same row."""
        return (self.source_file, self.content_hash)


@dataclass(slots=True)
class _Section:
    heading: str = ""
    chunk_type: str | None = None
    organisation: str | None = None
    skills: tuple[str, ...] = ()
    bullets: list[str] = field(default_factory=list)


def _parse_sections(text: str) -> tuple[list[_Section], list[str]]:
    """Split markdown into sections + metadata, collecting ``[ADD: ...]`` notes."""
    sections: list[_Section] = []
    placeholders: list[str] = []
    current = _Section()

    def flush() -> None:
        if current.heading or current.bullets:
            sections.append(current)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#"):
            flush()
            current = _Section(heading=line.lstrip("#").strip())
            continue

        if line.upper().startswith("[ADD"):
            placeholders.append(line)
            continue

        key, sep, value = line.partition(":")
        key_lower = key.strip().lower()
        if sep and key_lower in META_KEYS and not line.startswith("-"):
            value = value.strip()
            if key_lower == "skills":
                current.skills = tuple(part.strip() for part in value.split(",") if part.strip())
            else:
                setattr(current, META_KEYS[key_lower], value or None)
            continue

        if line.startswith(("- ", "* ", "+ ")):
            current.bullets.append(line[2:].strip())
        elif current.bullets:
            current.bullets[-1] += " " + line  # wrapped continuation
        else:
            current.bullets.append(line)

    flush()
    return sections, placeholders


def _render(section: _Section, bullets: list[str]) -> str:
    """One self-contained chunk: heading + keyword metadata + the bullet group."""
    lines: list[str] = []
    if section.heading:
        lines.append(f"# {section.heading}")
    if section.chunk_type:
        lines.append(f"Type: {section.chunk_type}")
    if section.organisation:
        lines.append(f"Organisation: {section.organisation}")
    if section.skills:
        lines.append("Skills: " + ", ".join(section.skills))
    if lines:
        lines.append("")
    lines.extend(f"- {bullet}" for bullet in bullets)
    return "\n".join(lines).strip()


def resume_files(only: str | None = None) -> list[Path]:
    """Master-resume markdown files, optionally narrowed to one file."""
    if not MASTER_RESUME_DIR.is_dir():
        raise ConfigError(f"master resume folder not found: {MASTER_RESUME_DIR}")
    files = sorted(MASTER_RESUME_DIR.glob("*.md"))
    if not files:
        raise ConfigError(f"no *.md master-resume files in {MASTER_RESUME_DIR}")
    if only:
        wanted = Path(only).stem
        files = [path for path in files if path.stem == wanted or path.name == only]
        if not files:
            raise ConfigError(f"no master-resume file matches {only!r}")
    return files


def chunk_file(
    path: Path, bullets_per_chunk: int = 2
) -> tuple[list[Chunk], list[str], list[str]]:
    """Chunk one file -> (chunks, skipped placeholders, sections without bullets)."""
    sections, placeholders = _parse_sections(path.read_text(encoding="utf-8"))
    size = max(1, bullets_per_chunk)
    chunks: list[Chunk] = []
    empty: list[str] = []
    index = 0

    for section in sections:
        if not section.bullets:
            empty.append(section.heading or "(untitled)")
            continue
        for start in range(0, len(section.bullets), size):
            content = _render(section, section.bullets[start : start + size])
            chunks.append(
                Chunk(
                    source_file=path.name,
                    section=section.heading,
                    chunk_index=index,
                    chunk_type=section.chunk_type,
                    organisation=section.organisation,
                    skills=section.skills,
                    content=content,
                    content_hash=content_hash(content),
                )
            )
            index += 1
    return chunks, placeholders, empty


def collect(
    files: list[Path], bullets_per_chunk: int
) -> tuple[list[Chunk], dict[str, list[str]], dict[str, list[str]]]:
    """Chunk every file, keeping the per-file warnings separate."""
    chunks: list[Chunk] = []
    placeholders: dict[str, list[str]] = {}
    empty: dict[str, list[str]] = {}
    for path in files:
        file_chunks, file_placeholders, file_empty = chunk_file(path, bullets_per_chunk)
        chunks.extend(file_chunks)
        if file_placeholders:
            placeholders[path.name] = file_placeholders
        if file_empty:
            empty[path.name] = file_empty
    return chunks, placeholders, empty


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
UPSERT_SQL = """
INSERT INTO resume_chunks (
    source_file, section, chunk_index, chunk_type, organisation, skills,
    content, content_hash, embedding, embedding_model, updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, now()
)
ON CONFLICT (source_file, content_hash) DO UPDATE SET
    section         = EXCLUDED.section,
    chunk_index     = EXCLUDED.chunk_index,
    chunk_type      = EXCLUDED.chunk_type,
    organisation    = EXCLUDED.organisation,
    skills          = EXCLUDED.skills,
    content         = EXCLUDED.content,
    -- a NULL EXCLUDED value means "this chunk did not need re-embedding"
    embedding       = COALESCE(EXCLUDED.embedding, resume_chunks.embedding),
    embedding_model = COALESCE(EXCLUDED.embedding_model, resume_chunks.embedding_model),
    updated_at      = now()
RETURNING (xmax = 0) AS inserted
"""


def load_existing(conn) -> dict[tuple[str, str], tuple[bool, str | None]]:
    """``(source_file, content_hash)`` -> ``(has_vector, embedding_model)``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_file, content_hash, embedding IS NOT NULL, embedding_model "
            "FROM resume_chunks"
        )
        return {(row[0], row[1]): (row[2], row[3]) for row in cur.fetchall()}


def needs_embedding(entry: tuple[bool, str | None] | None, *, fake: bool) -> bool:
    """Decide whether a chunk must be (re-)embedded.

    * absent or vectorless row         -> yes
    * row holding dummy test vectors   -> yes, but only on a real run, so a real
      ingest always replaces them and a test run never downgrades a real vector
    * unchanged row with a real vector -> no (this is what saves API spend)
    """
    if entry is None:
        return True
    has_vector, model = entry
    if not has_vector:
        return True
    if model == FAKE_MODEL and not fake:
        return True
    return False


def upsert_chunks(conn, rows) -> tuple[int, int]:
    """Write every chunk.  *rows* is ``[(chunk, vector|None, model|None), ...]``."""
    inserted = updated = 0
    with conn.cursor() as cur:
        for chunk, vector, model in rows:
            cur.execute(
                UPSERT_SQL,
                (
                    chunk.source_file,
                    chunk.section,
                    chunk.chunk_index,
                    chunk.chunk_type,
                    chunk.organisation,
                    list(chunk.skills),
                    chunk.content,
                    chunk.content_hash,
                    vector_literal(vector) if vector else None,
                    model,
                ),
            )
            row = cur.fetchone()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1
    return inserted, updated


def prune_file(conn, source_file: str, keep_hashes: list[str]) -> int:
    """Delete rows of *source_file* whose text no longer exists in the file."""
    if not keep_hashes:
        return 0  # never wipe a file that currently produces no chunks
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM resume_chunks WHERE source_file = %s AND content_hash <> ALL(%s)",
            (source_file, keep_hashes),
        )
        return cur.rowcount


def prune_missing_files(conn, present_files: list[str]) -> int:
    """Delete rows whose source file was deleted or renamed."""
    if not present_files:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM resume_chunks WHERE source_file <> ALL(%s)", (present_files,)
        )
        return cur.rowcount


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def print_plan(files, chunks, placeholders, empty, bullets_per_chunk: int) -> None:
    print(f"master_resume : {MASTER_RESUME_DIR}")
    print(f"chunking      : {bullets_per_chunk} bullet(s) per chunk")
    print()
    per_file = Counter(chunk.source_file for chunk in chunks)
    for path in files:
        print(f"  {path.name:<38} {per_file.get(path.name, 0):>2} chunk(s)")
        for note in placeholders.get(path.name, []):
            print(f"      - skipped placeholder  : {note[:88]}")
        for heading in empty.get(path.name, []):
            print(f"      - section has no bullets: {heading}")
    print(f"\n  total: {len(chunks)} chunk(s) from {len(files)} file(s)")


def show_status(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE embedding IS NULL), "
            "count(*) FILTER (WHERE embedding_model = %s) FROM resume_chunks",
            (FAKE_MODEL,),
        )
        total, missing, dummy = cur.fetchone()
        print(f"\nresume_chunks : {total} row(s), {missing} without a vector")
        if dummy:
            print(
                f"  [!] {dummy} row(s) hold DUMMY vectors ({FAKE_MODEL}).  Re-run without "
                "--fake-embeddings once OPENAI_API_KEY is set to replace them."
            )
        cur.execute(
            "SELECT source_file, count(*), count(*) FILTER (WHERE embedding IS NULL) "
            "FROM resume_chunks GROUP BY 1 ORDER BY 1"
        )
        for source_file, count, pending in cur.fetchall():
            print(f"    {source_file:<38} {count:>2} chunk(s), {pending} unembedded")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest_resume.py",
        description="Chunk + embed master_resume/*.md into the resume_chunks table.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  ingest_resume.py --dry-run            plan only: no API call, no writes\n"
            "  ingest_resume.py --fake-embeddings    plumbing test with dummy vectors\n"
            "  ingest_resume.py --status             what is stored right now\n"
            "  ingest_resume.py                      real ingest (needs OPENAI_API_KEY)\n"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would happen; no API calls, no writes"
    )
    parser.add_argument(
        "--fake-embeddings",
        action="store_true",
        help="write deterministic dummy vectors instead of calling OpenAI (plumbing test only)",
    )
    parser.add_argument("--file", metavar="NAME", help="only this master-resume file (name or stem)")
    parser.add_argument(
        "--bullets-per-chunk",
        type=int,
        default=2,
        metavar="N",
        help="bullets grouped into one chunk (default: 2)",
    )
    parser.add_argument(
        "--no-prune", action="store_true", help="keep rows for edited/removed chunks"
    )
    parser.add_argument("--status", action="store_true", help="print stored state and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        settings = load_settings()
        print(f"database : {redact(settings.dsn)}")
        print(f"model    : {settings.embedding_model}")

        if args.status:
            with connect(settings) as conn:
                check_vector_extension(conn)
                require_tables(conn, "resume_chunks")
                return show_status(conn)

        print(f"vectors  : {'DUMMY (--fake-embeddings)' if args.fake_embeddings else 'OpenAI API'}\n")

        files = resume_files(args.file)
        chunks, placeholders, empty = collect(files, args.bullets_per_chunk)
        print_plan(files, chunks, placeholders, empty, args.bullets_per_chunk)

        if args.dry_run:
            print("\n[dry run] nothing was written.")
            try:
                with connect(settings) as conn:
                    existing = load_existing(conn)
                pending = [c for c in chunks if needs_embedding(existing.get(c.key), fake=False)]
                tokens = sum(estimate_tokens(chunk.content) for chunk in pending)
                print(
                    f"[dry run] {len(pending)} chunk(s) need embedding, "
                    f"{len(chunks) - len(pending)} unchanged."
                )
                print(
                    f"[dry run] ~{tokens} input tokens ~= "
                    f"${estimate_cost_usd(tokens):.6f} at ${PRICE_PER_1M_TOKENS}/1M"
                )
            except ConfigError as exc:
                print(f"[dry run] could not compare with the database (fine): {exc}")
            return 0

        if not args.fake_embeddings and not settings.has_api_key:
            load_settings(require_key=True)  # raises ConfigError with the fix

        with connect(settings) as conn:
            check_vector_extension(conn)
            require_tables(conn, "resume_chunks")

            existing = load_existing(conn)
            pending = [c for c in chunks if needs_embedding(existing.get(c.key), fake=args.fake_embeddings)]

            vectors: dict[tuple[str, str], list[float]] = {}
            if pending:
                print(f"\nembedding {len(pending)} chunk(s)...")

                def progress(done: int, total: int) -> None:
                    print(f"  {done}/{total}", end="\r")

                results = embed_texts(
                    [chunk.content for chunk in pending],
                    settings,
                    fake=args.fake_embeddings,
                    progress=progress,
                )
                print()
                vectors = {chunk.key: vector for chunk, vector in zip(pending, results)}

            model_name = FAKE_MODEL if args.fake_embeddings else settings.embedding_model
            rows = [
                (chunk, vectors.get(chunk.key), model_name if chunk.key in vectors else None)
                for chunk in chunks
            ]
            inserted, updated = upsert_chunks(conn, rows)
            conn.commit()

            pruned = 0
            if not args.no_prune:
                for path in files:
                    keep = [c.content_hash for c in chunks if c.source_file == path.name]
                    pruned += prune_file(conn, path.name, keep)
                if args.file is None:
                    pruned += prune_missing_files(conn, [path.name for path in files])
                conn.commit()

            print(
                f"\n{len(chunks)} chunk(s): {inserted} inserted, {updated} updated, "
                f"{len(vectors)} embedded, {len(chunks) - len(vectors)} skipped (unchanged), "
                f"{pruned} pruned"
            )
            if args.fake_embeddings and vectors:
                print(
                    f"[!] DUMMY vectors stored ({FAKE_MODEL}).  Retrieval results are "
                    "meaningless until a real ingest replaces them."
                )
            return show_status(conn)

    except ConfigError as exc:
        print(f"\n[!] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())