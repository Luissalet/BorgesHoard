"""HTML extraction: sections by h1-h3 (so a citation reads `page.html § Heading`), the text of each section from the shared
`hoard_link.docs.readers_lite.html_to_text` (scripts, styles and `<head>` dropped, blocks on their own lines, table cells
joined with ` | `). EPUB chapters use `html_sections` too (epub.py)."""

from __future__ import annotations

import re
from pathlib import Path

from ..hoard_link.docs.readers_lite import html_to_text
from .base import Extracted, Unit, finish
from .text import read_text

_HEADING = re.compile(r"<h([1-3])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DROP = re.compile(r"<(script|style|noscript|svg|nav|head)\b.*?</\1\s*>", re.I | re.S)


def html_sections(markup: str, unit_kind: str = "section") -> tuple[str, list[Unit]]:
    """Split a document into units at h1-h3, keeping text order. Returns (title, units)."""
    title_match = _TITLE.search(markup)
    title = re.sub(r"\s+", " ", html_to_text(title_match.group(1))).strip() if title_match else ""
    body = _DROP.sub(" ", markup)
    units: list[Unit] = []
    cursor, heading = 0, ""
    for match in _HEADING.finditer(body):
        units.append(Unit(kind=unit_kind, number=len(units) + 1, title=heading, text=html_to_text(body[cursor:match.start()])))
        heading = re.sub(r"\s+", " ", html_to_text(match.group(2))).strip()
        cursor = match.end()
    units.append(Unit(kind=unit_kind, number=len(units) + 1, title=heading, text=html_to_text(body[cursor:])))
    return title, units


def extract_html(path: Path) -> Extracted:
    title, units = html_sections(read_text(path))
    return Extracted(title=title or path.stem, kind="html", units=finish(units))
