"""Logging bootstrap (rich console + optional file)."""

from __future__ import annotations

import logging
import sys

from rich.logging import RichHandler


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once; safe to call repeatedly."""
    root = logging.getLogger()
    if getattr(root, "_jobfather_configured", False):
        root.setLevel(level.upper())
        return

    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    handler.setFormatter(logging.Formatter("%(message)s", datefmt="%H:%M:%S"))

    root.handlers = [handler]
    root.setLevel(level.upper())
    root._jobfather_configured = True  # type: ignore[attr-defined]

    # Crawl4AI is chatty at INFO; keep it at WARNING unless we are debugging.
    logging.getLogger("crawl4ai").setLevel(
        logging.DEBUG if root.level <= logging.DEBUG else logging.WARNING
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    if sys.platform == "win32":  # avoid mojibake in legacy consoles
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # pragma: no cover - best effort
            pass


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
