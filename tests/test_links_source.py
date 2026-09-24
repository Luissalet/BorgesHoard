"""The Links source: a fake Links Hoard answers list_links / read_link (as the hub proxy would).

Covers: paging through list_links with next_cursor, paged read_link, the header unit (note, excerpt, tags),
a link whose page failed to fetch (indexed from its title and note), citations `[enlace «Title» · site]`,
source='links' search filter, change detection by fingerprint, deletions, the direct (no-hub) client,
and the `/api/sources` REST endpoints for links.
"""

from __future__ import annotations

import re

import httpx

from borges import links_source
from borges.links_source import LinksClient, LinksError, build_extracted, fingerprint, links_unique_key

CITATION_RE = re.compile(r"^\[enlace «.+» · .+\]$")
LONG = "Texto extraído de la página con relleno suficiente para superar el umbral mínimo de una sección " * 3


class FakeLinks:
    """Stands in for Links Hoard's two tools; counts calls so tests can assert what was (not) re-read."""

    def __init__(self):
        self.links: dict[str, dict] = {}
        self.texts: dict[str, str] = {}
        self.reads = 0
        self.lists = 0
        self.down = False

    def add(self, lid: str, title: str, text: str = "", site: str = "ejemplo.org", url: str | None = None, notes: str = "",
            tags: list[str] | None = None, saved_at: str = "2026-09-12T10:00:00Z", fetch_status: str = "ok", excerpt: str = "",
            archived: bool = False) -> None:
        self.links[lid] = {"id": lid, "url": url or f"https://{site}/{lid}", "title": title, "site": site, "excerpt": excerpt, "byline": "",
                           "kind": "article", "saved_at": saved_at, "read_at": None, "archived": archived, "favorite": False,
                           "tags": tags or [], "notes": notes, "fetch_status": fetch_status, "word_count": len(text.split())}
        self.texts[lid] = text

    def call(self, tool: str, args: dict) -> dict:
        if self.down:
            raise LinksError("links/list_links through the hub: hub not reachable")
        if tool == "list_links":
            self.lists += 1
            state = args.get("state", "unread")
            items = [l for l in self.links.values() if state == "all" or (state == "archived") == bool(l["archived"])]
            if args.get("tag"):
                items = [l for l in items if args["tag"] in l["tags"]]
            limit, cursor = int(args.get("limit", 30)), int(args.get("cursor", 0))
            page = items[cursor:cursor + limit]
            nxt = cursor + limit if cursor + limit < len(items) else None
            return {"total": len(items), "items": page, "next_cursor": nxt}
        if tool == "read_link":
            self.reads += 1
            link = self.links[args["id"]]
            text = self.texts[args["id"]]
            off, mx = int(args.get("offset", 0)), int(args.get("max_chars", 4000))
            slice_ = text[off:off + mx]
            return {**{k: link[k] for k in ("id", "url", "title", "byline", "site", "kind", "fetch_status")}, "text": slice_,
                    "offset": off, "total_chars": len(text), "has_more": off + len(slice_) < len(text), "highlights": []}
        raise AssertionError(tool)


def patch_build_client(monkeypatch, fake: FakeLinks) -> None:
    monkeypatch.setattr(links_source, "build_client", lambda config: LinksClient(caller=fake.call))


# ---------------------------------------------------------------------------
# pure pieces
# ---------------------------------------------------------------------------


def test_build_extracted_header_and_text():
    link = {"id": "l1", "url": "https://ejemplo.org/a", "title": "Un artículo", "site": "ejemplo.org", "byline": "Ana", "saved_at": "2026-09-12T10:00:00Z",
            "tags": ["astro", "leer"], "notes": "Mirar la tabla 3", "excerpt": "Resumen corto.", "fetch_status": "ok", "word_count": 3}
    extracted, meta = build_extracted(link, LONG)
    assert extracted.kind == "link" and extracted.title == "Enlace: Un artículo"
    assert [u.title for u in extracted.units] == ["ficha", "texto"]
    header = extracted.units[0].text
    assert "ejemplo.org · Ana" in header and "Etiquetas: astro, leer" in header and "Nota: Mirar la tabla 3" in header and "Resumen corto." in header
    assert meta["date"] == "2026-09-12" and meta["site"] == "ejemplo.org" and meta["url"] == "https://ejemplo.org/a" and meta["tags"] == ["astro", "leer"]
    # a failed fetch still yields a document from what the user saved
    failed, _ = build_extracted({"id": "l2", "title": "Sin texto", "url": "https://x.y/z", "fetch_status": "failed"}, "")
    assert [u.title for u in failed.units] == ["ficha"] and "Sin texto" in failed.units[0].text
    assert links_unique_key("") == "links://hub" and links_unique_key("http://127.0.0.1:8790/") == "links://http://127.0.0.1:8790"


