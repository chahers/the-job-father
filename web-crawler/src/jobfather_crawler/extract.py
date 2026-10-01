"""Extraction: schema.org JobPosting JSON-LD (stdlib) + CSS-payload mapping.

Order of preference per page:
  1. ``<script type="application/ld+json">`` JobPosting  (very stable)
  2. the CSS ``schema`` from config/sources.yaml, already parsed by Crawl4AI
     into ``extracted_content`` (a JSON string) and passed in here.

All logic is dependency-free so it can be unit tested against saved HTML.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from jobfather_crawler import dedup
from jobfather_crawler import markdown as md
from jobfather_crawler.models import JobPosting, RunRequest

LD_JSON_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_RELATIVE_RE = re.compile(r"(\d+)\+?\s*(hour|day|week|month)s?\s+ago", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

MIN_DESCRIPTION_CHARS = 120

_SENIORITY_HINTS: tuple[tuple[str, str], ...] = (
    ("intern", "Internship"),
    ("trainee", "Internship"),
    ("junior", "Junior"),
    ("senior", "Senior"),
    ("staff", "Staff"),
    ("lead", "Lead"),
    ("principal", "Principal"),
    ("head of", "Head"),
    ("director", "Director"),
    ("manager", "Manager"),
    ("vp", "VP"),
    ("chief", "Executive"),
)


# --------------------------------------------------------------------------- #
# JSON-LD
# --------------------------------------------------------------------------- #
def iter_json_ld_blocks(html: str | None) -> Iterable[Any]:
    for match in LD_JSON_RE.finditer(html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            try:
                yield json.loads(raw[: raw.rfind("}") + 1])
            except Exception:  # noqa: BLE001
                continue


def _walk(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _type_names(node: dict[str, Any]) -> list[str]:
    raw = node.get("@type")
    if isinstance(raw, list):
        return [str(x).lower() for x in raw]
    return [str(raw).lower()] if raw else []


def find_job_posting_ld(html: str | None) -> dict[str, Any] | None:
    for block in iter_json_ld_blocks(html):
        for node in _walk(block):
            if "jobposting" in _type_names(node):
                return node
    return None


# --------------------------------------------------------------------------- #
# small formatting helpers
# --------------------------------------------------------------------------- #
def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value)).strip()
    return text or None


def _join(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [_clean(v) for v in value]
        parts = [p for p in parts if p]
        return ", ".join(parts) or None
    return _clean(value)


def _format_location(job_location: Any) -> str | None:
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return _clean(job_location)

    address = job_location.get("address")
    if isinstance(address, list):
        address = address[0] if address else None
    if isinstance(address, dict):
        parts = [
            _clean(address.get("addressLocality")),
            _clean(address.get("addressRegion")),
            _clean(address.get("addressCountry")),
        ]
        joined = ", ".join(p for p in parts if p)
        if joined:
            return joined
    return _clean(job_location.get("name"))


def _format_salary(base_salary: Any) -> str | None:
    if isinstance(base_salary, list):
        base_salary = base_salary[0] if base_salary else None
    if not isinstance(base_salary, dict):
        return _clean(base_salary)

    currency = _clean(base_salary.get("currency")) or ""
    value = base_salary.get("value")
    unit = None
    low = high = single = None

    if isinstance(value, dict):
        low = value.get("minValue")
        high = value.get("maxValue")
        single = value.get("value")
        unit = _clean(value.get("unitText"))
    else:
        single = value

    def _num(x: Any) -> str | None:
        if x in (None, ""):
            return None
        try:
            return f"{float(x):,.0f}"
        except (TypeError, ValueError):
            return _clean(x)

    low_s, high_s, single_s = _num(low), _num(high), _num(single)
    if low_s and high_s:
        amount = f"{low_s} - {high_s}"
    elif low_s:
        amount = f"from {low_s}"
    elif single_s:
        amount = single_s
    else:
        amount = ""

    suffix = f" / {unit.lower()}" if unit else ""
    text = f"{currency} {amount}{suffix}".strip()
    return text or None


def _extract_skills(node: dict[str, Any]) -> list[str]:
    collected: list[str] = []
    for key in ("skills", "qualifications", "jobBenefits"):
        value = node.get(key)
        candidates: list[str] = []
        if isinstance(value, str):
            candidates = re.split(r"[\n\u2022\u00b7;,]|(?<=[a-z])\s{2,}", value)
        elif isinstance(value, list):
            candidates = [str(v) for v in value]
        for candidate in candidates:
            text = _WS_RE.sub(" ", str(candidate)).strip(" .-")
            if 1 < len(text) <= 60 and text.lower() not in {s.lower() for s in collected}:
                collected.append(text)
    return collected[:30]


def parse_posted_date(value: Any) -> date | None:
    """Parse ISO dates, ISO datetimes and relative strings like '3 days ago'."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    text = _clean(value)
    if not text:
        return None

    iso = _ISO_DATE_RE.search(text)
    if iso:
        try:
            return date.fromisoformat(iso.group(1))
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    lowered = text.lower()
    now = datetime.now(timezone.utc)
    if "today" in lowered or "just posted" in lowered:
        return now.date()
    if "yesterday" in lowered:
        return (now - timedelta(days=1)).date()

    rel = _RELATIVE_RE.search(lowered)
    if rel:
        amount = int(rel.group(1))
        delta = {
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "month": timedelta(days=30 * amount),
        }[rel.group(2)]
        return (now - delta).date()
    return None


