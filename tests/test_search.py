"""FTS search, snippets, dense search with the fake embedder, and hybrid fusion ordering."""

import numpy as np
import pytest

from borges.search import Search, fts_query, highlight, query_terms


def test_fts_query_drops_stopwords_and_stems():
    and_q, or_q = fts_query("¿dónde leí sobre las hojas de los árboles?")
    assert and_q == '"leí"* AND "hoja"* AND "árbol"*'
    assert or_q.count(" OR ") == 2
    assert fts_query("de la") == ('"de" AND "la"', '"de" OR "la"')  # all stopwords: keep them
    assert fts_query("   ") == ("", "")


def test_short_terms_match_whole_words_only():
    assert fts_query("co")[0] == '"co"'  # no prefix expansion under 3 characters
    assert fts_query("con")[0] == '"con"*'
    assert fts_query("commits co")[0] == '"commit"* AND "co"'


def test_short_terms_do_not_prefix_match_in_the_index(indexed):
    services, _ = indexed
    assert not services.search.bm25("co", 10, None)  # "combinaciones", "concebía", "committee" exist but "co" does not
    assert services.search.bm25("com", 10, None)  # 3+ letters expand as a prefix


def test_query_under_three_chars_is_refused(indexed, client, library):
    services, _ = indexed
    with pytest.raises(ValueError):
        services.search.search("co", "bm25", 5)
    with pytest.raises(ValueError):
        services.search.search("  a ", "hybrid", 5)
    response = client.get("/api/search", params={"q": "co"})
    assert response.status_code == 400 and "3 characters" in response.json()["error"]
    assert client.get("/api/search", params={"q": " x "}).status_code == 400


def test_search_reports_took_ms_and_collapses_sections(indexed):
    services, _ = indexed
    result = services.search.search("Valdeniebla", "bm25", 20)
    assert isinstance(result["took_ms"], float) and result["took_ms"] >= 0
    keys = [(h["document_id"], h["unit_id"]) for h in result["hits"]]
    assert len(keys) == len(set(keys))  # one hit per page/section
    assert all("also" in h and "more" in h for h in result["hits"])


def test_highlight_is_accent_insensitive_and_marks_whole_words():
    text = "Recordaba cada hoja de cada árbol de cada monte y los árboles del sur."
    out = highlight(text, query_terms("hojas arboles"))
    assert "<mark>hoja</mark>" in out and "<mark>árbol</mark>" in out and "<mark>árboles</mark>" in out
    assert "&lt;" in highlight("a <b> tag", ["tag"])


def test_bm25_finds_words_with_snippet_and_citation(indexed):
    services, _ = indexed
    result = services.search.search("vertedero de basuras", "bm25", 10)
    assert result["mode"] == "bm25" and result["hits"]
    hit = result["hits"][0]
    assert "<mark>vertedero</mark>" in hit["snippet"]
    citations = {h["citation"] for h in result["hits"]}
    assert "«Cuentos Valdeniebla», p. 1" in citations
    assert "apuntes.md § Funes" in citations
    assert any(c.startswith("«Ficciones de Valdeniebla», cap.") for c in citations)


def test_search_scoped_to_collection(indexed, tmp_path):
    services, collection = indexed
    other_dir = tmp_path / "otra"
    other_dir.mkdir()
    (other_dir / "nota.txt").write_text("Los telescopios del observatorio apuntan al vertedero de estrellas.", encoding="utf-8")
    other = services.add_collection("Otra", str(other_dir), [], None, False, False)
    assert services.worker.wait_idle(30)
    hits = services.search.search("vertedero", "hybrid", 20, collection_id=other.id)["hits"]
    assert hits and all(h["collection_id"] == other.id for h in hits)
    hits = services.search.search("vertedero", "hybrid", 20, collection_id=collection.id)["hits"]
    assert hits and all(h["collection_id"] == collection.id for h in hits)


def test_dense_mode_uses_embeddings(indexed):
    services, _ = indexed
    result = services.search.search("quarterly maintenance budget", "dense", 3)
    assert result["mode"] == "dense" and result["hits"][0]["rel_path"] == "sub/budget.txt"


def test_rrf_orders_by_agreement():
    fused = Search.rrf([[(1, 9.0), (2, 8.0), (3, 7.0)], [(3, 0.9), (1, 0.8), (4, 0.7)]])
    ids = [cid for cid, _ in fused]
    assert ids[0] == 1 and ids[1] == 3  # both lists rank 1 and 3; 1 is higher on average
    assert set(ids) == {1, 2, 3, 4}
    assert fused[0][1] == 1 / 61 + 1 / 62


def test_hybrid_prefers_hits_found_by_both_legs(indexed):
    services, _ = indexed
    hybrid = services.search.search("Valdeniebla observatory telescope", "hybrid", 5)
    assert hybrid["hits"][0]["rel_path"] == "sub/budget.txt"
    bm25_ids = [cid for cid, _ in services.search.bm25("Valdeniebla observatory telescope", 40, None)]
    dense_ids = [cid for cid, _ in services.search.dense("Valdeniebla observatory telescope", 40, None)]
    assert hybrid["hits"][0]["chunk_id"] in bm25_ids and hybrid["hits"][0]["chunk_id"] in dense_ids


def test_hybrid_falls_back_to_bm25_without_model(tmp_path, library):
    from borges.services import Services
    from conftest import make_config

    services = Services(make_config(tmp_path, embed_backend="none"))
    services.worker.start()
    try:
        services.add_collection("Sin modelo", str(library), [], None, False, False)
        assert services.worker.wait_idle(60)
        result = services.search.search("bifurcan", "dense", 5)
        assert result["mode"] == "bm25" and result["dense_available"] is False and result["hits"]
    finally:
        services.stop()


def test_similar_excludes_itself(indexed):
    services, _ = indexed
    hit = services.search.search("vertedero de basuras", "bm25", 1)["hits"][0]
    similar = services.search.similar(hit["chunk_id"], 5)
    assert similar and all(h["chunk_id"] != hit["chunk_id"] for h in similar)
    assert similar[0]["rel_path"] != hit["rel_path"]  # the same passage in another document


def test_reranker_hook_is_applied(indexed):
    services, _ = indexed
    services.search.reranker = lambda q, hits: list(reversed(hits))
    ordered = services.search.search("biblioteca", "bm25", 5)["hits"]
    services.search.reranker = None
    plain = services.search.search("biblioteca", "bm25", 5)["hits"]
    assert [h["chunk_id"] for h in ordered] == [h["chunk_id"] for h in reversed(plain)]


def test_embeddings_roundtrip_as_float32(indexed):
    services, _ = indexed
    ids, matrix = services.documents.load_embeddings("fake-hash")
    assert matrix.dtype == np.float32 and matrix.shape == (len(ids), 32)
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)
