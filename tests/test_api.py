"""HTTP API through TestClient: collections, documents, text, search, agent auth."""

import time


def wait_idle(client, timeout=60):
    assert client.services.worker.wait_idle(timeout)


def add_library(client, library, **extra):
    response = client.post("/api/collections", json={"path": str(library), "name": "Pruebas", **extra})
    assert response.status_code == 201, response.text
    wait_idle(client)
    return response.json()


def test_health_and_local_only(client):
    assert client.get("/api/health").json() == {"service": "borges-hoard", "version": "0.1.0", "dataDirConfigured": True}
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/api/status", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/nope").status_code == 404


def test_collections_crud_and_validation(client, library):
    assert client.post("/api/collections", json={"path": str(library / "missing")}).status_code == 400
    assert client.post("/api/collections", json={}).status_code == 400
    created = add_library(client, library, include=["**/*.md", "**/*.pdf"], watch=False)
    assert created["name"] == "Pruebas" and created["include"] == ["**/*.md", "**/*.pdf"] and created["exclude"]
    again = client.post("/api/collections", json={"path": str(library)})
    assert again.status_code == 201 and again.json()["id"] == created["id"]  # idempotent by path
    listed = client.get("/api/collections").json()["collections"]
    assert len(listed) == 1 and listed[0]["progress"]["phase"] == "done" and listed[0]["progress"]["files_total"] == 3
    patched = client.patch(f"/api/collections/{created['id']}", json={"name": "Lecturas", "enabled": False}).json()
    assert patched["name"] == "Lecturas" and patched["enabled"] is False
    assert client.patch("/api/collections/999", json={"name": "x"}).status_code == 404
    reindex = client.post(f"/api/collections/{created['id']}/reindex").json()
    assert reindex["ok"] is True
    wait_idle(client)
    assert client.get(f"/api/collections/{created['id']}/progress").json()["progress"]["phase"] == "done"
    assert client.delete(f"/api/collections/{created['id']}").json() == {"ok": True}
    assert client.delete(f"/api/collections/{created['id']}").status_code == 404
    assert client.get("/api/status").json()["counts"]["documents"] == 0


def test_documents_listing_outline_and_text(client, library):
    created = add_library(client, library)
    page = client.get("/api/documents", params={"collection": created["id"], "limit": 3}).json()
    assert len(page["documents"]) == 3 and page["next_cursor"]
    rest = client.get("/api/documents", params={"limit": 100, "cursor": page["next_cursor"]}).json()
    assert len(rest["documents"]) == 5 and rest["next_cursor"] is None
    by_name = client.get("/api/documents", params={"q": "cuentos"}).json()["documents"]
    assert len(by_name) == 1 and by_name[0]["kind"] == "pdf"
    doc = client.get(f"/api/documents/{by_name[0]['id']}").json()
    assert doc["collection"] == "Pruebas" and doc["pages"] == 3 and [u["number"] for u in doc["outline"]] == [1, 2, 3]
    assert doc["path"].endswith("cuentos_valdeniebla.pdf")
    text = client.get(f"/api/documents/{doc['id']}/text", params={"page": 2}).json()
    assert "bifurcan" in text["text"] and text["prev"]["number"] == 1 and text["next"]["number"] == 3 and text["total"] == 3
    assert client.get(f"/api/documents/{doc['id']}/text", params={"page": 9}).status_code == 404
    assert client.get("/api/documents/999").status_code == 404
    md = client.get("/api/documents", params={"q": "apuntes"}).json()["documents"][0]
    section = client.get(f"/api/documents/{md['id']}/text", params={"section": 2}).json()
    assert section["title"] == "Funes" and section["line_start"] == 5
    scanned = client.get("/api/documents", params={"q": "escaneado"}).json()["documents"][0]
    assert scanned["needs_ocr"] is True