def infer_seniority(title: str | None) -> str | None:
    lowered = (title or "").lower()
    for hint, label in _SENIORITY_HINTS:
        if hint in lowered:
            return label
    return None


# --------------------------------------------------------------------------- #
# mapping
# --------------------------------------------------------------------------- #
def ld_to_fields(node: dict[str, Any]) -> dict[str, Any]:
    """Flatten a schema.org JobPosting node into our internal field dict."""
    identifier = node.get("identifier")
    source_job_id = None
    if isinstance(identifier, dict):
        source_job_id = _clean(identifier.get("value") or identifier.get("name"))
    elif isinstance(identifier, (str, int)):
        source_job_id = _clean(identifier)

    org = node.get("hiringOrganization")
    if isinstance(org, dict):
        company = _clean(org.get("name"))
    else:
        company = _clean(org)

    experience = node.get("experienceRequirements")
    if isinstance(experience, dict):
        seniority = _clean(experience.get("name"))
    else:
        seniority = _clean(experience)

    return {
        "title": _clean(node.get("title")),
        "company": company,
        "location": _format_location(node.get("jobLocation")),
        "salary": _format_salary(node.get("baseSalary")),
        "employment_type": _join(node.get("employmentType")),
        "seniority": seniority,
        "posted_date": node.get("datePosted"),
        "description_html": node.get("description"),
        "skills": _extract_skills(node),
        "source_job_id": source_job_id,
        "canonical_url": _clean(node.get("url")),
    }


def extracted_to_fields(payload: Any) -> dict[str, Any]:
    """Normalise Crawl4AI's ``extracted_content`` (JSON str/dict/list)."""
    if payload is None:
        return {}
    data = payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}
    return {
        "title": _clean(data.get("title")),
        "company": _clean(data.get("company")),
        "location": _clean(data.get("location")),
        "salary": _clean(data.get("salary")),
        "employment_type": _clean(data.get("employment_type")),
        "seniority": _clean(data.get("seniority")),
        "posted_date": data.get("posted_date"),
        "description_html": data.get("description_html") or data.get("description"),
        "skills": data.get("skills") if isinstance(data.get("skills"), list) else [],
        "source_job_id": _clean(data.get("source_job_id")),
    }


def build_job_posting(
    fields: dict[str, Any],
    *,
    url: str,
    source: str,
    request: RunRequest,
    fallback_markdown: str | None = None,
    source_job_id: str | None = None,
) -> JobPosting:
    """Combine extracted fields + crawl markdown into the canonical record."""
    description = md.html_to_markdown(fields.get("description_html")) or (fallback_markdown or "").strip()
    title = _clean(fields.get("title"))

    job = JobPosting(
        source=source,
        source_job_id=source_job_id or fields.get("source_job_id"),
        url=url,
        url_hash=dedup.url_hash(url),
        title=title,
        company=_clean(fields.get("company")),
        location=_clean(fields.get("location")),
        salary=_clean(fields.get("salary")),
        employment_type=_clean(fields.get("employment_type")),
        seniority=_clean(fields.get("seniority")) or infer_seniority(title),
        posted_date=parse_posted_date(fields.get("posted_date")),
        description_markdown=description,
        skills=[str(s) for s in (fields.get("skills") or []) if str(s).strip()][:30],
        run_id=request.run_id,
        state=request.state,
        work_arrangement=request.work_arrangement,
        expected_salary=request.expected_salary,
    )
    job.content_hash = dedup.content_hash(job.title, job.company, job.description_markdown)
    return job


def validate_posting(job: JobPosting) -> str | None:
    """Return a human-readable reason when a posting is not usable, else None."""
    if not job.title:
        return "missing title (possible challenge/empty page)"
    if len(job.description_markdown or "") < MIN_DESCRIPTION_CHARS:
        return f"description too short ({len(job.description_markdown or '')} chars)"
    return None
