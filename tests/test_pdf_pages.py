"""Per-page text-layer / OCR decision for PDFs, on synthetic PDFs with a mocked OCR / vision backend."""

import pymupdf as fitz
import pytest
from fixtures import BIBLIOTECA, FUNES, JARDIN, make_md, make_pdf, make_scanned_pdf

from borges.extract import extract
from borges.extract import pdf as pdf_module
from borges.extract.pdf_pages import assess_page, page_ranges, running_lines

SCANNED_TEXT = "Texto reconocido por el motor de OCR en una pagina escaneada del cuaderno de campo de Valdeniebla."


class SpyOcr:
    """Stands for `hoard_link.fam_docs`: records which pages it was asked to read."""

    def __init__(self, up=True, texts=None, backend="paddleocr", ok=True):
        self.up, self.texts, self.backend, self.ok, self.calls = up, texts or {}, backend, ok, []

    def available(self, timeout=1.0):
        return self.up

    def ocr_pdf(self, path, **kw):
        self.calls.append(kw)
        if not self.ok:
            return {"ok": False, "error": "boom", "kind": "tool_error"}
        wanted = set()
        for part in kw["pages"].split(","):
            lo, _, hi = part.partition("-")
            wanted.update(range(int(lo), int(hi or lo) + 1))
        return {"ok": True, "backend": self.backend,
                "pages": [{"page": n, "text": t} for n, t in self.texts.items() if n in wanted]}

    @property
    def pages_asked(self):
        asked = []
        for kw in self.calls:
            for part in kw["pages"].split(","):
                lo, _, hi = part.partition("-")
                asked.extend(range(int(lo), int(hi or lo) + 1))
        return asked


