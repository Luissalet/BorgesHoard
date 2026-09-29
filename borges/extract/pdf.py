"""PDF extraction with PyMuPDF: one unit per page, page numbers preserved; scanned PDFs are flagged."""

from __future__ import annotations

from pathlib import Path

from .base import Extracted, Unit, finish

MIN_TEXT_PER_PAGE = 25  # average non-blank chars per page below which we assume a scan
PDF_STRUCTURE_VERSION = 1  # re-extract older PDFs once to populate declared bookmarks


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
                     outline_source="bookmarks" if outline else "none")
