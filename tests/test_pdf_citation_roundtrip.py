"""Physical page provenance through indexing, reopening, retrieval and tools."""

import pymupdf as fitz
import pytest

from borges.agent_tools import ReadArgs, run_read
from borges.chunking import INDEX_VERSION
from borges.indexer import Progress
from borges.services import Services
from conftest import make_config


def test_short_bookmarked_pages_keep_citations_after_restart_and_upgrade(tmp_path):
    folder = tmp_path / "library"
    folder.mkdir()
    path = folder / "evidence.pdf"
    with fitz.open() as pdf:
        pdf.set_metadata({"title": "Evidence manual"})
        pdf.new_page().insert_text((50, 70), "Zirconium calibration: 41 units.")
        pdf.new_page().insert_text((50, 70), "\n".join(
            ["Detailed operation instructions for the instrument."] * 12))
        pdf.new_page()  # no extractable text: must not return a neighbouring page
        pdf.new_page().insert_text((50, 70), "Cobalt correction: 17 units.")
        pdf.set_toc([[1, "Calibration", 1], [1, "Operation", 2],
                     [1, "Blank separator", 3], [1, "Correction", 4]])
        pdf.save(path)

    config = make_config(tmp_path)
    services = Services(config)
    services.worker.start()
    try:
        collection = services.add_collection("Evidence", str(folder), [], None, False, False)
        assert services.worker.wait_idle(30)
        doc_id = services.queries.list_documents(None, "", 10, None)["documents"][0]["id"]
        # Simulate an index from the previous page-merging version. The actual
        # restart must mark it stale and rebuild it before returning evidence.
        with services.db.transaction() as conn:
            conn.execute("UPDATE documents SET index_version = ? WHERE id = ?",
                         (INDEX_VERSION - 1, doc_id))
    finally:
        services.stop()

    services = Services(config)
    try:
        assert services.documents.count_stale() == 1
        services.start()
        assert services.worker.wait_idle(30)
        assert services.documents.count_stale() == 0
        document = services.queries.document(doc_id)
        assert document["structure_source"] == "bookmarks"
        assert [entry["page"] for entry in document["structure"]] == [1, 2, 3, 4]
        assert [unit["number"] for unit in document["outline"]] == [1, 2, 4]
        for query, page, value in [("Zirconium", 1, "41 units"), ("Cobalt", 4, "17 units")]:
            hits = services.search.search(query, "bm25", 5)["hits"]
            assert len(hits) == 1
            hit = hits[0]
            assert hit["page"] == page
            assert hit["citation"] == f"«Evidence manual», p. {page}"
            by_hit = run_read(services, ReadArgs(document_id=doc_id, chunk_id=hit["chunk_id"]))
            by_outline = run_read(services, ReadArgs(document_id=doc_id, page=page))
            assert by_hit == by_outline
            assert value in by_hit["text"]
            assert by_hit["citation"] == hit["citation"]
        with pytest.raises(LookupError):
            run_read(services, ReadArgs(document_id=doc_id, page=3))
        progress = Progress(collection_id=collection.id)
        services.indexer.index_collection(collection, progress)
        assert progress.files_changed == 0  # migration happens once
    finally:
        services.stop()
