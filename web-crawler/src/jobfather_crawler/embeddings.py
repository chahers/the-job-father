"""OpenAI embeddings for staged job descriptions (text-embedding-3-small)."""

from __future__ import annotations

from typing import Sequence

from jobfather_crawler.logging_conf import get_logger
from jobfather_crawler.models import JobPosting

log = get_logger(__name__)

# text-embedding-3-small dimensionality (matches vector(1536) in the schema).
EMBEDDING_DIM = 1536

# The API caps a single input at ~8191 tokens; keep well below that.
MAX_EMBEDDING_CHARS = 12_000


def embedding_text(job: JobPosting) -> str:
    """The exact text we embed: identity fields + the full JD."""
    parts = [job.title or "", job.company or "", job.location or "", job.description_markdown or ""]
    return "\n".join(p for p in parts if p).strip()[:MAX_EMBEDDING_CHARS]


def embed_texts(
    texts: Sequence[str],
    *,
    model: str,
    api_key: str,
    batch_size: int = 100,
) -> list[list[float]]:
    """Embed *texts* in batches, preserving order."""
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set - cannot generate embeddings")

    from openai import OpenAI  # imported lazily so tests need no network deps

    client = OpenAI(api_key=api_key)
    vectors: list[list[float]] = []
    total = len(texts)
    for start in range(0, total, batch_size):
        batch = [text or " " for text in texts[start : start + batch_size]]
        log.info("embedding %d-%d of %d", start + 1, start + len(batch), total)
        response = client.embeddings.create(model=model, input=batch)
        vectors.extend(item.embedding for item in response.data)

    if vectors and len(vectors[0]) != EMBEDDING_DIM:
        log.warning(
            "embedding dimension is %d but the schema expects %d", len(vectors[0]), EMBEDDING_DIM
        )
    return vectors
