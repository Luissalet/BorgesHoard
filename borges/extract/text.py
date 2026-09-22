"""Markdown, plain text, code and CSV: headings open sections; every unit remembers its first line number."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .base import Extracted, Unit, finish

MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CSV_MAX_ROWS = 200


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _first_heading_title(units: list[Unit]) -> str:
    for unit in units:
        if unit.title:
            return unit.title
    return ""


def extract_markdown(path: Path) -> Extracted:
    lines = read_text(path).split("\n")
    units: list[Unit] = [Unit(kind="section", number=1, title="", line_start=1)]
    in_fence = False
    for number, line in enumerate(lines, start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else MD_HEADING.match(line)
        if match:
            units.append(Unit(kind="section", number=len(units) + 1, title=match.group(2).strip(), line_start=number))
            continue
        units[-1].text += line + "\n"
    title = _first_heading_title(units) or path.stem
    return Extracted(title=title, kind="md", units=finish(units))


def extract_plain(path: Path, kind: str = "txt") -> Extracted:
    text = read_text(path)
    return Extracted(title=path.stem, kind=kind, units=finish([Unit(kind="section", number=1, title="", text=text, line_start=1)]))


def extract_csv(path: Path) -> Extracted:
    text = read_text(path)
    reader = csv.reader(io.StringIO(text))
    rows = []
    for index, row in enumerate(reader):
        if index >= CSV_MAX_ROWS:
            rows.append(f"… ({CSV_MAX_ROWS} first rows shown)")
            break
        rows.append(" | ".join(cell.strip() for cell in row))
    unit = Unit(kind="section", number=1, title="", text="\n".join(rows), line_start=1)
    return Extracted(title=path.stem, kind="csv", units=finish([unit]))
