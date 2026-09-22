"""Common types for extractors: a document becomes an ordered list of units (pages, sections, chapters)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_UNIT_CHARS = 400_000  # a single page/section bigger than this is truncated (pathological files)


@dataclass
class Unit:
    kind: str  # page | section | chapter
    number: int  # page number (1-based) or ordinal of the section/chapter
    title: str = ""
    text: str = ""
    line_start: int | None = None  # 1-based line in the source file, for text formats


@dataclass
class Extracted:
    title: str
    kind: str  # pdf | docx | md | txt | epub | html | csv | code
    units: list[Unit] = field(default_factory=list)
    needs_ocr: bool = False
    pages: int = 0

    @property
    def chars(self) -> int:
        return sum(len(u.text) for u in self.units)


_WS = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Normalize whitespace without destroying paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANKS.sub("\n\n", text)
    return text.strip()[:MAX_UNIT_CHARS]


def finish(units: list[Unit]) -> list[Unit]:
    """Drop empty units and renumber sections/chapters consecutively (pages keep their number)."""
    out: list[Unit] = []
    for unit in units:
        unit.text = clean_text(unit.text)
        if unit.text:
            out.append(unit)
    ordinal = 0
    for unit in out:
        if unit.kind != "page":
            ordinal += 1
            unit.number = ordinal
    return out
