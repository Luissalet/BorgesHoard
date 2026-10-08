"""The domain code that now comes from the Hoard Link commons: folder policy, citations, chunk boundaries, extractors,
OCR routing of scanned PDFs and the dense ranking."""

import zipfile
import os

import numpy as np
import pytest
from conftest import make_config
from fixtures import FUNES, JARDIN, make_docx, make_epub, make_html, make_pdf, make_scanned_pdf

from borges.chunking import INDEX_VERSION, chunk_units, merge_small_units
from borges.extract import extract
from borges.extract import pdf as pdf_module
from borges.extract.base import Unit
from borges.queries import citation
from borges.services import Services


# ---------------- add_collection: the shared folder policy ----------------

def test_add_collection_accepts_a_quoted_pasted_path(services, library):
    first = services.add_collection("Quoted", f'  "{library}"  ', [], None, False, False)
    assert first.path == str(library.resolve())
    assert services.add_collection("Again", f"'{library}'", [], None, False, False).id == first.id  # idempotent through the quotes


SYSTEM_FOLDERS = ["/", os.environ["SystemRoot"], os.environ["ProgramFiles"]] if os.name == "nt" else ["/", "/etc", "/usr/lib"]


@pytest.mark.parametrize("bad", SYSTEM_FOLDERS)
def test_add_collection_refuses_broad_and_system_folders(services, bad):
    with pytest.raises(ValueError, match="cannot be indexed"):
        services.add_collection("Nope", bad, [], None, False, False)


def test_add_collection_refuses_secret_folders_and_the_data_folder(services, tmp_path):
    secret = tmp_path / "me" / ".ssh"
    secret.mkdir(parents=True)
    with pytest.raises(ValueError, match="cannot be indexed"):
        services.add_collection("Keys", str(secret), [], None, False, False)
    with pytest.raises(ValueError, match="own data folder"):
        services.add_collection("Self", str(services.config.data_dir), [], None, False, False)
    with pytest.raises(ValueError, match="contains the app's own data folder"):
        services.add_collection("Parent", str(services.config.data_dir.parent), [], None, False, False)
    with pytest.raises(ValueError, match="The folder does not exist"):
        services.add_collection("Gone", str(tmp_path / "nope"), [], None, False, False)


def test_add_collection_policy_over_http_and_tool(client):
    refused = client.post("/api/collections", json={"path": SYSTEM_FOLDERS[1]})
    assert refused.status_code == 400 and "cannot be indexed" in refused.text
    auth = {"Authorization": f"Bearer {client.services.token}"}
    via_tool = client.post("/api/agent/call", json={"name": "library_add_collection", "arguments": {"path": str(client.services.config.data_dir)}}, headers=auth)
    assert via_tool.status_code == 400 and "own data folder" in via_tool.json()["error"]


# ---------------- citations (the shared wording) ----------------

def test_citations_read_as_they_always_did():
    pdf = {"kind": "pdf", "title": "Cuentos", "filename": "c.pdf"}
    assert citation(pdf, 12, "") == "«Cuentos», p. 12" and citation(pdf, None, "") == "«Cuentos»"
    assert citation({"kind": "epub", "title": "Ficciones", "filename": "f.epub"}, None, "3") == "«Ficciones», cap. 3"
    assert citation({"kind": "epub", "title": "Ficciones", "filename": "f.epub"}, None, "") == "«Ficciones»"
    md = {"kind": "md", "title": "Apuntes", "filename": "apuntes.md"}
    assert citation(md, None, "Funes", 5) == "apuntes.md § Funes" and citation(md, None, "", 40) == "apuntes.md, l. 40" and citation(md, None, "") == "apuntes.md"
    chat = {"kind": "chat", "title": "x", "filename": "", "meta": {"title": "Viaje", "date": "2026-09-30"}}
    assert citation(chat, None, "turno 4") == "[chat «Viaje» · 2026-09-30 · turno 4]" and citation({**chat, "meta": {}}, None, "") == "[chat «x»]"
    link = {"kind": "link", "title": "t", "filename": "", "meta": {"title": "Receta de pan", "site": "example.org"}}
    assert citation(link, None, "") == "[enlace «Receta de pan» · example.org]" and citation({**link, "meta": {"title": "R"}}, None, "") == "[enlace «R»]"


# ---------------- chunking: same boundaries, same index version ----------------

def test_chunk_boundaries_are_the_ones_the_library_was_indexed_with():
    """Pinned from the app's own chunker before it moved to the commons: a change here would re-chunk every library."""
    assert INDEX_VERSION == 4
    text = " ".join(f"Frase número {i} sobre la aldea imaginaria de Valdeniebla y sus bibliotecas infinitas." for i in range(30))
    units = [Unit(kind="page", number=1, text=text), Unit(kind="page", number=2, text="Corta."), Unit(kind="section", number=3, title="Fin", text="Z " * 300, line_start=5)]
    chunks = chunk_units(merge_small_units(units))
    assert [(c.unit_index, c.ordinal, c.page, c.section, c.line, c.char_start, c.char_end) for c in chunks] == [
        (0, 0, 1, "", None, 0, 849), (0, 1, 1, "", None, 701, 1537), (0, 2, 1, "", None, 1391, 2225), (0, 3, 1, "", None, 2079, 2569),
        (1, 4, 2, "", None, 0, 6), (2, 5, None, "Fin", 5, 0, 599)]


