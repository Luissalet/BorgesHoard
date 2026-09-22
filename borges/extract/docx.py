"""DOCX extraction with python-docx: headings open sections, paragraphs fill them, tables are appended as rows."""

from __future__ import annotations

from pathlib import Path

from .base import Extracted, Unit, finish

HEADING_PREFIXES = ("heading", "título", "titulo", "title")


def _is_heading(paragraph) -> bool:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    return style.lower().startswith(HEADING_PREFIXES)


def _iter_blocks(document):
    """Yield paragraphs and tables in document order."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def extract_docx(path: Path) -> Extracted:
    import docx
    from docx.table import Table

    document = docx.Document(str(path))
    units: list[Unit] = [Unit(kind="section", number=1, title="")]
    title = ""
    for block in _iter_blocks(document):
        if isinstance(block, Table):
            rows = []
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                units[-1].text += "\n" + "\n".join(rows) + "\n"
            continue
        text = block.text
        if _is_heading(block) and text.strip():
            style = (block.style.name or "").lower()
            if style.startswith("title") and not title:
                title = text.strip()
            units.append(Unit(kind="section", number=len(units) + 1, title=text.strip()))
            continue
        units[-1].text += text + "\n"
    core_title = (document.core_properties.title or "").strip()
    return Extracted(title=core_title or title or path.stem, kind="docx", units=finish(units))
