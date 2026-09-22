"""Split extracted units into overlapping chunks that never cross a page/section boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract.base import Unit

CHUNK_CHARS = 900  # ~220 Spanish tokens: inside the model's useful window
OVERLAP_CHARS = 150
MIN_TAIL = 200  # a trailing piece shorter than this is merged into the previous chunk

_BREAKS = re.compile(r"\n\n|\n|(?<=[.!?…;:])\s+|(?<=,)\s+|\s+")


@dataclass
class Chunk:
    unit_index: int  # position in the unit list
    ordinal: int  # position within the document
    page: int | None
    section: str
    line: int | None
    char_start: int  # offsets inside the unit text
    char_end: int
    text: str


def _split_points(text: str, start: int, limit: int) -> int:
    """Best cut position in text[start:limit]: paragraph > line > sentence > clause > word."""
    window = text[start:limit]
    best = -1
    priority = {"\n\n": 5, "\n": 4}
    best_priority = -1
    for match in _BREAKS.finditer(window):
        if match.end() < len(window) * 0.4:  # don't cut too early
            continue
        token = match.group(0)
        if token == "\n\n":
            score = priority["\n\n"]
        elif token == "\n":
            score = priority["\n"]
        elif window[match.start() - 1 : match.start()] in ".!?…;:":
            score = 3
        elif window[match.start() - 1 : match.start()] == ",":
            score = 2
        else:
            score = 1
        if score >= best_priority:
            best_priority = score
            best = match.end()
    return start + best if best > 0 else limit


def chunk_unit(unit: Unit, unit_index: int, first_ordinal: int, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[Chunk]:
    text = unit.text
    page = unit.number if unit.kind == "page" else None
    chunks: list[Chunk] = []
    start = 0
    n = len(text)
    while start < n:
        if n - start <= size + MIN_TAIL:
            end = n
        else:
            end = _split_points(text, start, start + size)
        piece = text[start:end]
        stripped = piece.strip()
        if stripped:
            lead = len(piece) - len(piece.lstrip())
            c_start, c_end = start + lead, start + lead + len(stripped)
            line = unit.line_start + text.count("\n", 0, c_start) if unit.line_start is not None else None
            chunks.append(Chunk(unit_index, first_ordinal + len(chunks), page, unit.title, line, c_start, c_end, stripped))
        if end >= n:
            break
        next_start = max(end - overlap, start + 1)
        # move the overlap start to a word boundary
        boundary = text.find(" ", end - overlap, end)
        if boundary > start:
            next_start = boundary + 1
        start = next_start
    return chunks


def chunk_units(units: list[Unit], size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[Chunk]:
    out: list[Chunk] = []
    for index, unit in enumerate(units):
        out.extend(chunk_unit(unit, index, len(out), size, overlap))
    return out
