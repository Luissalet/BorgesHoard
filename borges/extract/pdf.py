"""PDF extraction with PyMuPDF: one unit per page, page numbers preserved; scanned PDFs are flagged."""

from __future__ import annotations

import re
from pathlib import Path

from .base import Extracted, Unit, finish

MIN_TEXT_PER_PAGE = 25  # average non-blank chars per page below which we assume a scan
PDF_STRUCTURE_VERSION = 2  # bookmarks, then conservative typography fallback


def _typographic_outline(doc) -> list[dict]:
    """Low-confidence heading candidates from unusually large text lines.

    This is deliberately narrower than PageIndex Flash: body-size text,
    repeated running headers and sentence-like lines are not headings.
    No candidates is an honest result, not a reason to invent structure.
    """
    spans: list[tuple[float, int]] = []
    lines: list[tuple[int, str, float, float]] = []
    for page_number, page in enumerate(doc, start=1):
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                line_spans = line.get("spans", [])
                label = " ".join(str(span.get("text") or "").strip()
                                 for span in line_spans).strip()
                if not label:
                    continue
                size = max(float(span.get("size") or 0) for span in line_spans)
                for span in line_spans:
                    chars = len(str(span.get("text") or "").strip())
                    if chars:
                        spans.append((float(span.get("size") or 0), chars))
                y = float(line.get("bbox", (0, 0, 0, 0))[1])
                if y < page.rect.height * 0.75:
                    lines.append((page_number, " ".join(label.split()), size, y))
    if not spans:
        return []
    total = sum(chars for _, chars in spans)
    running = 0
    body_size = 0.0
    for size, chars in sorted(spans):
        running += chars
        if running >= total / 2:
            body_size = size
            break
    counts: dict[str, int] = {}
    for _, label, _, _ in lines:
        key = label.casefold()
        counts[key] = counts.get(key, 0) + 1
    candidates = [(page, label, size, y) for page, label, size, y in lines
                  if size >= max(body_size * 1.25, body_size + 2)
                  and 4 <= len(label) <= 90 and len(label.split()) <= 12
                  and not label.endswith((".", ",", ";"))
                  and re.search(r"\w", label)
                  and counts[label.casefold()] == 1]
    if not candidates:
        return []
    largest = max(size for _, _, size, _ in candidates)
    outline: list[dict] = []
    per_page: dict[int, int] = {}
    for page, label, size, _ in sorted(candidates, key=lambda row: (row[0], row[3])):
        if per_page.get(page, 0) >= 3:
            continue
        per_page[page] = per_page.get(page, 0) + 1
        outline.append({"level": 1 if size >= largest * 0.9 else 2,
                        "title": label, "page": page})
    return outline


def extract_pdf(path: Path) -> Extracted:
    import pymupdf as fitz

    units: list[Unit] = []
    with fitz.open(str(path)) as doc:
        meta_title = (doc.metadata or {}).get("title") or ""
        page_count = doc.page_count
        # PDF bookmarks are declared structure, so they are useful without
        # guessing headings from typography or calling a language model.
        try:
            toc = doc.get_toc() or []
        except (ValueError, RuntimeError):
            toc = []
        outline = [dict(level=level, title=title.strip(), page=page)
                   for level, title, page in toc
                   if isinstance(level, int) and level > 0
                   and isinstance(title, str) and title.strip()
                   and isinstance(page, int) and 1 <= page <= page_count]
        outline_source = "bookmarks" if outline else "none"
        if not outline:
            outline = _typographic_outline(doc)
            if outline:
                outline_source = "typography_inferred"
        by_page: dict[int, list[dict]] = {}
        for entry in outline:
            by_page.setdefault(entry["page"], []).append(entry)
        path_titles: list[str] = []
        total_chars = 0
        for index, page in enumerate(doc, start=1):
            for entry in by_page.get(index, []):
                level = entry["level"]
                path_titles = path_titles[:level - 1]
                path_titles.append(entry["title"])
            text = page.get_text("text") or ""
            total_chars += len("".join(text.split()))
            units.append(Unit(kind="page", number=index,
                              title=" › ".join(path_titles), text=text))
    title = meta_title.strip() or path.stem
    needs_ocr = page_count > 0 and total_chars < MIN_TEXT_PER_PAGE * page_count
    return Extracted(title=title, kind="pdf", units=finish(units),
                     needs_ocr=needs_ocr, pages=page_count, outline=outline,
                     outline_source=outline_source)
