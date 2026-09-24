"""The Faustus source: a fake Faustus ASGI app stands in for the real workspace.

Covers: login/token auth (and the loopback-bypass case where no auth is needed at all), listing and
exporting conversations, turn extraction (tool noise skipped, short tool results kept), chunking reuse,
change detection on re-sync, citations, source/date search filters, the `chats_recent` tool, and the
`/api/sources` REST CRUD + sync.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from starlette.testclient import TestClient as ASGITestClient

from borges import faustus_source
from borges.faustus_source import FaustusClient, build_extracted

CITATION_RE = re.compile(r"^\[chat «.+» · \d{4}-\d{2}-\d{2} · turno \d+\]$")


class FakeFaustus:
    """A tiny stand-in for Faustus: /api/auth/login, /api/sessions, /api/session/{id}/export?fmt=json."""

    def __init__(self, require_auth: bool = True, token: str | None = None, username: str = "tester", password: str = "secret123"):
        self.require_auth = require_auth
        self.token = token
        self.username = username
        self.password = password
        self.sessions: dict[str, dict] = {}
        self.exports: dict[str, dict] = {}
        self.login_attempts = 0
        self.app = self._build_app()

    def add_session(self, sid: str, name: str, folder: str = "", created_at: str = "2026-09-01T10:00:00Z",
                     updated_at: str | None = None, model: str = "gpt-test", messages: list[dict] | None = None) -> None:
        updated_at = updated_at or created_at
        messages = messages or []
        self.sessions[sid] = {"id": sid, "name": name, "folder": folder, "created_at": created_at,
                               "updated_at": updated_at, "last_message_at": updated_at, "model": model,
                               "message_count": len(messages)}
        self.exports[sid] = {"name": name, "model": model, "exported": updated_at, "session_id": sid,
                              "project": folder, "workspace": "", "message_count": len(messages), "messages": messages}

    def _authed(self, request: Request) -> bool:
        if not self.require_auth:
            return True
        auth = request.headers.get("authorization", "")
        if self.token and auth == f"Bearer {self.token}":
            return True
        return request.cookies.get("odysseus_session") == "ok"

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/api/auth/login")
        async def login(request: Request, response: Response):
            self.login_attempts += 1
            body = await request.json()
            if body.get("username") == self.username and body.get("password") == self.password:
                response.set_cookie("odysseus_session", "ok")
                return {"ok": True, "username": self.username}
            raise HTTPException(401, "Invalid credentials")

        @app.get("/api/sessions")
        async def sessions(request: Request):
            if not self._authed(request):
                raise HTTPException(401, "Not authenticated")
            return list(self.sessions.values())

        @app.get("/api/session/{sid}/export")
        async def export(sid: str, request: Request, fmt: str = "md"):
            if not self._authed(request):
                raise HTTPException(401, "Not authenticated")
            if sid not in self.exports:
                raise HTTPException(404, "Session not found")
            return self.exports[sid]

        return app


def patch_build_client(monkeypatch, fake: FakeFaustus) -> None:
    """Point `faustus_source.build_client` at the fake app instead of the network."""

    def _build(config: dict) -> FaustusClient:
        base_url = config.get("base_url") or "http://faustus.local"
        return FaustusClient(
            base_url, config.get("username", ""), config.get("password", ""), config.get("token", ""),
            client=ASGITestClient(fake.app, base_url=base_url),
        )

    monkeypatch.setattr(faustus_source, "build_client", _build)


LONG = "Contenido de prueba con relleno suficiente para superar el umbral mínimo de una sección " * 3  # > 200 chars


def sample_messages() -> list[dict]:
    return [
        {"role": "system", "content": "Eres un asistente útil."},  # must never become a turn
        {"role": "user", "content": f"{LONG} ¿Qué decidimos sobre el presupuesto del observatorio Valdeniebla?"},
        {"role": "assistant", "content": f"{LONG} Decidimos aprobar el presupuesto sin enmiendas.",
         "tool_calls": [{"name": "calc", "result": "42", "status": "ok"}]},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "bigdump", "result": "x" * 1000}]},  # long result: dropped, not noise
    ]


# ---------------------------------------------------------------------------
# pure extraction
# ---------------------------------------------------------------------------


def test_build_extracted_skips_noise_and_keeps_short_tool_results():
    session = {"id": "s1", "name": "Presupuesto observatorio", "folder": "astro", "created_at": "2026-09-10T09:00:00Z",
               "last_message_at": "2026-09-10T09:05:00Z", "model": "gpt-test"}
    export = {"name": "Presupuesto observatorio", "session_id": "s1", "messages": sample_messages()}
    extracted, meta = build_extracted(session, export)
    assert extracted.kind == "chat"
    assert extracted.title == "Chat: Presupuesto observatorio (2026-09-10)"
    assert meta == {"conversation_id": "s1", "project": "astro", "created_at": "2026-09-10T09:00:00Z",
                     "updated_at": "2026-09-10T09:05:00Z", "model": "gpt-test", "title": "Presupuesto observatorio",
                     "date": "2026-09-10"}
    full_text = "\n\n".join(u.text for u in extracted.units)
    assert "Eres un asistente útil" not in full_text  # system turn skipped
    assert "[calc: 42]" in full_text  # short tool result kept
    assert "x" * 1000 not in full_text  # long tool result dropped
    assert all(u.title.startswith("turno ") for u in extracted.units)


# ---------------------------------------------------------------------------
# indexing through the shared services + worker
# ---------------------------------------------------------------------------


def test_index_with_token_auth_and_citation_format(services, monkeypatch):
    fake = FakeFaustus(require_auth=True, token="tok_abc123")
    fake.add_session("s1", "Presupuesto observatorio", folder="astro", created_at="2026-09-10T09:00:00Z", messages=sample_messages())
    patch_build_client(monkeypatch, fake)

    collection = services.add_faustus_source("Mi Faustus", {"base_url": "http://faustus.local", "token": "tok_abc123", "poll_minutes": 10})
    assert services.worker.wait_idle(30)
    assert fake.login_attempts == 0  # token auth never needs a login call

    docs = services.documents.counts()
    assert docs["documents"] == 1
    result = services.search.search("presupuesto del observatorio", "hybrid", 5)
    assert result["hits"], "the chat conversation should be searchable"
    hit = result["hits"][0]
    assert CITATION_RE.match(hit["citation"]), hit["citation"]
    assert hit["date"] == "2026-09-10"
    assert hit["project"] == "astro"
    assert hit["source_kind"] == "faustus"

    status = services.collections.get(collection.id).sync_status
    assert status["ok"] is True and status["sessions_total"] == 1 and status["sessions_changed"] == 1


def test_index_with_password_login_and_loopback_bypass(services, monkeypatch):
    # password login
    fake = FakeFaustus(require_auth=True, username="tester", password="secret123")
    fake.add_session("s1", "Chat con login", messages=sample_messages())
    patch_build_client(monkeypatch, fake)
    services.add_faustus_source("Con login", {"base_url": "http://faustus.local", "username": "tester", "password": "secret123"})
    assert services.worker.wait_idle(30)
    assert fake.login_attempts == 1
    assert services.documents.counts()["documents"] == 1

    # loopback bypass: no credentials at all, the fake app never requires auth
    open_fake = FakeFaustus(require_auth=False)
    open_fake.add_session("s2", "Chat sin login", messages=sample_messages())
    monkeypatch.setattr(faustus_source, "build_client", lambda config: FaustusClient(
        config.get("base_url") or "http://faustus.local",
        client=ASGITestClient(open_fake.app, base_url=config.get("base_url") or "http://faustus.local")))
    services.add_faustus_source("Sin login", {"base_url": "http://open.local"})
    assert services.worker.wait_idle(30)
    assert open_fake.login_attempts == 0
    assert services.documents.counts()["documents"] == 2


def test_resync_only_reindexes_changed_conversations(services, monkeypatch):
    fake = FakeFaustus(require_auth=False)
    fake.add_session("s1", "Chat estable", updated_at="2026-09-01T10:00:00Z", messages=sample_messages())
    fake.add_session("s2", "Chat que cambia", updated_at="2026-09-01T10:00:00Z", messages=sample_messages())
    patch_build_client(monkeypatch, fake)
    collection = services.add_faustus_source("Fuente", {"base_url": "http://faustus.local"})
    assert services.worker.wait_idle(30)
    assert services.documents.counts()["documents"] == 2

    # nothing changed: re-sync touches nothing
    services.sync_source(collection.id)
    assert services.worker.wait_idle(30)
    progress = services.worker.progress(collection.id)
    assert progress["files_changed"] == 0 and progress["files_total"] == 2

    # bump one conversation's updated_at and change its content
    fake.add_session("s2", "Chat que cambia", updated_at="2026-09-02T10:00:00Z",
                      messages=[{"role": "user", "content": f"{LONG} Nueva pregunta sobre telescopios reflectores."}])
    services.sync_source(collection.id)
    assert services.worker.wait_idle(30)
    progress = services.worker.progress(collection.id)
    assert progress["files_changed"] == 1
    result = services.search.search("telescopios reflectores", "hybrid", 5)
    assert result["hits"]

    # a conversation removed on the Faustus side is purged from the index
    del fake.sessions["s1"]
    services.sync_source(collection.id)
    assert services.worker.wait_idle(30)
    assert services.worker.progress(collection.id)["files_removed"] == 1
    assert services.documents.counts()["documents"] == 1


def test_sync_error_is_reported(services, monkeypatch):
    fake = FakeFaustus(require_auth=True, username="tester", password="secret123")
    patch_build_client(monkeypatch, fake)
    collection = services.add_faustus_source("Mal configurada", {"base_url": "http://faustus.local", "username": "tester", "password": "wrong"})
    assert services.worker.wait_idle(30)
    progress = services.worker.progress(collection.id)
    assert progress["phase"] == "error"
    status = services.collections.get(collection.id).sync_status
    assert status["ok"] is False and status["last_error"]


def test_search_filters_by_source_and_date_range(services, monkeypatch, library):
    fake = FakeFaustus(require_auth=False)
    fake.add_session("s1", "Chat de observatorio", created_at="2026-09-05T10:00:00Z",
                      messages=[{"role": "user", "content": f"{LONG} hablamos del observatorio Valdeniebla otra vez"}])
    fake.add_session("s2", "Chat viejo", created_at="2026-01-01T10:00:00Z",
                      messages=[{"role": "user", "content": f"{LONG} hablamos del observatorio Valdeniebla hace tiempo"}])
    patch_build_client(monkeypatch, fake)
    services.add_faustus_source("Fuente", {"base_url": "http://faustus.local"})
    services.add_collection("Documentos", str(library), [], None, False, False)
    assert services.worker.wait_idle(60)

    only_chats = services.search.search("observatorio Valdeniebla", "hybrid", 10, source="faustus")
    assert only_chats["hits"] and all(h["source_kind"] == "faustus" for h in only_chats["hits"])

    only_folders = services.search.search("senderos que se bifurcan", "hybrid", 10, source="folder")
    assert only_folders["hits"] and all(h["source_kind"] == "folder" for h in only_folders["hits"])

    recent_only = services.search.search("observatorio Valdeniebla", "hybrid", 10, source="faustus", since="2026-09-01")
    dates = {h["date"] for h in recent_only["hits"]}
    assert dates and all(d >= "2026-09-01" for d in dates)


def test_chats_recent_tool_and_project_filter(services, monkeypatch):
    from borges.agent_tools import ChatsRecentArgs, run_chats_recent

    fake = FakeFaustus(require_auth=False)
    fake.add_session("s1", "Chat astro", folder="astro", created_at="2026-09-05T10:00:00Z", messages=sample_messages())
    fake.add_session("s2", "Chat cocina", folder="cocina", created_at="2026-09-06T10:00:00Z", messages=sample_messages())
    patch_build_client(monkeypatch, fake)
    services.add_faustus_source("Fuente", {"base_url": "http://faustus.local"})
    assert services.worker.wait_idle(30)

    recent = run_chats_recent(services, ChatsRecentArgs(limit=10))
    assert recent["count"] == 2
    titles = {c["title"] for c in recent["chats"]}
    assert titles == {"Chat astro", "Chat cocina"}

    astro_only = run_chats_recent(services, ChatsRecentArgs(limit=10, project="astro"))
    assert astro_only["count"] == 1 and astro_only["chats"][0]["project"] == "astro"


# ---------------------------------------------------------------------------
# REST /api/sources
# ---------------------------------------------------------------------------


def test_sources_api_crud_and_sync(client, monkeypatch):
    fake = FakeFaustus(require_auth=True, token="tok_xyz")
    fake.add_session("s1", "Chat de prueba", messages=sample_messages())
    patch_build_client(monkeypatch, fake)

    assert client.post("/api/sources", json={"base_url": "http://faustus.local"}).status_code == 400  # no credentials

    created = client.post("/api/sources", json={"base_url": "http://faustus.local", "name": "Mi Faustus", "token": "tok_xyz"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "faustus" and body["config"]["token"] == "••••••••"  # secrets are masked
    source_id = body["id"]

    assert client.services.worker.wait_idle(30)
    listed = client.get("/api/sources").json()["sources"]
    assert len(listed) == 1 and listed[0]["id"] == source_id

    status = client.get(f"/api/sources/{source_id}/status").json()
    assert status["sync_status"]["ok"] is True

    patched = client.patch(f"/api/sources/{source_id}", json={"name": "Renombrada", "poll_minutes": 30, "enabled": False})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renombrada" and patched.json()["enabled"] is False

    synced = client.post(f"/api/sources/{source_id}/sync")
    assert synced.status_code == 200 and synced.json()["ok"] is True
    assert client.services.worker.wait_idle(30)

    recent = client.get("/api/sources/faustus/recent-chats").json()["chats"]
    assert len(recent) == 1 and recent[0]["title"] == "Chat de prueba"

    assert client.get("/api/sources/999").status_code == 404
    assert client.delete(f"/api/sources/{source_id}").json() == {"ok": True}
    assert client.get("/api/sources").json()["sources"] == []


def test_search_endpoint_accepts_source_and_date_params(client, monkeypatch):
    fake = FakeFaustus(require_auth=False)
    fake.add_session("s1", "Chat filtrable", created_at="2026-09-10T10:00:00Z",
                      messages=[{"role": "user", "content": f"{LONG} decisión sobre el telescopio reflector"}])
    patch_build_client(monkeypatch, fake)
    client.post("/api/sources", json={"base_url": "http://faustus.local", "username": "a", "password": "b"})
    assert client.services.worker.wait_idle(30)

    ok = client.get("/api/search", params={"q": "telescopio reflector", "source": "faustus"})
    assert ok.status_code == 200 and ok.json()["hits"]
    bad_source = client.get("/api/search", params={"q": "x", "source": "nope"})
    assert bad_source.status_code == 400
    bad_date = client.get("/api/search", params={"q": "x", "since": "not-a-date"})
    assert bad_date.status_code == 400