def test_search_endpoints(client, library):
    add_library(client, library)
    assert client.get("/api/search").status_code == 400
    assert client.get("/api/search", params={"q": "x", "mode": "magic"}).status_code == 400
    assert client.get("/api/search", params={"q": "x", "collection": 42}).status_code == 404
    result = client.get("/api/search", params={"q": "senderos que se bifurcan", "limit": 5}).json()
    assert result["mode"] == "hybrid" and result["hits"]
    hit = result["hits"][0]
    assert {"chunk_id", "document_id", "citation", "snippet", "page", "section", "score", "path"} <= set(hit)
    assert "<mark>" in hit["snippet"]
    similar = client.get(f"/api/similar/{hit['chunk_id']}").json()["hits"]
    assert similar and all(h["chunk_id"] != hit["chunk_id"] for h in similar)
    assert client.get("/api/similar/999999").status_code == 404
    chunk = client.get(f"/api/chunks/{hit['chunk_id']}").json()
    assert chunk["id"] == hit["chunk_id"] and "bifurcan" in chunk["text"]


def test_agent_tools_and_auth(client, library):
    add_library(client, library)
    catalog = client.get("/api/agent/tools").json()
    names = [t["name"] for t in catalog["tools"]]
    assert names == ["library_search", "library_read", "library_document", "library_documents", "library_collections", "library_status",
                     "library_similar", "library_reindex", "library_add_collection", "chats_recent"]
    assert "cite" in catalog["instructions"].lower()
    for tool in catalog["tools"]:
        assert "Sinónimos:" in tool["description"] and tool["inputSchema"]["type"] == "object"
    write = next(t for t in catalog["tools"] if t["name"] == "library_add_collection")
    assert write["annotations"]["readOnlyHint"] is False
    token = client.services.token
    assert client.post("/api/agent/call", json={"name": "library_status"}).status_code == 401
    assert client.post("/api/agent/call", json={"name": "library_status"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/agent/call", json={"name": "unknown"}, headers=auth).status_code == 404
    assert client.post("/api/agent/call", json={"name": "library_search", "arguments": {}}, headers=auth).status_code == 400
    assert client.post("/api/agent/call", json={"name": "library_read", "arguments": {"document_id": 999}}, headers=auth).status_code == 404

    search = client.post("/api/agent/call", json={"name": "library_search", "arguments": {"q": "vertedero de basuras", "limit": 3}}, headers=auth).json()
    assert search["count"] == 3 and search["hits"][0]["citation"]
    hit = search["hits"][0]
    read = client.post("/api/agent/call", json={"name": "library_read", "arguments": {"document_id": hit["document_id"], "chunk_id": hit["chunk_id"], "max_chars": 300}}, headers=auth).json()
    assert "vertedero" in read["text"] and read["citation"] == hit["citation"]
    assert read["truncated"] is (len(read["text"]) > 300)
    status = client.post("/api/agent/call", json={"name": "library_status"}, headers=auth).json()
    assert status["counts"]["documents"] == 8 and status["indexing"] is False and status["model"]["state"] == "ready"
    cols = client.post("/api/agent/call", json={"name": "library_collections"}, headers=auth).json()["collections"]
    assert cols[0]["documents"] == 8
    docs = client.post("/api/agent/call", json={"name": "library_documents", "arguments": {"q": "ficciones"}}, headers=auth).json()
    assert docs["documents"][0]["kind"] == "epub"
    outline = client.post("/api/agent/call", json={"name": "library_document", "arguments": {"document_id": docs["documents"][0]["id"]}}, headers=auth).json()
    assert [u["title"] for u in outline["outline"]][-1] == "La biblioteca"
    similar = client.post("/api/agent/call", json={"name": "library_similar", "arguments": {"chunk_id": hit["chunk_id"]}}, headers=auth).json()
    assert similar["count"] > 0
    empty = client.post("/api/agent/call", json={"name": "library_search", "arguments": {"q": "zzzzqqqq", "mode": "bm25"}}, headers=auth).json()
    assert empty["count"] == 0 and empty["note"]
    missing = client.post("/api/agent/call", json={"name": "library_add_collection", "arguments": {"path": str(library / "nope")}}, headers=auth)
    assert missing.status_code == 400
    reindex = client.post("/api/agent/call", json={"name": "library_reindex", "arguments": {"collection_id": cols[0]["id"]}}, headers=auth).json()
    assert reindex["ok"] is True
    wait_idle(client)


def test_status_shape(client):
    status = client.get("/api/status").json()
    assert status["service"] == "borges-hoard" and status["model"]["backend"] == "fake"
    assert status["worker"]["running"] is True and status["counts"] == {"documents": 0, "chunks": 0, "errors": 0, "needs_ocr": 0}
    assert time.time() - status["started_at"] < 60
