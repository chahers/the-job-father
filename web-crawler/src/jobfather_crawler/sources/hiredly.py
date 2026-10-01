"""Hiredly adapter - server-rendered and the friendliest source to crawl."""

from __future__ import annotations

import re

from jobfather_crawler.sources.base import SourceAdapter

_SLUG = re.compile(r"/jobs/([^/?#]+)", re.IGNORECASE)


class HiredlyAdapter(SourceAdapter):
    key = "hiredly"

    def job_id_from_url(self, url: str) -> str | None:
        match = _SLUG.search(url or "")
        return match.group(1) if match else None
