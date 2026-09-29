"""Check persisted PDF navigation, literal retrieval and physical-page citations.

    python scripts/check_pdf_provenance.py path/to/document.pdf

Uses a temporary index and fake embeddings; evaluates BM25, not semantic recall.
The input PDF is only read. No model download or production index is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf as fitz

from borges.agent_tools import ReadArgs, run_read
from borges.config import Config
from borges.extract.base import clean_text
from borges.services import Services


def check(path: Path) -> dict:
    with fitz.open(path) as pdf:
        original = {i: clean_text(page.get_text("text") or "")
                    for i, page in enumerate(pdf, 1)}
    with tempfile.TemporaryDirectory(prefix="borges-provenance-") as temporary:
        root = Path(temporary)
        folder = root / "library"
        folder.mkdir()
        shutil.copy2(path, folder / path.name)
        config = Config(data_dir=root / "data", embed_backend="fake", watch=False,
                        autostart=False, data_dir_configured=True)
        services = Services(config)
        services.worker.start()
        try:
            services.add_collection("provenance", str(folder), [], None, False, False)
            assert services.worker.wait_idle(60), "Indexing timed out"
            assert services.documents.counts()["errors"] == 0, "Extraction error"
            document = services.queries.list_documents(None, "", 10, None)["documents"][0]
            doc_id = document["id"]
            before = services.queries.document(doc_id)
        finally:
            services.stop()

        services = Services(config)
        try:
            after = services.queries.document(doc_id)
            assert after["structure"] == before["structure"], "Structure lost on reopen"
            expected_pages = [number for number, text in original.items() if text]
            assert [unit["number"] for unit in after["outline"]] == expected_pages
            checked = 0
            for number, text in original.items():
                unit = services.queries.unit(doc_id, page=number)
                assert (unit["text"] if unit else "") == text, f"Page {number} changed"
                if not text:
                    continue
                # A page-exclusive literal term gives a deterministic retrieval
                # target without pretending to evaluate semantic relevance.
                words = re.findall(r"\b[a-zA-Z]{6,}\b", text)
                term = next((word for word in words if all(
                    word.casefold() not in other.casefold()
                    for page, other in original.items() if page != number)), None)
                if term is None:
                    continue
                hits = services.search.search(term, "bm25", 30)["hits"]
                matching = [hit for hit in hits if hit["page"] == number]
                assert matching, f"Missing literal retrieval on page {number}: {term}"
                hit = matching[0]
                read = run_read(services, ReadArgs(document_id=doc_id, chunk_id=hit["chunk_id"]))
                assert read["unit"]["number"] == number
                assert read["citation"] == hit["citation"] == f"«{document['title']}», p. {number}"
                checked += 1
            for entry in after["structure"]:
                page = entry["page"]
                assert page in original, f"Invalid structure destination: {page}"
                if original[page]:
                    read = run_read(services, ReadArgs(document_id=doc_id, page=page))
                    assert read["unit"]["number"] == page
            return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "pages": len(original), "persisted_nonempty_pages": len(expected_pages),
                    "structure_source": after["structure_source"],
                    "structure_destinations_checked": len(after["structure"]),
                    "literal_retrieval_pages_checked": checked,
                    "retrieval_pages_without_exclusive_term": len(expected_pages) - checked,
                    "passed": True}
        finally:
            services.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.pdf.resolve()), ensure_ascii=False, indent=2))
