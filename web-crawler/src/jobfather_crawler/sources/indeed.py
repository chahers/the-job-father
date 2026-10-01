"""Indeed adapter - best-effort only (ToS-sensitive, aggressive anti-bot)."""

from __future__ import annotations

import re

from jobfather_crawler.sources.base import SourceAdapter

_JK = re.compile(r"[?&]jk=([0-9a-zA-Z]+)", re.IGNORECASE)


class IndeedAdapter(SourceAdapter):
    key = "indeed"

    def job_id_from_url(self, url: str) -> str | None:
        match = _JK.search(url or "")
        return match.group(1) if match else None
