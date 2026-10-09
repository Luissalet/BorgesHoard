"""Common types for extractors: a document becomes an ordered list of units (pages, sections, chapters)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..hoard_link.docs.chunking import Unit
from ..hoard_link.docs.textclean import clean_text as tidy

__all__ = ["Unit", "Extracted", "clean_text", "finish", "MAX_UNIT_CHARS"]

MAX_UNIT_CHARS = 400_000  # a single page/section bigger than this is truncated (pathological files)


@dataclass
class Extracted:
    title: str
    kind: str  # pdf | docx | pptx | md | txt | epub | html | csv | code
    units: list[Unit] = field(default_factory=list)
    needs_ocr: bool = False
    pages: int = 0
    outline: list[dict] = field(default_factory=list)
    outline_source: str = "none"
    page_methods: list[dict] = field(default_factory=list)  # PDF only: how each page was read (text_layer | ocr | vision | empty)
    ocr_forced: bool = False

    @property
    def chars(self) -> int:
        return sum(len(u.text) for u in self.units)


def clean_text(text: str) -> str:
    """Tidy extracted text (the shared `textclean`: line endings, invisible characters, spaces, blank lines, words split
    by a hyphen at a line end, one-word-per-line exports) and cut a pathological unit."""
    return tidy(text, max_chars=MAX_UNIT_CHARS)


def finish(units: list[Unit]) -> list[Unit]:
    """Drop empty units; sections/chapters renumber, physical pages/slides keep their number."""
    out: list[Unit] = []
    for unit in units:
        unit.text = clean_text(unit.text)
        if unit.text:
            out.append(unit)
    ordinal = 0
    for unit in out:
        if unit.kind not in ("page", "slide"):
            ordinal += 1
            unit.number = ordinal
    return out