def test_fingerprint_changes_with_what_matters():
    a = {"id": "l1", "title": "T", "word_count": 10, "fetch_status": "ok", "notes": "", "tags": ["x"], "url": "u"}
    assert fingerprint(a) == fingerprint({**a, "read_at": "now", "favorite": True})  # reading it is not a change
    assert fingerprint(a) != fingerprint({**a, "notes": "nueva nota"})
    assert fingerprint(a) != fingerprint({**a, "word_count": 11})
    assert fingerprint(a) == fingerprint({**a, "tags": ["x"]})


def test_client_pages_through_lists_and_reads(monkeypatch):
    fake = FakeLinks()
    for i in range(7):
        fake.add(f"l{i}", f"Enlace {i}", text="palabra " * 50)
    monkeypatch.setattr(links_source, "PAGE", 3)
    client = LinksClient(caller=fake.call)
    assert len(client.list_links("all")) == 7 and fake.lists == 3
    monkeypatch.setattr(links_source, "READ_CHARS", 100)
    header, text = client.read_link("l1")
    assert text == "palabra " * 50 and header["title"] == "Enlace 1" and fake.reads == 4
    assert client.via == "test"


def test_direct_client_without_hub():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        body = request.read()
        seen["body"] = body
        return httpx.Response(200, json={"ok": True, "result": {"total": 0, "items": [], "next_cursor": None}})

    client = LinksClient("http://links.local/", token="tok", client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://links.local"))
    assert client.via == "direct"
    assert client.list_links("unread") == []
    assert seen["auth"] == "Bearer tok" and seen["path"] == "/api/agent/call" and b'"name": "list_links"' in seen["body"] or b'"name":"list_links"' in seen["body"]

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad token"})

    bad = LinksClient("http://links.local", client=httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://links.local"))
    try:
        bad.list_links()
    except LinksError as error:
        assert "401" in str(error)
    else:
        raise AssertionError("expected LinksError")


# ---------------------------------------------------------------------------
# through services + worker
# ---------------------------------------------------------------------------


def test_index_links_and_cite_them(services, monkeypatch):
    fake = FakeLinks()
    fake.add("l1", "Telescopios reflectores caseros", text=f"{LONG} Cómo construir un telescopio reflector con espejo parabólico.", tags=["astro"],
             notes="Para el taller del sábado")
    fake.add("l2", "Página que no cargó", text="", fetch_status="failed", notes="Receta de tarta de manzana de la abuela", site="cocina.example")
    patch_build_client(monkeypatch, fake)
    collection = services.add_links_source("Mis enlaces", {"state": "all", "poll_minutes": 30})
    assert collection.kind == "links" and collection.path == "links://hub"
    assert services.worker.wait_idle(30)
    assert services.documents.counts()["documents"] == 2
    assert fake.reads == 1  # the failed one was never read

    result = services.search.search("telescopio reflector espejo", "hybrid", 5, source="links")
    assert result["hits"], "the link text should be searchable"
    hit = result["hits"][0]
    assert CITATION_RE.match(hit["citation"]), hit["citation"]
    assert hit["citation"] == "[enlace «Telescopios reflectores caseros» · ejemplo.org]"
    assert hit["source_kind"] == "links" and hit["kind"] == "link" and hit["url"] == "https://ejemplo.org/l1" and hit["date"] == "2026-09-12"

    # the note of an unfetched page is still findable
    note_hit = services.search.search("tarta de manzana", "bm25", 5, source="links")["hits"]
    assert note_hit and note_hit[0]["citation"] == "[enlace «Página que no cargó» · cocina.example]"
    # source filter excludes links
    assert services.search.search("tarta de manzana", "bm25", 5, source="folder")["hits"] == []

    status = services.collections.get(collection.id).sync_status
    assert status["ok"] is True and status["links_total"] == 2 and status["links_changed"] == 2 and status["via"] == "test"


def test_resync_reads_only_changed_links_and_drops_deleted(services, monkeypatch):
    fake = FakeLinks()
    fake.add("l1", "Estable", text=f"{LONG} uno")
    fake.add("l2", "Cambia", text=f"{LONG} dos")
    patch_build_client(monkeypatch, fake)
    collection = services.add_links_source("Enlaces", {})
    assert services.worker.wait_idle(30)
    assert fake.reads == 2

    services.sync_source(collection.id)
    assert services.worker.wait_idle(30)
    assert services.worker.progress(collection.id)["files_changed"] == 0 and fake.reads == 2

    fake.links["l2"]["notes"] = "ahora con nota sobre cometas"
    services.sync_source(collection.id)
    assert services.worker.wait_idle(30)
    assert services.worker.progress(collection.id)["files_changed"] == 1 and fake.reads == 3
    assert services.search.search("nota sobre cometas", "bm25", 5)["hits"]

    del fake.links["l1"]
    services.sync_source(collection.id)
    assert services.worker.wait_idle(30)
    assert services.worker.progress(collection.id)["files_removed"] == 1
    assert services.documents.counts()["documents"] == 1


def test_state_and_tag_filters_and_errors(services, monkeypatch):
    fake = FakeLinks()
    fake.add("l1", "Leído y archivado", text=f"{LONG} a", archived=True, tags=["astro"])
    fake.add("l2", "Pendiente", text=f"{LONG} b", tags=["astro"])
    fake.add("l3", "Otro tema", text=f"{LONG} c", tags=["cocina"])
    patch_build_client(monkeypatch, fake)
    collection = services.add_links_source("Solo astro", {"state": "unread", "tag": "astro"})
    assert services.worker.wait_idle(30)
    assert services.documents.counts()["documents"] == 1
    assert services.search.search("Pendiente", "bm25", 5)["hits"]

    fake.down = True
    services.sync_source(collection.id)
    assert services.worker.wait_idle(30)
    progress = services.worker.progress(collection.id)
    assert progress["phase"] == "error" and "hub not reachable" in progress["message"]
    assert services.collections.get(collection.id).sync_status["ok"] is False


def test_scheduler_polls_links_sources(services, monkeypatch):
    fake = FakeLinks()
    fake.add("l1", "Uno", text=f"{LONG} a")
    patch_build_client(monkeypatch, fake)
    collection = services.add_links_source("Enlaces", {"poll_minutes": 1})
    assert services.worker.wait_idle(30)
    services.collections.set_sync_status(collection.id, {"last_run": 0})
    services.faustus_scheduler._tick()
    assert services.worker.wait_idle(30)
    assert fake.lists == 2


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


def test_sources_api_for_links(client, monkeypatch):
    fake = FakeLinks()
    fake.add("l1", "Vía API", text=f"{LONG} api")
    patch_build_client(monkeypatch, fake)
    r = client.post("/api/sources/links", json={"name": "Enlaces", "state": "all", "poll_minutes": 15})
    assert r.status_code == 201, r.text
    source = r.json()
    assert source["kind"] == "links" and source["config"]["state"] == "all" and source["config"]["poll_minutes"] == 15
    sid = source["id"]
    assert [s["id"] for s in client.get("/api/sources", params={"kind": "links"}).json()["sources"]] == [sid]
    assert client.get("/api/sources").json()["sources"][0]["kind"] == "links"
    r = client.patch(f"/api/sources/{sid}", json={"state": "unread", "tag": "astro", "name": "Astro"})
    assert r.status_code == 200 and r.json()["config"]["state"] == "unread" and r.json()["config"]["tag"] == "astro" and r.json()["name"] == "Astro"
    assert client.post(f"/api/sources/{sid}/sync").status_code == 200
    assert "sync_status" in client.get(f"/api/sources/{sid}/status").json()
    assert client.delete(f"/api/sources/{sid}").json() == {"ok": True}
    assert client.get(f"/api/sources/{sid}").status_code == 404
    assert client.post("/api/sources/links", json={"state": "nope"}).status_code in (400, 422)
