"""Extraction per format on generated fixtures."""

from fixtures import BUDGET_EN, FUNES, JARDIN, make_csv, make_docx, make_epub, make_html, make_md, make_pdf, make_scanned_pdf, make_txt

from borges.extract import extract, kind_for


def test_pdf_pages_keep_numbers(tmp_path):
    result = extract(make_pdf(tmp_path / "libro_de_arena.pdf"))
    assert result.kind == "pdf" and result.pages == 3 and not result.needs_ocr
    assert [u.kind for u in result.units] == ["page"] * 3
    assert [u.number for u in result.units] == [1, 2, 3]
    assert "Funes" in result.units[0].text and "bifurcan" in result.units[1].text
    assert result.title == "Libro De Arena"


def test_scanned_pdf_is_flagged(tmp_path):
    result = extract(make_scanned_pdf(tmp_path / "scan.pdf"))
    assert result.needs_ocr is True and result.pages == 2 and result.units == []


def test_docx_headings_become_sections(tmp_path):
    result = extract(make_docx(tmp_path / "memoria.docx"))
    assert result.kind == "docx"
    assert [u.title for u in result.units] == ["Introducción", "Método", "Conclusiones"]
    assert FUNES[:40] in result.units[0].text
    assert "senderos | infinitos" in result.units[1].text  # table rows appended to the section
    assert result.title == "Memoria del máster"


def test_markdown_sections_and_line_numbers(tmp_path):
    result = extract(make_md(tmp_path / "apuntes.md"))
    assert result.title == "Apuntes de lectura"
    titles = [u.title for u in result.units]
    assert titles == ["Apuntes de lectura", "Funes", "El jardín", "Biblioteca"]
    assert "esto no es un título" in result.units[2].text  # fenced code is not a heading
    funes = result.units[1]
    assert funes.line_start == 5 and funes.number == 2


def test_plain_text_single_section(tmp_path):
    result = extract(make_txt(tmp_path / "notas.txt"))
    assert result.kind == "txt" and len(result.units) == 1
    assert result.units[0].text.strip() == BUDGET_EN and result.units[0].line_start == 1


def test_epub_chapters(tmp_path):
    result = extract(make_epub(tmp_path / "ficciones.epub"))
    assert result.kind == "epub" and result.title == "Ficciones de Valdeniebla"
    chapters = [u for u in result.units if u.kind == "chapter"]
    assert [c.title for c in chapters][-3:] == ["Funes el memorioso", "El jardín", "La biblioteca"]
    assert JARDIN[:30] in chapters[-2].text


def test_html_drops_scripts_and_splits_on_headings(tmp_path):
    result = extract(make_html(tmp_path / "ensayo.html"))
    assert result.title == "Página de pruebas"
    assert [u.title for u in result.units] == ["Ensayo sobre la memoria", "Los senderos"]
    assert "var x" not in result.units[0].text and "color:red" not in result.units[0].text


def test_csv_first_rows(tmp_path):
    result = extract(make_csv(tmp_path / "catalogo.csv"))
    assert result.kind == "csv" and "El memorioso | Anónimo de Valdeniebla | 1942" in result.units[0].text


def test_kind_for_and_code_switch(tmp_path):
    assert kind_for(tmp_path / "a.PDF") == "pdf"
    assert kind_for(tmp_path / "a.py") is None
    assert kind_for(tmp_path / "a.py", code=True) == "code"
    assert kind_for(tmp_path / "a.exe", code=True) is None


def test_encoding_fallback(tmp_path):
    path = tmp_path / "latin.txt"
    path.write_bytes("Canción de cuna en Valdeniebla".encode("cp1252"))
    assert "Canción" in extract(path).units[0].text
