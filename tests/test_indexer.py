"""Incremental indexing: unchanged files are skipped, modified ones re-extracted, deleted ones purged."""

import os
import time

from fixtures import make_md, make_txt

from borges.indexer import Matcher, Progress, glob_to_regex, walk


def docs_by_path(services):
    return {d["rel_path"]: d for d in services.queries.list_documents(None, "", 100, None)["documents"]}


def run(services, collection) -> Progress:
    progress = Progress(collection_id=collection.id)
    services.indexer.index_collection(collection, progress)
    assert progress.phase == "done", progress.message
    return progress


def test_first_pass_indexes_everything(indexed):
    services, collection = indexed
    docs = docs_by_path(services)
    assert set(docs) == {"apuntes.md", "catalogo.csv", "cuentos_valdeniebla.pdf", "ensayo.html", "escaneado.pdf", "ficciones.epub", "memoria_master.docx", "sub/budget.txt"}
    assert docs["escaneado.pdf"]["needs_ocr"] is True and docs["escaneado.pdf"]["chunks"] == 0
    counts = services.documents.counts()
    assert counts["documents"] == 8 and counts["needs_ocr"] == 1 and counts["errors"] == 0
    assert services.documents.count_without_embedding("fake-hash") == 0


def test_second_pass_touches_nothing(indexed):
    services, collection = indexed
    before = docs_by_path(services)
    progress = run(services, collection)
    assert progress.files_changed == 0 and progress.files_removed == 0 and progress.files_total == 8
    after = docs_by_path(services)
    assert {k: v["indexed_at"] for k, v in before.items()} == {k: v["indexed_at"] for k, v in after.items()}


def test_modified_file_is_the_only_one_reextracted(indexed, library):
    services, collection = indexed
    before = docs_by_path(services)
    path = library / "sub" / "budget.txt"
    make_txt(path, "Nuevo presupuesto para el observatorio de Valdeniebla y sus telescopios.")
    os.utime(path, (time.time() + 5, time.time() + 5))
    progress = run(services, collection)
    assert progress.files_changed == 1
    after = docs_by_path(services)
    assert after["sub/budget.txt"]["indexed_at"] > before["sub/budget.txt"]["indexed_at"]
    assert after["apuntes.md"]["indexed_at"] == before["apuntes.md"]["indexed_at"]
    hits = services.search.search("presupuesto observatorio", "bm25", 5)["hits"]
    assert hits and hits[0]["rel_path"] == "sub/budget.txt"
    assert not services.search.search("quarterly maintenance", "bm25", 5)["hits"]
    assert services.documents.count_without_embedding("fake-hash") == 0


def test_touched_but_identical_file_is_not_reextracted(indexed, library):
    services, collection = indexed
    before = docs_by_path(services)
    path = library / "apuntes.md"
    os.utime(path, (time.time() + 9, time.time() + 9))
    progress = run(services, collection)
    assert progress.files_changed == 0
    assert docs_by_path(services)["apuntes.md"]["indexed_at"] == before["apuntes.md"]["indexed_at"]


def test_deleted_file_is_purged(indexed, library):
    services, collection = indexed
    doc_id = docs_by_path(services)["apuntes.md"]["id"]
    (library / "apuntes.md").unlink()
    progress = run(services, collection)
    assert progress.files_removed == 1
    assert "apuntes.md" not in docs_by_path(services)
    assert services.queries.document(doc_id) is None
    with services.db.lock:
        assert services.db.conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc_id,)).fetchone()[0] == 0
        assert services.db.conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'olvido'").fetchone()[0] == 0


def test_new_file_is_added_and_embedded(indexed, library):
    services, collection = indexed
    make_md(library / "nuevo.md")
    progress = run(services, collection)
    assert progress.files_changed == 1 and progress.chunks_embedded > 0
    assert "nuevo.md" in docs_by_path(services)


