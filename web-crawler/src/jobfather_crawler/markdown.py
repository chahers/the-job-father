"""HTML -> Markdown conversion and staged-document assembly.

Pure stdlib so the extractor and stager stay testable without Crawl4AI or a
database.  Job descriptions are simple markup (headings, paragraphs, lists),
which is exactly what this converter targets.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import yaml

_HEADINGS = {f"h{i}": i for i in range(1, 7)}
_SKIP = {"script", "style", "noscript", "head", "svg", "iframe"}
_BLOCK_END = {"p", "div", "section", "article", "tr", "blockquote", "table", "ul", "ol"}
_MANY_NEWLINES = re.compile(r"\n{3,}")
_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")


class _HtmlToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0
        self._lists: list[str] = []
        self._links: list[str | None] = []

    # -- helpers ---------------------------------------------------------
    def _nl(self) -> None:
        self._parts.append("\n")

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return

        if tag in _HEADINGS:
            self._nl()
            self._nl()
            self._parts.append("#" * _HEADINGS[tag] + " ")
        elif tag in ("p", "div", "section", "article", "tr", "table"):
            self._nl()
        elif tag == "br":
            self._nl()
        elif tag in ("ul", "ol"):
            if not self._lists:
                self._nl()
            self._lists.append(tag)
        elif tag == "li":
            self._nl()
            depth = max(len(self._lists) - 1, 0)
            self._parts.append("  " * depth + "- ")
        elif tag == "a":
            href = dict(attrs).get("href")
            self._links.append(href)
            self._parts.append("[")
        elif tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag in ("td", "th"):
            if self._parts and not self._parts[-1].endswith(("| ", "\n")):
                self._parts.append(" | ")
        elif tag == "blockquote":
            self._nl()
            self._parts.append("> ")
        elif tag == "hr":
            self._nl()
            self._parts.append("---")
            self._nl()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return

        if tag in _HEADINGS:
            self._nl()
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            self._nl()
        elif tag in _BLOCK_END:
            self._nl()
        elif tag == "a":
            href = self._links.pop() if self._links else None
            self._parts.append(f"]({href})" if href else "]")
        elif tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")

    def handle_data(self, data: str) -> None:
        if not self._skip and data:
            self._parts.append(data)

    def result(self) -> str:
        return _collapse("".join(self._parts))


def _collapse(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return _MANY_NEWLINES.sub("\n\n", "\n".join(out)).strip()


def looks_like_html(value: str | None) -> bool:
    return bool(value) and bool(_TAG_RE.search(value or ""))


def html_to_markdown(html_or_text: str | None) -> str:
    """Convert an HTML fragment (or plain text) to clean Markdown."""
    if not html_or_text:
        return ""
    if not looks_like_html(html_or_text):
        return _collapse(html_or_text)

    parser = _HtmlToMarkdown()
    try:
        parser.feed(html_or_text)
        parser.close()
    except Exception:  # pragma: no cover - malformed markup fallback
        return _collapse(_TAG_RE.sub(" ", html_or_text))
    return parser.result()


def front_matter(meta: dict[str, Any]) -> str:
    """Render a YAML front-matter block (delimited by ``---``)."""
    body = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{body}---"


def assemble_document(meta: dict[str, Any], body_markdown: str) -> str:
    """Combine front-matter metadata with the JD body."""
    return f"{front_matter(meta)}\n\n{(body_markdown or '').strip()}\n"
