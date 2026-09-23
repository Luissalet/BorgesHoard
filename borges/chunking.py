"""Split extracted units into overlapping chunks that never cross a page/section boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract.base import Unit

CHUNK_CHARS = 900  # ~220 Spanish tokens: inside the model's useful window
OVERLAP_CHARS = 150
MIN_TAIL = 200  # a trailing piece shorter than this is merged into the previous chunk
MIN_UNIT_CHARS = 200  # a page/section shorter than this is merged into its neighbour
MIN_CHUNK_CHARS = 120  # never emit a chunk shorter than this unless it is the whole document
INDEX_VERSION = 2  # bump when chunking rules change: documents below it are re-chunked on the next reindex

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


def merge_small_units(units: list[Unit], minimum: int = MIN_UNIT_CHARS) -> list[Unit]:
    """Merge units shorter than `minimum` into the following unit (or the previous one at the end).

    The merged unit keeps the metadata (kind, number, title, line) of the larger part, so a two-line
    "## Pendiente" section stops being its own chunk and its text rides along with its neighbour.
    """
    if len(units) <= 1:
        return list(units)
    pending: list[Unit] = []  # short units waiting to be attached to the next big one
    out: list[Unit] = []
    for unit in units:
        if len(unit.text) < minimum:
            pending.append(unit)
            continue
        if pending:
            unit = _absorb(unit, pending, before=True)
            pending = []
        out.append(unit)
    if pending:
        if out:
            out[-1] = _absorb(out[-1], pending, before=False)
        else:  # every unit is short: keep the longest one's metadata
            biggest = max(pending, key=lambda u: len(u.text))
            rest = [u for u in pending if u is not biggest]
            out.append(_absorb(biggest, rest, before=True))
    return out


def _absorb(main: Unit, others: list[Unit], before: bool) -> Unit:
    """Return a copy of `main` whose text also contains `others` (heading lines kept as context)."""
    parts = [(f"{u.title}\n{u.text}" if u.title else u.text) for u in others]
    text = "\n\n".join([*parts, main.text] if before else [main.text, *parts])
    return Unit(kind=main.kind, number=main.number, title=main.title, text=text, line_start=main.line_start)


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
    """Chunk every unit (already merged with merge_small_units by the indexer) and drop tiny chunks."""
    out: list[Chunk] = []
    for index, unit in enumerate(units):
        out.extend(chunk_unit(unit, index, len(out), size, overlap))
    return _absorb_tiny_chunks(units, out)


def _absorb_tiny_chunks(units: list[Unit], chunks: list[Chunk]) -> list[Chunk]:
    """A chunk under MIN_CHUNK_CHARS is glued to its neighbour in the same unit (offsets widened)."""
    if len(chunks) <= 1:
        return chunks
    result: list[Chunk] = []
    for chunk in chunks:
        if len(chunk.text) >= MIN_CHUNK_CHARS or not result or result[-1].unit_index != chunk.unit_index:
            result.append(chunk)
            continue
        previous = result[-1]
        previous.char_end = chunk.char_end
        previous.text = units[previous.unit_index].text[previous.char_start : previous.char_end].strip()
    # a tiny first chunk of a unit is merged into the next chunk of that unit
    cleaned: list[Chunk] = []
    for index, chunk in enumerate(result):
        nxt = result[index + 1] if index + 1 < len(result) else None
        if len(chunk.text) < MIN_CHUNK_CHARS and nxt is not None and nxt.unit_index == chunk.unit_index:
            nxt.char_start = chunk.char_start
            nxt.text = units[nxt.unit_index].text[nxt.char_start : nxt.char_end].strip()
            nxt.line = chunk.line
            continue
        cleaned.append(chunk)
    for ordinal, chunk in enumerate(cleaned):
        chunk.ordinal = ordinal
    return cleaned
