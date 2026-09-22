"""HTML and EPUB extraction (BeautifulSoup): HTML → sections by h1–h3; EPUB → one chapter per spine item."""

from __future__ import annotations

import warnings
from pathlib import Path

from .base import Extracted, Unit, finish
from .text import read_text

HEADINGS = ("h1", "h2", "h3")
DROP = ("script", "style", "noscript", "svg", "nav", "head")


def _soup(markup: str | bytes):
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:  # pragma: no cover - lxml missing
        return BeautifulSoup(markup, "html.parser")


def html_sections(markup: str | bytes, unit_kind: str = "section") -> tuple[str, list[Unit]]:
    """Split a document into units at h1–h3, keeping text order. Returns (title, units)."""
    soup = _soup(markup)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup.find_all(DROP):
        tag.decompose()
    body = soup.body or soup
    units: list[Unit] = [Unit(kind=unit_kind, number=1, title="")]
    for element in body.descendants:
        if getattr(element, "name", None) in HEADINGS:
            heading = element.get_text(" ", strip=True)
            if heading:
                units.append(Unit(kind=unit_kind, number=len(units) + 1, title=heading))
            continue
        if isinstance(element, str):
            parent = element.parent.name if element.parent is not None else ""
            if parent in HEADINGS:
                continue
            piece = str(element)
            if piece.strip():
                units[-1].text += piece
            elif "\n" in piece:
                units[-1].text += "\n"
        elif element.name in ("p", "br", "div", "li", "tr", "h4", "h5", "h6", "blockquote", "pre"):
            units[-1].text += "\n"
    return title, units


def extract_html(path: Path) -> Extracted:
    title, units = html_sections(read_text(path))
    return Extracted(title=title or path.stem, kind="html", units=finish(units))


def extract_epub(path: Path) -> Extracted:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import ebooklib
        from ebooklib import epub

        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    titles = book.get_metadata("DC", "title")
    title = titles[0][0].strip() if titles else path.stem
    items_by_id = {item.get_id(): item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
    ordered = [items_by_id[idref] for idref, _linear in book.spine if idref in items_by_id]
    ordered += [item for item in items_by_id.values() if item not in ordered]
    units: list[Unit] = []
    for item in ordered:
        chapter_title, sections = html_sections(item.get_content(), unit_kind="chapter")
        text = "\n\n".join(s.text for s in sections)
        first = next((s.title for s in sections if s.title), "")
        units.append(Unit(kind="chapter", number=len(units) + 1, title=first or chapter_title, text=text))
    return Extracted(title=title, kind="epub", units=finish(units))
