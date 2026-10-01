"""Retry policy: transient-vs-permanent classification plus async backoff."""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, Literal, TypeVar

from jobfather_crawler.logging_conf import get_logger

log = get_logger(__name__)

FailureKind = Literal["transient", "permanent"]

T = TypeVar("T")

# Substrings that indicate the page *could* succeed on a later attempt.
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "net::err",
    "err_",
    "connection reset",
    "connection aborted",
    "temporarily",
    "429",
    "too many requests",
    "503",
    "service unavailable",
    "target closed",
    "navigation failed",
    "page crashed",
    "browser has disconnected",
)

# Substrings that indicate retrying is pointless.
_PERMANENT_MARKERS = (
    "404",
    "410",
    "page not found",
    "job not found",
    "no longer available",
    "no matching configuration",
    "invalid url",
    "sign in",
    "log in",
    "login",
    "authwall",
    "captcha",
    "challenge",
    "verification",
)


def classify(error: str | None) -> FailureKind:
    """Best-effort classification of a crawl error string."""
    text = (error or "").lower()
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return "permanent"
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return "transient"
    # Unknown errors are treated as transient so we get one more chance.
    return "transient"


def backoff_delay(attempt: int, *, base: float, factor: float, cap: float) -> float:
    """Exponential backoff with full jitter, clamped to *cap*."""
    raw = min(base * (factor ** max(attempt - 1, 0)), cap)
    return random.uniform(0.0, raw)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.5,
    factor: float = 2.0,
    max_delay: float = 30.0,
    is_success: Callable[[T], bool] = lambda _r: True,
    label: str = "task",
) -> tuple[T | None, int, str | None]:
    """Run *fn* until it "succeeds" or attempts are exhausted.

    Returns ``(result, attempts_used, last_error)``.  ``fn`` should raise on a
    hard failure and may return a value that :func:`is_success` rejects.
    """
    last_error: str | None = None
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            result = await fn()
            if is_success(result):
                return result, attempt, None
            last_error = "result rejected by is_success"
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < attempts:
            delay = backoff_delay(attempt, base=base_delay, factor=factor, cap=max_delay)
            log.warning("[%s] attempt %d/%d failed (%s) - retrying in %.1fs", label, attempt, attempts, last_error, delay)
            await asyncio.sleep(delay)

    return None, attempts, last_error
