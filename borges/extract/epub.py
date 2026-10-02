"""EPUB extraction: one chapter per document of the book, in reading order (the spine first, then any other document the
manifest lists), titled by its first heading. The navigation document (the table of contents page) is not a chapter. Reading
the zip, decoding the pages and turning HTML into text use the shared `hoard_link.docs` (zip-bomb check, `html_to_text`)."""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote

from ..hoard_link.docs.readers_lite import check_zip
from ..hoard_link.docs.textclean import decode_text
from .base import Extracted, Unit, finish
from .html import html_sections

MAX_PAGE_BYTES = 20_000_000
HTML_TYPES = ("application/xhtml+xml", "text/html")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _member(z: zipfile.ZipFile, name: str, limit: int = MAX_PAGE_BYTES) -> bytes:
    if z.getinfo(name).file_size > limit:
        raise ValueError(f"{name} is larger than {limit // 1_000_000} MB")
    return z.read(name)


def extract_epub(path: Path) -> Extracted:
    with zipfile.ZipFile(path) as z:
        check_zip(z)
        container = ET.fromstring(_member(z, "META-INF/container.xml"))
        rootfile = next((el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile"), None)
        if not rootfile:
            raise ValueError("EPUB without a rootfile")
        opf = ET.fromstring(_member(z, rootfile))
        base = posixpath.dirname(rootfile)
        title, manifest, spine = "", {}, []
        for el in opf.iter():
            name = _local(el.tag)
            if name == "item":
                manifest[el.get("id")] = (el.get("href") or "", el.get("media-type") or "", el.get("properties") or "")
            elif name == "itemref":
                spine.append(el.get("idref"))
            elif name == "title" and not title:
                title = (el.text or "").strip()
        documents = [i for i, (href, media, props) in manifest.items() if href and media in HTML_TYPES and "nav" not in props.split()]
        ordered = [i for i in spine if i in documents] + [i for i in documents if i not in spine]
        names = set(z.namelist())
        units: list[Unit] = []
        for item_id in ordered:
            member = posixpath.normpath(posixpath.join(base, unquote(manifest[item_id][0].split("#")[0])))
            if member not in names:
                continue
            chapter_title, sections = html_sections(decode_text(_member(z, member)), unit_kind="chapter")
            first = next((s.title for s in sections if s.title), "")
            units.append(Unit(kind="chapter", number=len(units) + 1, title=first or chapter_title,
                              text="\n\n".join(s.text for s in sections)))
    return Extracted(title=title or path.stem, kind="epub", units=finish(units))
