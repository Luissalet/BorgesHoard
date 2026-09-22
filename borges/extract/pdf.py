"""PDF extraction with PyMuPDF: one unit per page, page numbers preserved; scanned PDFs are flagged."""

from __future__ import annotations

from pathlib import Path

from .base import Extracted, Unit, finish

MIN_TEXT_PER_PAGE = 25  # average non-blank chars per page below which we assume a scan


def extract_pdf(path: Path) -> Extracted:
    import pymupdf as fitz

    units: list[Unit] = []
    with fitz.open(str(path)) as doc:
        meta_title = (doc.metadata or {}).get("title") or ""
        page_count = doc.page_count
        total_chars = 0
        for index, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            total_chars += len("".join(text.split()))
            units.append(Unit(kind="page", number=index, text=text))
    title = meta_title.strip() or path.stem
    needs_ocr = page_count > 0 and total_chars < MIN_TEXT_PER_PAGE * page_count
    return Extracted(title=title, kind="pdf", units=finish(units), needs_ocr=needs_ocr, pages=page_count)
