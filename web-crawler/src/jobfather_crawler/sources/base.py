"""Source specification + adapter base class.

Selectors and caps live in config/sources.yaml; the adapter classes only add
source-specific URL parsing.  This keeps DOM drift a config edit, not a code
change.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class SourceSpec(BaseModel):
    """Validated view of one entry under ``sources:`` in sources.yaml."""

    model_config = ConfigDict(extra="ignore")

    key: str = "unknown"
    label: str = ""
    url_patterns: list[str] = Field(default_factory=list)
    needs_cdp: bool = False
    max_jobs: int = 50
    max_concurrency: int = 4
    prefer_json_ld: bool = True
    wait_for: str | None = None
    js_code: str | None = None
    css_schema: dict[str, Any] | None = None
    dispatcher: str = "memory_adaptive"  # memory_adaptive | semaphore

    def matches(self, url: str) -> bool:
        host = urlsplit(url).netloc.lower()
        return any(pattern.lower() in host for pattern in self.url_patterns)


def parse_spec(key: str, raw: dict[str, Any]) -> SourceSpec:
    """Build a :class:`SourceSpec`, mapping the YAML ``schema`` key."""
    data: dict[str, Any] = dict(raw or {})
    data["key"] = key
    data.setdefault("label", key.title())
    if "schema" in data:
        data["css_schema"] = data.pop("schema")
    return SourceSpec.model_validate(data)


class SourceAdapter:
    """Base adapter - subclasses override :meth:`job_id_from_url`."""

    key: str = "base"

    def __init__(self, spec: SourceSpec) -> None:
        self.spec = spec

    # -- metadata shortcuts ------------------------------------------------
    @property
    def name(self) -> str:
        return self.spec.key

    @property
    def needs_cdp(self) -> bool:
        return self.spec.needs_cdp

    def matches(self, url: str) -> bool:
        return self.spec.matches(url)

    def max_jobs(self, ceiling: int | None = None) -> int:
        return self.spec.max_jobs if ceiling is None else min(self.spec.max_jobs, ceiling)

    def concurrency(self, default: int) -> int:
        return self.spec.max_concurrency or default

    # -- source specific ---------------------------------------------------
    def job_id_from_url(self, url: str) -> str | None:
        """Extract the platform job id from a posting URL (best effort)."""
        return None
