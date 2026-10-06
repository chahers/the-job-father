"""node 2 (RAG) configuration: .env secrets, paths, and embedding helpers.

Design notes
------------
* ``requirements.txt`` for this node has no SQLAlchemy and no pydantic, so this
  module deliberately uses ``python-dotenv`` + a plain dataclass.  It mirrors the
  *values* declared in ``web-crawler/src/jobfather_crawler/config.py`` (same role,
  database, port, embedding model) so both nodes stay on one database contract.
* ``openai`` is imported lazily inside :func:`embed_texts`, so every command that
  does not call the API works with an empty ``OPENAI_API_KEY`` - which is the
  situation until credits are bought (use ``--dry-run`` / ``--fake-embeddings``).
* Nothing here reads the `postgres` superuser password: the one-time bootstrap in
  ``database/scripts/pg_setup.ps1`` owns that.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
NODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = NODE_DIR.parent
MASTER_RESUME_DIR = NODE_DIR / "master_resume"
DATABASE_DIR = REPO_ROOT / "database"
MIGRATIONS_DIR = DATABASE_DIR / "migrations"

# --------------------------------------------------------------------------- #
# constants (1536 dims is what the schema's vector(1536) expects)
# --------------------------------------------------------------------------- #
EMBEDDING_DIM = 1536
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
# The crawler caps one input at 12_000 chars; the API hard-caps at ~8191 tokens,
# so staying at the crawler's figure keeps both nodes comfortably inside it.
MAX_EMBEDDING_CHARS = 12_000
DEFAULT_BATCH_SIZE = 100
DEFAULT_TOP_K = 6
# text-embedding-3-small list price, used only for the cost estimate.
PRICE_PER_1M_TOKENS = 0.02
CHARS_PER_TOKEN = 4.0

# Loads node2-rag/.env if present; harmless when the file is missing.
load_dotenv(NODE_DIR / ".env")


class ConfigError(RuntimeError):
    """Raised with a human-readable fix when configuration is incomplete."""


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class Settings:
    database_url: str
    openai_api_key: str = ""
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    batch_size: int = DEFAULT_BATCH_SIZE
    top_k: int = DEFAULT_TOP_K

    @property
    def dsn(self) -> str:
        """libpq connection string (psycopg3 cannot parse ``postgresql+psycopg://``)."""
        return strip_driver(self.database_url)

    @property
    def has_api_key(self) -> bool:
        return bool(self.openai_api_key.strip())


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def strip_driver(url: str) -> str:
    """Drop a SQLAlchemy driver suffix so a shared .env works for either node.

    ``postgresql+psycopg://a@b/d``  ->  ``postgresql://a@b/d``
    ``postgres://a@b/d``            ->  ``postgresql://a@b/d``
    """
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if "+" in scheme:
            return f"{scheme.split('+', 1)[0]}://{rest}"
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def redact(dsn: str) -> str:
    """Hide the password before printing a connection string."""
    return re.sub(r"(://[^:/@]+:)[^@]*(@)", r"\1***\2", dsn)


def load_settings(*, require_key: bool = False) -> Settings:
    """Read ``node2-rag/.env`` + the process environment.

    ``require_key=True`` turns a missing API key into an actionable error instead
    of letting the OpenAI client fail later.
    """
    url = env("DATABASE_URL")
    if not url:
        raise ConfigError(
            "DATABASE_URL is not set.  Add it to node2-rag/.env, e.g.\n"
            "  DATABASE_URL=postgresql://jobfather:the-great-job-father@127.0.0.1:5432/jobfather"
        )

    settings = Settings(
        database_url=url,
        openai_api_key=env("OPENAI_API_KEY"),
        embedding_model=env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        batch_size=int(env("EMBEDDING_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))),
        top_k=int(env("RETRIEVAL_TOP_K", str(DEFAULT_TOP_K))),
    )

    if require_key and not settings.has_api_key:
        raise ConfigError(
            "OPENAI_API_KEY is empty, so no embedding can be generated.\n"
            "Either buy credits and put the key in node2-rag/.env, or use:\n"
            "  --dry-run           show what would be embedded, write nothing\n"
            "  --fake-embeddings   deterministic dummy vectors (plumbing tests only)"
        )
    return settings


# --------------------------------------------------------------------------- #
# database helpers
# --------------------------------------------------------------------------- #
def connect(settings: Settings, *, autocommit: bool = False):
    """Open a psycopg3 connection, translating the usual failure modes."""
    import psycopg

    try:
        return psycopg.connect(settings.dsn, autocommit=autocommit)
    except psycopg.OperationalError as exc:
        raise ConfigError(
            f"could not connect to PostgreSQL: {exc}\n"
            "Is the cluster running?   .\\database\\scripts\\pg.ps1 status\n"
            f"DSN: {redact(settings.dsn)}"
        ) from exc


def vector_extension_version(conn) -> str | None:
    """Installed pgvector version, or None when the extension is absent."""
    with conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = cur.fetchone()
    return row[0] if row else None


def check_vector_extension(conn) -> str:
    """Fail early with the exact fix when pgvector is missing."""
    version = vector_extension_version(conn)
    if version is None:
        raise ConfigError(
            "pgvector is not installed in this database (CREATE EXTENSION needs a\n"
            "superuser the first time).  Run the one-time bootstrap from the repo root:\n"
            "  .\\database\\scripts\\pg_setup.ps1 -SuperuserPassword "
            "(Read-Host -AsSecureString 'postgres superuser password')"
        )
    return version


def installed_tables(conn) -> set[str]:
    """Names of the tables that actually exist in ``public``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        return {row[0] for row in cur.fetchall()}


def require_tables(conn, *names: str) -> None:
    """Fail early with the exact fix when the shared migrations are not applied."""
    missing = sorted(set(names) - installed_tables(conn))
    if missing:
        raise ConfigError(
            "missing table(s): " + ", ".join(missing) + "\n"
            "Apply the shared migrations first (from node2-rag/):\n"
            "  .\\.venv\\Scripts\\python.exe db_init.py"
        )


def field(record, name: str, default=None):
    """Read a column from a dict row or a psycopg ``Row`` without raising."""
    try:
        return record[name]
    except (KeyError, IndexError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# text -> vector helpers
# --------------------------------------------------------------------------- #
def content_hash(text: str) -> str:
    """sha256 of the exact text that gets embedded (hex)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare(text: str | None) -> str:
    """Normalise + truncate one embedding input (never returns an empty string)."""
    text = (text or "").strip()
    if not text:
        return " "
    return text[:MAX_EMBEDDING_CHARS]


def vector_literal(values) -> str:
    """pgvector text literal, byte-for-byte the format the crawler writes."""
    return "[" + ",".join(f"{float(value):.7g}" for value in values) + "]"


def fake_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic stand-in for a real embedding (plumbing tests only).

    Seeded by the text itself, so a chunk always maps to the same vector and two
    identical texts collapse to the same point - enough to exercise the SQL and
    the ranking code with no API key.  These vectors carry NO semantic meaning.
    """
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    return [rng.gauss(0.0, 1.0) for _ in range(dim)]


def embed_texts(
    texts: list[str],
    settings: Settings,
    *,
    fake: bool = False,
    progress=None,
) -> list[list[float]]:
    """Embed *texts* in batches, preserving order.

    ``fake=True`` returns deterministic dummy vectors instead of calling OpenAI, so
    the whole pipeline can be exercised before an API key exists.
    """
    prepared = [prepare(text) for text in texts]
    if not prepared:
        return []
    if fake:
        if progress:
            progress(len(prepared), len(prepared))
        return [fake_embedding(text) for text in prepared]

    if not settings.has_api_key:
        raise ConfigError(
            "OPENAI_API_KEY is empty - cannot call the embeddings API.  "
            "Use --dry-run or --fake-embeddings."
        )

    from openai import OpenAI  # lazy: keeps keyless commands dependency-free

    client = OpenAI(api_key=settings.openai_api_key)
    vectors: list[list[float]] = []
    batch = max(1, settings.batch_size)
    for start in range(0, len(prepared), batch):
        window = prepared[start : start + batch]
        response = client.embeddings.create(model=settings.embedding_model, input=window)
        vectors.extend(item.embedding for item in response.data)
        if progress:
            progress(start + len(window), len(prepared))

    if vectors and len(vectors[0]) != EMBEDDING_DIM:
        raise ConfigError(
            f"{settings.embedding_model} returned {len(vectors[0])}-dim vectors but the "
            f"schema declares vector({EMBEDDING_DIM}).  Fix the column or the model."
        )
    return vectors


def job_embedding_text(row) -> str:
    """The text embedded for one ``jobs`` row.

    Deliberately the SAME recipe as ``web-crawler``'s ``embeddings.embedding_text``
    (title, company, location, full markdown JD, truncated) so the single
    ``jobs.embedding`` column never mixes two recipes - whichever node writes it.
    Seniority and skills are already present in most postings' markdown.
    """
    parts = [
        field(row, "title"),
        field(row, "company"),
        field(row, "location"),
        field(row, "description_markdown"),
    ]
    return prepare("\n".join(part for part in parts if part))


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) - for cost previews only."""
    return int(len(text) / CHARS_PER_TOKEN)


def estimate_cost_usd(tokens: int) -> float:
    return tokens / 1_000_000 * PRICE_PER_1M_TOKENS