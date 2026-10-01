"""URL normalisation, hashing and slugging used for dedup-by-URL."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that never change which job a URL points at.  Stripping them
# makes dedup-by-URL stable across referrers, campaigns and search position.
TRACKING_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "refid",
        "trk",
        "trackingid",
        "originalsubdomain",
        "position",
        "pagenum",
        "from",
        "type",
    }
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith("utm_") or lowered in TRACKING_KEYS


def normalize_url(url: str) -> str:
    """Return a canonical form of *url* suitable for hashing/dedup.

    - forces https
    - lower-cases the host and drops a leading ``www.``
    - drops the fragment
    - removes tracking query params and sorts the rest
    - removes a trailing slash (except for the bare root)
    """
    parts = urlsplit((url or "").strip())
    if not parts.scheme and not parts.netloc:  # not a URL - return as-is
        return (url or "").strip()

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    query.sort()

    return urlunsplit(("https", host, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def content_hash(title: str | None, company: str | None, description_markdown: str | None) -> str:
    """Stable fingerprint of the *content* so we can detect edited listings."""
    blob = "|".join(
        _WS_RE.sub(" ", (part or "")).strip().lower()
        for part in (title, company, description_markdown)
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def slugify(value: str, *, max_len: int = 60) -> str:
    slug = _SLUG_RE.sub("-", (value or "").lower()).strip("-")
    return (slug[:max_len].strip("-")) or "job"


def job_slug(source: str, source_job_id: str | None, url: str, *, max_len: int = 100) -> str:
    """Filesystem-safe, collision-resistant slug for a staged job."""
    ident = source_job_id or url_hash(url)[:10]
    return slugify(f"{source}-{ident}", max_len=max_len)
