"""DOCX extraction through the shared `hoard_link.docs.readers_lite` (standard library only, zip-bomb safe): a heading opens a
section (the style in any language, or the outline level), paragraphs fill it, list items keep their dash and tables become
rows."""

from __future__ import annotations

from pathlib import Path

from ..hoard_link.docs import readers_lite
from .base import Extracted, Unit, finish


def _units(items: list[dict]) -> list[Unit]:
    return [Unit(kind=item["kind"], number=int(item.get("number") or index), title=item.get("title") or "", text=item.get("text") or "")
            for index, item in enumerate(items, start=1)]


def _read(path: Path, kind: str) -> Extracted:
    result = readers_lite.read_any(path.name, path.read_bytes())
    if result.get("error") or result.get("kind") != kind:
        raise ValueError(result.get("error") or f"not a {kind} file")
    return Extracted(title=result.get("title") or path.stem, kind=kind, units=finish(_units(result["units"])))


def extract_docx(path: Path) -> Extracted:
    return _read(path, "docx")
