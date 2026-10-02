"""PDF extraction with PyMuPDF: one unit per page, page numbers preserved; scanned PDFs are flagged."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..hoard_link import fam_docs
from .base import Extracted, Unit, finish

log = logging.getLogger("borges.pdf")

MIN_TEXT_PER_PAGE = 25  # average non-blank chars per page below which we assume a scan
PDF_STRUCTURE_VERSION = 6  # recognize standalone contents headings
OCR_MAX_PAGES = 2000


def ocr_enabled() -> bool:
    """Scanned pages are read by the family's OCR service (Kafka's Hoard through the hub) when it is running.
    BORGES_OCR=0 turns that off; BORGES_OCR_LANG is the language asked of the engine (default Spanish)."""
    return os.environ.get("BORGES_OCR", "1").strip() != "0" and fam_docs.available()


def _page_chars(text: str) -> int:
    return len("".join(text.split()))


def _ocr_thin_pages(path: Path, units: list[Unit], page_count: int) -> int:
    """Fill the pages that have (almost) no text layer with Kafka's OCR text; returns how many pages were read. The pages that
    already have text keep PyMuPDF's, so the page coordinates and the outline titles of the citations never change."""
    got = fam_docs.extract(str(path), ocr="auto", max_pages=min(page_count, OCR_MAX_PAGES), local_fallback=False,
                           lang=os.environ.get("BORGES_OCR_LANG", "").strip() or "es")
    if not got.get("ok"):
        log.info("%s: OCR through the family service failed (%s)", path.name, got.get("error") or got.get("kind"))
        return 0
    read = {int(u["number"]): str(u.get("text") or "") for u in got.get("units") or [] if u.get("kind") == "page" and u.get("number")}
    done = 0
    for unit in units:
        text = read.get(unit.number, "")
        if _page_chars(unit.text) < MIN_TEXT_PER_PAGE and _page_chars(text) > _page_chars(unit.text):
            unit.text = text
            done += 1
    return done


def _is_contents_page(page) -> bool:
    """Detect an explicit contents heading near the top, not an arbitrary mention."""
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            if float(line.get("bbox", (0, page.rect.height))[1]) > page.rect.height * 0.2:
                continue
            label = " ".join(str(span.get("text") or "") for span in line.get("spans", [])).strip()
            if re.search(r"(?:^|[/|:–-]\s*)(?:table of contents|contents|"
                         r"tabla de contenidos|sumario|índice(?: general)?|"
                         r"indice(?: general)?)\s*$", label.casefold()):
                return True
    return False


def _has_sparse_cover(doc) -> bool:
    """Only treat page one as a cover when the next page is much denser."""
    if len(doc) < 3:
        return False
    first = len("".join((doc[0].get_text("text") or "").split()))
    second = len("".join((doc[1].get_text("text") or "").split()))
    return 0 < first < 200 and second >= first * 2


def _typographic_outline(doc) -> list[dict]:
    """Low-confidence heading candidates from unusually large text lines.

    This is deliberately narrower than PageIndex Flash: body-size text,
    repeated running headers and sentence-like lines are not headings.
    No candidates is an honest result, not a reason to invent structure.
    """
    spans: list[tuple[float, int]] = []
    lines: list[tuple[int, str, float, float, float, float]] = []
    sparse_cover = _has_sparse_cover(doc)
    for page_number, page in enumerate(doc, start=1):
        if (page_number == 1 and sparse_cover) or _is_contents_page(page):
            continue
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
                bbox = line.get("bbox", (0, 0, 0, 0))
                x, y = float(bbox[0]), float(bbox[1])
                if y < page.rect.height * 0.75:
                    lines.append((page_number, " ".join(label.split()), size,
                                  y, x, float(page.rect.width)))
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
    # A compact two-column page needs reading-order analysis that this small
    # fallback does not have. Leave it unstructured instead of inventing an
    # ordering from Y coordinates across both columns.
    columns: dict[int, dict[str, list[float]]] = {}
    for page, label, size, y, x, width in lines:
        if size > body_size * 1.1 or len(label) < 20:
            continue
        side = "left" if x < width * 0.45 else "right" if x > width * 0.52 else ""
        if side:
            columns.setdefault(page, {"left": [], "right": []})[side].append(y)
    for sides in columns.values():
        left, right = sides["left"], sides["right"]
        if (len(left) >= 3 and len(right) >= 3
                and max(min(left), min(right)) <= min(max(left), max(right))):
            return []

    for _, label, _, _, _, _ in lines:
        key = label.casefold()
        counts[key] = counts.get(key, 0) + 1
    candidates = [(page, label, size, y) for page, label, size, y, _, _ in lines
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
    if needs_ocr and ocr_enabled():
        if _ocr_thin_pages(path, units, page_count):
            total_chars = sum(_page_chars(unit.text) for unit in units)
            needs_ocr = total_chars < MIN_TEXT_PER_PAGE * page_count
    return Extracted(title=title, kind="pdf", units=finish(units),
                     needs_ocr=needs_ocr, pages=page_count, outline=outline,
                     outline_source=outline_source)