def make_mixed_pdf(path):
    """Page 1 and 4 real text, page 2 an image only, page 3 nothing but a running header and a page number."""
    doc = fitz.open()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    pix.clear_with(180)
    for number in (1, 2, 3, 4):
        page = doc.new_page()
        if number == 2:
            page.insert_image(fitz.Rect(100, 100, 300, 300), pixmap=pix)
            continue
        page.insert_text((50, 40), "Informe anual de Valdeniebla", fontsize=9, fontname="helv")
        if number != 3:
            page.insert_textbox(fitz.Rect(50, 60, 550, 700), FUNES if number == 1 else JARDIN, fontsize=11, fontname="helv")
        page.insert_text((290, 800), f"Page {number}", fontsize=9, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def methods(result):
    return {e["page"]: e["method"] for e in result.page_methods}


# ---------------- the heuristic ----------------

def test_heuristic_accepts_real_text_and_refuses_numbers_headers_and_garbage():
    assert assess_page(FUNES).usable
    assert assess_page("  ").reason == "no_text_layer"
    assert assess_page("12").reason == "only_headers_or_page_numbers"
    assert assess_page("Page 4 of 10").reason == "only_headers_or_page_numbers"
    assert assess_page("Capitulo uno").reason == "too_little_text"
    assert assess_page("\ufffd\ufffd\ufffd \ufffd\ufffd " * 30).reason in ("garbage_text", "no_text_layer")
    assert assess_page("\ufffd\ufffd\ufffd lorem ipsum dolor sit amet consectetur adipiscing \ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd").reason == "garbage_text"
    assert assess_page("@@ ## $$ %% ^^ && ** ((  )) __ ++ == ~~ || xx") .usable is False
    assert assess_page("1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23").reason == "too_few_words"


def test_running_headers_do_not_count_as_text():
    pages = [f"Informe anual de Valdeniebla {n}\nResumen\n{n}" for n in (1, 2, 3, 4)]
    running = running_lines(pages)
    assert running and all(assess_page(p, running).reason == "only_headers_or_page_numbers" for p in pages)
    assert assess_page(pages[0] + "\n" + FUNES, running).usable
    assert running_lines(pages[:2]) == frozenset()  # too few pages to call anything a running header


def test_page_ranges():
    assert page_ranges([5, 1, 2, 3, 9, 3]) == "1-3,5,9" and page_ranges([4]) == "4" and page_ranges([]) == ""


# ---------------- extraction ----------------

def test_text_pdf_never_reaches_the_ocr_backend(tmp_path, monkeypatch):
    spy = SpyOcr(texts={1: "NO", 2: "NO", 3: "NO"})
    monkeypatch.setattr(pdf_module, "fam_docs", spy)
    result = extract(make_pdf(tmp_path / "texto.pdf"))
    assert spy.calls == [] and methods(result) == {1: "text_layer", 2: "text_layer", 3: "text_layer"}
    assert all(e["reason"] == "usable_text_layer" and e["chars"] > 100 for e in result.page_methods)
    assert not result.needs_ocr and result.ocr_forced is False


def test_image_only_pdf_goes_to_ocr_page_by_page(tmp_path, monkeypatch):
    spy = SpyOcr(texts={1: SCANNED_TEXT, 2: SCANNED_TEXT})
    monkeypatch.setattr(pdf_module, "fam_docs", spy)
    result = extract(make_scanned_pdf(tmp_path / "scan.pdf"))
    assert spy.pages_asked == [1, 2] and methods(result) == {1: "ocr", 2: "ocr"}
    assert result.page_methods[0]["reason"] == "no_text_layer" and not result.needs_ocr


def test_mixed_pdf_only_sends_the_pages_without_a_usable_text_layer(tmp_path, monkeypatch):
    spy = SpyOcr(texts={2: SCANNED_TEXT, 3: "no debe usarse"})
    monkeypatch.setattr(pdf_module, "fam_docs", spy)
    result = extract(make_mixed_pdf(tmp_path / "mixto.pdf"))
    assert spy.pages_asked == [2, 3]  # never pages 1 and 4
    by_page = {e["page"]: e for e in result.page_methods}
    assert by_page[1]["method"] == "text_layer" and by_page[4]["method"] == "text_layer"
    assert by_page[2]["method"] == "ocr" and by_page[2]["reason"] == "no_text_layer"
    assert by_page[3]["method"] == "empty" and by_page[3]["reason"] == "only_headers_or_page_numbers" and by_page[3]["ocr_note"] == "no_text_found"
    texts = {u.number: u.text for u in result.units}
    assert "Funes" in texts[1] and "OCR" in texts[2] and "bifurcan" in texts[4]


def test_a_vision_backend_is_reported_as_vision(tmp_path, monkeypatch):
    spy = SpyOcr(texts={1: SCANNED_TEXT, 2: SCANNED_TEXT}, backend="qwen2.5-vl")
    monkeypatch.setattr(pdf_module, "fam_docs", spy)
    assert set(methods(extract(make_scanned_pdf(tmp_path / "scan.pdf"))).values()) == {"vision"}


def test_without_a_backend_the_pages_are_reported_empty_with_the_reason(tmp_path, monkeypatch):
    result = extract(make_mixed_pdf(tmp_path / "mixto.pdf"))  # conftest: no service running
    by_page = {e["page"]: e for e in result.page_methods}
    assert by_page[2]["method"] == "empty" and by_page[2]["ocr_note"] == "service_unavailable"
    assert by_page[1]["method"] == "text_layer"
    monkeypatch.setattr(pdf_module, "fam_docs", SpyOcr(ok=False))
    assert {e["ocr_note"] for e in extract(make_mixed_pdf(tmp_path / "otro.pdf")).page_methods if e["method"] == "empty"} == {"failed"}
    monkeypatch.setenv("BORGES_OCR", "0")
    assert {e["ocr_note"] for e in extract(tmp_path / "otro.pdf").page_methods if e["method"] == "empty"} == {"ocr_disabled"}


def test_force_ocr_sends_every_page_and_falls_back_to_the_text_layer(tmp_path, monkeypatch):
    spy = SpyOcr(texts={1: SCANNED_TEXT, 2: SCANNED_TEXT, 4: ""})
    monkeypatch.setattr(pdf_module, "fam_docs", spy)
    result = extract(make_mixed_pdf(tmp_path / "mixto.pdf"), force_ocr=True)
    assert spy.pages_asked == [1, 2, 3, 4] and result.ocr_forced is True
    by_page = {e["page"]: e for e in result.page_methods}
    assert by_page[1]["method"] == "ocr" and by_page[1]["reason"] == "forced" and by_page[2]["method"] == "ocr"
    assert by_page[4]["method"] == "text_layer" and by_page[4]["reason"] == "forced_ocr_gave_nothing_kept_text_layer"
    assert by_page[3]["method"] == "empty"
    assert "OCR" in {u.number: u.text for u in result.units}[1]


def test_non_pdf_inputs_are_unchanged(tmp_path):
    result = extract(make_md(tmp_path / "nota.md"))
    assert result.page_methods == [] and result.ocr_forced is False


# ---------------- API, index metadata and MCP tools ----------------

def agent(client, name, arguments=None):
    return client.post("/api/agent/call", json={"name": name, "arguments": arguments or {}},
                       headers={"Authorization": f"Bearer {client.app.state.services.token}"})


def test_report_reaches_metadata_api_and_mcp_and_force_reindex(client, tmp_path, monkeypatch):
    spy = SpyOcr(texts={n: SCANNED_TEXT for n in (1, 2, 3, 4)})
    monkeypatch.setattr(pdf_module, "fam_docs", spy)
    folder = tmp_path / "docs"
    folder.mkdir()
    make_mixed_pdf(folder / "mixto.pdf")
    created = client.post("/api/collections", json={"path": str(folder), "name": "Mixtos"}).json()
    assert client.services.worker.wait_idle(60)
    assert spy.pages_asked == [2, 3]

    listed = client.get("/api/documents", params={"q": "mixto"}).json()["documents"][0]
    assert listed["page_method_counts"] == {"text_layer": 2, "ocr": 2} or listed["page_method_counts"] == {"text_layer": 2, "ocr": 1, "empty": 1}
    assert [e["page"] for e in listed["meta"]["pdf_page_methods"]] == [1, 2, 3, 4]
    doc = client.get(f"/api/documents/{listed['id']}").json()
    assert [e["method"] for e in doc["page_methods"]][:2] == ["text_layer", "ocr"] and doc["ocr_forced"] is False
    page = client.get(f"/api/documents/{doc['id']}/text", params={"page": 2}).json()
    assert page["extraction"]["method"] == "ocr" and page["extraction"]["reason"] == "no_text_layer"

    tool_doc = agent(client, "library_document", {"document_id": doc["id"]}).json()
    assert [e["method"] for e in tool_doc["page_methods"]][:2] == ["text_layer", "ocr"]
    tool_page = agent(client, "library_read", {"document_id": doc["id"], "page": 1}).json()
    assert tool_page["extraction"]["method"] == "text_layer"
    tool_list = agent(client, "library_documents", {}).json()
    assert tool_list["documents"][0]["page_method_counts"]["text_layer"] == 2

    # unchanged PDF + plain reindex: nothing is re-read
    spy.calls.clear()
    client.post(f"/api/collections/{created['id']}/reindex")
    assert client.services.worker.wait_idle(60) and spy.calls == []

    # forced reindex over the API: every page is re-read even though the file did not change
    forced = client.post(f"/api/collections/{created['id']}/reindex", params={"force_ocr": "true"}).json()
    assert forced["ok"] and forced["progress"]["force_ocr"] is True
    assert client.services.worker.wait_idle(60)
    assert spy.pages_asked == [1, 2, 3, 4]
    doc = client.get(f"/api/documents/{doc['id']}").json()
    assert doc["ocr_forced"] is True and {e["method"] for e in doc["page_methods"]} == {"ocr"}

    # and through the MCP tool
    spy.calls.clear()
    out = agent(client, "library_reindex", {"collection_id": created["id"], "force_ocr": True}).json()
    assert out["force_ocr"] is True and out["queued"] is True
    assert client.services.worker.wait_idle(60) and spy.pages_asked == [1, 2, 3, 4]