"""LinkedIn adapter - CDP-attached session only, deliberately low concurrency."""

from __future__ import annotations

import re

from jobfather_crawler.sources.base import SourceAdapter

_JOB_ID = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", re.IGNORECASE)


class LinkedInAdapter(SourceAdapter):
    key = "linkedin"

    def job_id_from_url(self, url: str) -> str | None:
        match = _JOB_ID.search(url or "")
        return match.group(1) if match else None
