"""PowerPoint text, tables and speaker notes through the family reader.

Physical slide numbers survive empty slides and presentation reordering.
Images, animations and embedded charts are not OCR'd or executed.
"""
from pathlib import Path

from ..hoard_link.docs import readers_lite
from .base import Extracted, Unit, finish


def extract_pptx(path: Path) -> Extracted:
    items = readers_lite.read_pptx(path.read_bytes())
    units = [Unit(kind="slide", number=item["number"], title=item["title"],
                  text="\n".join(dict.fromkeys(part for part in (item["title"], item["text"]) if part)))
             for item in items]
    return Extracted(title=path.stem, kind="pptx", units=finish(units))
