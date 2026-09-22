"""Chunking: overlap, boundaries, page numbers and line numbers."""

from borges.chunking import chunk_units
from borges.extract.base import Unit


def sentence(i: int) -> str:
    return f"Frase número {i} sobre la aldea imaginaria de Valdeniebla y sus bibliotecas infinitas."


def test_long_page_splits_with_overlap_and_keeps_page_number():
    text = " ".join(sentence(i) for i in range(60))  # ~5000 chars
    chunks = chunk_units([Unit(kind="page", number=7, text=text)], size=900, overlap=150)
    assert len(chunks) >= 5
    assert all(c.page == 7 for c in chunks)
    assert all(len(c.text) <= 900 + 250 for c in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        assert current.char_start < previous.char_end  # overlap
        assert previous.char_end - current.char_start <= 160
        assert current.text[:20] in text and not current.text.startswith(" ")
    assert chunks[-1].char_end == len(text)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_chunks_never_cross_units():
    units = [Unit(kind="section", number=1, title="Uno", text="A " * 200), Unit(kind="section", number=2, title="Dos", text="B " * 200)]
    chunks = chunk_units(units, size=900, overlap=100)
    assert len(chunks) == 2
    assert chunks[0].section == "Uno" and "B" not in chunks[0].text
    assert chunks[1].section == "Dos" and chunks[1].unit_index == 1 and chunks[1].page is None


def test_line_numbers_follow_offsets():
    text = "\n".join(f"línea {i} " + "x" * 60 for i in range(40))
    chunks = chunk_units([Unit(kind="section", number=1, title="", text=text, line_start=10)], size=500, overlap=50)
    assert chunks[0].line == 10
    assert chunks[1].line > 10
    second = chunks[1]
    assert text[second.char_start:].startswith(second.text)
    assert second.line == 10 + text.count("\n", 0, second.char_start)


def test_short_tail_merges_into_previous():
    text = " ".join(sentence(i) for i in range(12))  # ~1000 chars: one chunk plus a tail shorter than MIN_TAIL
    chunks = chunk_units([Unit(kind="page", number=1, text=text)], size=900, overlap=100)
    assert len(chunks) == 1 and chunks[0].text == text


def test_empty_units_yield_nothing():
    assert chunk_units([Unit(kind="page", number=1, text="   ")]) == []
