"""JobStreet (SEEK) adapter.

SEEK serves a client-rendered page behind an anti-bot layer, so this source is
crawled by attaching to a *human-logged-in* Chrome over CDP.
"""

from __future__ import annotations

import re

from jobfather_crawler.sources.base import SourceAdapter

_JOB_ID = re.compile(r"/job/(\d+)", re.IGNORECASE)


class JobStreetAdapter(SourceAdapter):
    key = "jobstreet"

    def job_id_from_url(self, url: str) -> str | None:
        match = _JOB_ID.search(url or "")
        return match.group(1) if match else None