def test_broken_file_records_error_without_stopping(indexed, library):
    services, collection = indexed
    (library / "roto.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    progress = run(services, collection)
    assert progress.errors and progress.errors[0]["path"] == "roto.pdf"
    docs = docs_by_path(services)
    assert docs["roto.pdf"]["status"] == "error" and docs["roto.pdf"]["error"]
    assert services.documents.counts()["errors"] == 1


def test_globs_include_exclude(library, services):
    from borges.store import Collection

    base = dict(id=1, name="x", path=str(library), enabled=True, watch=False, code=False, created_at=0.0, last_indexed_at=None)
    only_pdf = walk(Collection(include=["**/*.pdf"], exclude=[], **base))
    assert [rel for rel, _ in only_pdf] == ["cuentos_valdeniebla.pdf", "escaneado.pdf"]
    no_sub = walk(Collection(include=[], exclude=["sub/**"], **base))
    assert all(not rel.startswith("sub/") for rel, _ in no_sub) and len(no_sub) == 7
    with_code = Collection(include=[], exclude=[], **{**base, "code": True})
    (library / "script.py").write_text("print('hola')")
    assert "script.py" in [rel for rel, _ in walk(with_code)]
    assert "script.py" not in [rel for rel, _ in walk(Collection(include=[], exclude=[], **base))]


def test_glob_translation():
    assert glob_to_regex("**/*.pdf").match("a/b/c.PDF")
    assert glob_to_regex("**/*.pdf").match("c.pdf")
    assert not glob_to_regex("*.pdf").match("a/c.pdf")
    assert glob_to_regex("**/node_modules/**").match("x/node_modules/y/z.md")
    matcher = Matcher([], ["**/.*"])
    assert matcher.excludes_dir(".git") and matcher.excludes_dir("a/.obsidian") and not matcher.excludes_dir("a/b")


def test_documents_below_index_version_are_rechunked_once(indexed):
    services, collection = indexed
    from borges.chunking import INDEX_VERSION

    before = docs_by_path(services)
    assert all(d["index_version"] == INDEX_VERSION for d in before.values())
    with services.db.transaction() as conn:
        conn.execute("UPDATE documents SET index_version = 0 WHERE rel_path IN ('apuntes.md', 'ensayo.html')")
    assert services.documents.count_stale() == 2
    assert services.status()["reindex_needed"] == 2
    progress = run(services, collection)
    assert progress.files_changed == 2  # only the stale ones were re-extracted, nothing else touched
    after = docs_by_path(services)
    assert services.documents.count_stale() == 0 and after["apuntes.md"]["index_version"] == INDEX_VERSION
    assert after["apuntes.md"]["indexed_at"] > before["apuntes.md"]["indexed_at"]
    assert after["catalogo.csv"]["indexed_at"] == before["catalogo.csv"]["indexed_at"]
    assert run(services, collection).files_changed == 0


def test_stale_documents_are_reindexed_at_startup(tmp_path, library):
    from borges.services import Services
    from conftest import make_config

    first = Services(make_config(tmp_path))
    first.worker.start()
    first.add_collection("Pruebas", str(library), [], None, False, False)
    assert first.worker.wait_idle(60)
    with first.db.transaction() as conn:
        conn.execute("UPDATE documents SET index_version = 0")
    first.stop()

    second = Services(make_config(tmp_path))  # autostart=False, but stale documents still trigger the reindex
    second.start()
    try:
        assert second.worker.wait_idle(60)
        assert second.documents.count_stale() == 0
    finally:
        second.stop()


def test_short_sections_are_merged_in_the_index(indexed):
    services, _ = indexed
    doc = next(d for d in docs_by_path(services).values() if d["rel_path"] == "apuntes.md")
    outline = services.queries.document(doc["id"])["outline"]
    assert [u["title"] for u in outline] == ["Funes", "El jardín", "Biblioteca"]  # the two-line intro rode into Funes
    assert "Notas sueltas" in services.queries.unit(doc["id"], section=2)["text"]