# ---------------- extractors ----------------

def test_docx_sections_tables_lists_and_title(tmp_path):
    result = extract(make_docx(tmp_path / "memoria.docx"))
    assert result.kind == "docx" and result.title and result.units
    assert [u.title for u in result.units if u.title][:1] == ["Introducción"]
    assert all(u.kind == "section" for u in result.units)
    assert [u.number for u in result.units] == list(range(1, len(result.units) + 1))


def test_a_damaged_docx_is_an_error_not_an_empty_document(tmp_path):
    bad = tmp_path / "roto.docx"
    bad.write_bytes(b"this is not a zip file")
    with pytest.raises(ValueError):
        extract(bad)
    notword = tmp_path / "otro.docx"
    with zipfile.ZipFile(notword, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(ValueError):
        extract(notword)


def test_epub_gives_one_titled_chapter_per_spine_item(tmp_path):
    result = extract(make_epub(tmp_path / "ficciones.epub"))
    assert result.kind == "epub" and result.title == "Ficciones de Valdeniebla"
    assert [u.title for u in result.units] == ["Funes el memorioso", "El jardín", "La biblioteca"]
    assert all(u.kind == "chapter" for u in result.units) and "hoja de cada árbol" in result.units[0].text


def test_html_sections_by_heading_drop_scripts_styles_and_nav(tmp_path):
    page = tmp_path / "ensayo.html"
    page.write_text("<html><head><title>Ensayo breve</title><style>h1{}</style><script>var h2='<h2>no</h2>'</script></head><body>"
                    "<nav>Inicio | Contacto</nav><p>Preámbulo largo.</p><h1>Uno</h1><p>Texto del uno.</p>"
                    "<h2>Dos <em>bis</em></h2><table><tr><td>a</td><td>b</td></tr></table></body></html>", encoding="utf-8")
    result = extract(page)
    assert result.title == "Ensayo breve" and result.kind == "html"
    assert [(u.title, u.text) for u in result.units] == [("", "Preámbulo largo."), ("Uno", "Texto del uno."), ("Dos bis", "a | b")]


def test_html_without_headings_is_one_section(tmp_path):
    page = make_html(tmp_path / "plano.html")
    result = extract(page)
    assert result.units and result.title


def test_text_files_decode_by_bom_then_utf8_then_windows_1252(tmp_path):
    utf16 = tmp_path / "u16.txt"
    utf16.write_bytes("Ñandú corre en la aldea\n".encode("utf-16"))
    assert "Ñandú corre" in extract(utf16).units[0].text
    cp = tmp_path / "cp.txt"
    cp.write_bytes("Año nuevo, vida nueva".encode("cp1252"))
    assert extract(cp).units[0].text == "Año nuevo, vida nueva"
    plain = tmp_path / "ok.md"
    plain.write_text("# Título\nCuerpo con acentos: éáú\n", encoding="utf-8-sig")
    got = extract(plain)
    assert got.title == "Título" and "éáú" in got.units[0].text


def test_extracted_text_joins_words_split_at_a_line_end(tmp_path):
    page = tmp_path / "guion.txt"
    page.write_text("La informa-\nción llegó tarde a la aldea de Valdeniebla.\n", encoding="utf-8")
    assert "información llegó" in extract(page).units[0].text


# ---------------- scanned PDFs through the family OCR ----------------

class FakeDocs:
    """Stands for `hoard_link.fam_docs` (Kafka's Hoard through the hub)."""

    def __init__(self, up=True, pages=None, ok=True):
        self.up, self.pages, self.ok, self.calls = up, pages or {}, ok, []

    def available(self, timeout=1.0):
        return self.up

    def extract(self, path, **kw):
        self.calls.append((path, kw))
        if not self.ok:
            return {"ok": False, "error": "OCR engine missing", "kind": "tool_error", "via": "kafka"}
        return {"ok": True, "via": "kafka", "units": [{"kind": "page", "number": n, "title": "", "text": t} for n, t in self.pages.items()]}


def test_a_scan_is_read_through_the_family_ocr(tmp_path, monkeypatch):
    fake = FakeDocs(pages={1: FUNES, 2: JARDIN})
    monkeypatch.setattr(pdf_module, "fam_docs", fake)
    result = extract(make_scanned_pdf(tmp_path / "scan.pdf"))
    assert result.needs_ocr is False and [u.number for u in result.units] == [1, 2] and all(u.kind == "page" for u in result.units)
    assert "hoja de cada árbol" in result.units[0].text and "jardín" in result.units[1].text
    path, kw = fake.calls[0]
    assert path.endswith("scan.pdf") and kw["ocr"] == "auto" and kw["local_fallback"] is False and kw["lang"] == "es"


def test_a_pdf_with_text_is_never_sent_to_the_ocr_service(tmp_path, monkeypatch):
    fake = FakeDocs(pages={1: "OTRO TEXTO QUE NO DEBE USARSE " * 30, 2: JARDIN})
    monkeypatch.setattr(pdf_module, "fam_docs", fake)
    result = extract(make_pdf(tmp_path / "texto.pdf"))
    assert not result.needs_ocr and not fake.calls and "OTRO TEXTO" not in result.units[0].text


def test_only_the_thin_pages_of_a_mostly_scanned_pdf_take_the_ocr_text(tmp_path, monkeypatch):
    import pymupdf as fitz

    path = tmp_path / "mixto.pdf"
    doc = fitz.open()
    for text in ("corta", "", ""):  # almost no text layer overall: the document counts as a scan
        page = doc.new_page()
        if text:
            page.insert_textbox(fitz.Rect(50, 50, 550, 780), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    monkeypatch.setattr(pdf_module, "fam_docs", FakeDocs(pages={1: "OTRO TEXTO QUE NO DEBE USARSE", 2: FUNES, 3: JARDIN}))
    result = extract(path)
    by_page = {u.number: u.text for u in result.units}
    assert "OTRO TEXTO" in by_page[1]  # a page under 25 characters counts as unread: it takes the longer OCR text
    assert "hoja de cada árbol" in by_page[2] and "jardín" in by_page[3] and not result.needs_ocr


def test_a_scan_stays_unread_when_the_service_is_down_fails_or_is_switched_off(tmp_path, monkeypatch):
    scan = make_scanned_pdf(tmp_path / "scan.pdf")
    for fake, asked in ((FakeDocs(up=False, pages={1: FUNES}), 0), (FakeDocs(ok=False), 1)):
        monkeypatch.setattr(pdf_module, "fam_docs", fake)
        result = extract(scan)
        assert result.needs_ocr is True and result.units == []
        assert len(fake.calls) == asked  # down: not even asked; failing: asked once, the answer is ignored
    fake = FakeDocs(pages={1: FUNES, 2: JARDIN})
    monkeypatch.setattr(pdf_module, "fam_docs", fake)
    monkeypatch.setenv("BORGES_OCR", "0")
    assert extract(scan).needs_ocr is True and not fake.calls


def test_a_scan_listed_without_text_is_read_once_the_service_is_running(tmp_path, monkeypatch):
    folder = tmp_path / "scans"
    folder.mkdir()
    make_scanned_pdf(folder / "escaneo.pdf")
    monkeypatch.setattr(pdf_module, "fam_docs", FakeDocs(up=False))
    svc = Services(make_config(tmp_path))
    svc.worker.start()
    try:
        svc.add_collection("Scans", str(folder), [], None, False, False)
        assert svc.worker.wait_idle(60)
        row = svc.documents.fingerprints(svc.collections.list()[0].id)["escaneo.pdf"]
        assert row[7] == 1  # needs_ocr
        assert not svc.search.bm25("memorioso", 5, None)
        monkeypatch.setattr(pdf_module, "fam_docs", FakeDocs(pages={1: FUNES, 2: JARDIN}))
        svc.reindex(svc.collections.list()[0].id)
        assert svc.worker.wait_idle(60)
        row = svc.documents.fingerprints(svc.collections.list()[0].id)["escaneo.pdf"]
        assert row[7] == 0 and svc.search.bm25("memorioso", 5, None)
    finally:
        svc.stop()


# ---------------- dense ranking through the shared vector helpers ----------------

def test_dense_respects_collection_filter_exclusion_and_threshold(indexed):
    services, collection = indexed
    chunk_id = services.queries.chunk_ids_for_collections({collection.id}).pop()
    vector = services.docs.embedding_for(chunk_id, services.embedder.model) if hasattr(services, "docs") else services.documents.embedding_for(chunk_id, services.embedder.model)
    everything = services.search.dense("", 10, None, vector=vector)
    assert everything[0][0] == chunk_id and everything[0][1] == pytest.approx(1.0, abs=1e-4)
    assert all(score >= 0.2 for _, score in everything) and [s for _, s in everything] == sorted((s for _, s in everything), reverse=True)
    assert chunk_id not in [c for c, _ in services.search.dense("", 10, None, vector=vector, exclude=chunk_id)]
    assert services.search.dense("", 10, {collection.id + 99}, vector=vector) == []
    inside = services.search.dense("", 10, {collection.id}, vector=vector)
    assert inside == everything
    assert services.search.dense("", 10, set(), vector=vector) == []
