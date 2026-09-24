"""The Faustus source: index the user's own conversations from their Faustus workspace.

A "collection" of kind `faustus` holds, in its `config` JSON, everything needed to reach one Faustus
instance: `base_url`, and either `token` (an API token minted in Faustus with the `sessions` scope —
preferred, see README) or `username`/`password` for a cookie login. `include_projects` (a list of
Faustus folder names) restricts which conversations are indexed; `poll_minutes` controls how often the
background scheduler re-syncs. Faustus commonly bypasses auth entirely for loopback callers
(`LOCALHOST_BYPASS=true`); this client handles both that case and real login transparently — it never
assumes one or the other and simply retries once with credentials when a call comes back 401.

One conversation becomes one document (`kind="chat"`, `rel_path` = the Faustus session id), with one
section-unit per kept turn ("turno N"). The existing chunking/embedding/FTS pipeline (`chunking.py`,
`indexer.embed_pending`) is reused unchanged — a chat document is, from there on, just another document.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from .chunking import INDEX_VERSION, chunk_units, merge_small_units
from .extract.base import Extracted, Unit, finish
from .indexer import Progress
from .store import Collection, CollectionStore, DocumentStore

log = logging.getLogger("borges.faustus")

ROLE_LABEL = {"user": "Usuario", "assistant": "Asistente"}
MAX_TOOL_RESULT_CHARS = 300  # a longer tool result is noise for citation purposes; only its name is kept
DEFAULT_POLL_MINUTES = 10


class FaustusError(RuntimeError):
    """Anything that stops a sync: unreachable host, bad credentials, unexpected response shape."""


def faustus_unique_key(base_url: str) -> str:
    """The `collections.path` value used to dedupe Faustus sources by URL (mirrors folder-by-path)."""
    return "faustus://" + (base_url or "").strip().rstrip("/").lower()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class FaustusClient:
    """Talks to one Faustus instance: login (or loopback bypass), list sessions, export one as JSON."""

    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = "", timeout: float = 20.0,
                 client: httpx.Client | None = None):
        self.base_url = (base_url or "").strip().rstrip("/")
        if not self.base_url:
            raise FaustusError("This source has no base_url configured.")
        self.username = username or ""
        self.password = password or ""
        self.token = token or ""
        # `client` is only ever set by tests: a starlette.testclient.TestClient (an httpx.Client subclass)
        # wrapping a fake Faustus ASGI app, in place of a real network connection.
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FaustusClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _login(self) -> None:
        if not (self.username and self.password):
            raise FaustusError("Not authenticated and no username/password/token configured.")
        try:
            response = self._client.post(
                "/api/auth/login", json={"username": self.username, "password": self.password, "remember": True}
            )
        except httpx.HTTPError as error:
            raise FaustusError(f"Could not reach Faustus at {self.base_url}: {error}") from error
        if response.status_code != 200:
            raise FaustusError(f"Faustus login failed (HTTP {response.status_code}).")
        data = response.json()
        if data.get("requires_totp"):
            raise FaustusError("This Faustus account has 2FA enabled; use an API token instead of a password.")

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        try:
            response = self._client.get(path, params=params, headers=self._headers())
        except httpx.HTTPError as error:
            raise FaustusError(f"Could not reach Faustus at {self.base_url}: {error}") from error
        if response.status_code == 401:
            self._login()
            try:
                response = self._client.get(path, params=params, headers=self._headers())
            except httpx.HTTPError as error:
                raise FaustusError(f"Could not reach Faustus at {self.base_url}: {error}") from error
        if response.status_code != 200:
            raise FaustusError(f"Faustus {path} returned HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def list_sessions(self) -> list[dict]:
        data = self._get("/api/sessions")
        if not isinstance(data, list):
            raise FaustusError("Unexpected response from Faustus GET /api/sessions (expected a list).")
        return data

    def export_session_json(self, session_id: str) -> dict:
        data = self._get(f"/api/session/{session_id}/export", params={"fmt": "json"})
        if not isinstance(data, dict):
            raise FaustusError(f"Unexpected export response for session {session_id}.")
        return data


def build_client(config: dict) -> FaustusClient:
    return FaustusClient(config.get("base_url", ""), config.get("username", ""), config.get("password", ""), config.get("token", ""))


# ---------------------------------------------------------------------------
# conversation -> document
# ---------------------------------------------------------------------------


def _parse_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _date_str(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10]


def _turn_text(message: dict) -> str:
    """One turn's text: role label + content, plus a short note per tool call (never the raw tool noise)."""
    role = (message.get("role") or "").strip().lower()
    label = ROLE_LABEL.get(role, role.title() or "Mensaje")
    text = (message.get("content") or "").strip()
    parts = [f"{label}: {text}" if text else f"{label}:"]
    for call in message.get("tool_calls") or []:
        name = str(call.get("name") or "tool").strip()
        result = str(call.get("result") or "").strip()
        if result and len(result) <= MAX_TOOL_RESULT_CHARS:
            parts.append(f"[{name}: {result}]")
        elif call.get("status"):
            parts.append(f"[{name}: {call.get('status')}]")
    return "\n".join(parts)


def build_extracted(session: dict, export: dict) -> tuple[Extracted, dict]:
    """One document per conversation, one unit ("turno N") per kept user/assistant turn.

    Tool-call approval cards and internal bookkeeping turns are already stripped by Faustus's own JSON
    export (`build_transcript`, which skips `metadata.hidden` and system turns); this only keeps
    user/assistant roles and trims long tool results to a short note.
    """
    title = (session.get("name") or export.get("name") or "").strip() or "(sin título)"
    created_at = session.get("created_at") or export.get("exported") or ""
    updated_at = session.get("last_message_at") or session.get("updated_at") or created_at
    date = _date_str(created_at) or _date_str(updated_at)

    raw_units: list[Unit] = []
    for message in export.get("messages") or []:
        role = (message.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        text = _turn_text(message)
        raw_units.append(Unit(kind="section", number=len(raw_units) + 1, title="", text=text))

    units = finish(raw_units)  # drops empty turns, normalizes whitespace
    for index, unit in enumerate(units, start=1):
        unit.number = index
        unit.title = f"turno {index}"

    extracted = Extracted(
        title=f"Chat: {title} ({date})" if date else f"Chat: {title}",
        kind="chat", units=units, needs_ocr=False, pages=0,
    )
    meta = {
        "conversation_id": session.get("id") or export.get("session_id") or "",
        "project": session.get("folder") or export.get("project") or "",
        "created_at": created_at, "updated_at": updated_at,
        "model": session.get("model") or export.get("model") or "",
        "title": title, "date": date,
    }
    return extracted, meta


# ---------------------------------------------------------------------------
# indexer (same shape as Indexer.index_collection, so IndexWorker treats it identically)
# ---------------------------------------------------------------------------


class FaustusIndexer:
    def __init__(self, collections: CollectionStore, documents: DocumentStore, embed_indexer, on_change=None):
        self.collections = collections
        self.documents = documents
        self.embed_indexer = embed_indexer  # anything with .embed_pending(progress, collection_id, cancel) — reused as-is
        self.on_change = on_change or (lambda: None)

    def index_collection(self, collection: Collection, progress: Progress, cancel: threading.Event | None = None) -> Progress:
        cancel = cancel or threading.Event()
        progress.phase = "scanning"
        progress.started_at = time.time()
        client: FaustusClient | None = None
        try:
            client = build_client(collection.config)
            sessions = client.list_sessions()
            include = set(collection.config.get("include_projects") or []) or None
            if include:
                sessions = [s for s in sessions if (s.get("folder") or "") in include]
            progress.files_total = len(sessions)

            known = self.documents.fingerprints(collection.id)
            present = {s.get("id") for s in sessions if s.get("id")}
            for rel, (doc_id, *_rest) in known.items():
                if rel not in present:
                    self.documents.remove(doc_id)
                    progress.files_removed += 1

            progress.phase = "extracting"
            for session in sessions:
                if cancel.is_set():
                    progress.phase = "cancelled"
                    return progress
                sid = session.get("id")
                if not sid:
                    progress.files_done += 1
                    continue
                progress.current_file = session.get("name") or sid
                try:
                    if self._sync_one(collection, client, session, known.get(sid)):
                        progress.files_changed += 1
                        self.on_change()
                except Exception as error:  # one bad conversation must not stop the sync
                    log.warning("faustus session %s: %s", sid, error)
                    progress.errors.append({"path": sid, "error": f"{type(error).__name__}: {error}"[:500]})
                progress.files_done += 1
            progress.current_file = ""

            self.embed_indexer.embed_pending(progress, collection.id, cancel)
            self.collections.mark_indexed(collection.id)
            progress.phase = "cancelled" if cancel.is_set() else "done"
            self._write_status(collection.id, progress)
        except Exception as error:
            progress.phase = "error"
            progress.message = f"{type(error).__name__}: {error}"
            log.warning("Faustus sync of %s failed: %s", collection.name, progress.message)
            self._write_status(collection.id, progress)
        finally:
            progress.finished_at = time.time()
            if client is not None:
                client.close()
            self.on_change()
        return progress

    def _write_status(self, collection_id: int, progress: Progress) -> None:
        self.collections.set_sync_status(collection_id, {
            "last_run": time.time(), "ok": progress.phase == "done",
            "sessions_total": progress.files_total, "sessions_changed": progress.files_changed,
            "sessions_removed": progress.files_removed, "error_count": len(progress.errors),
            "last_error": progress.message or (progress.errors[-1]["error"] if progress.errors else None),
        })

    def _sync_one(self, collection: Collection, client: FaustusClient, session: dict, known: tuple | None) -> bool:
        """Fetch and (re)index one conversation if it changed since the last sync. Returns whether it did."""
        sid = session["id"]
        last_at = session.get("last_message_at") or session.get("updated_at") or session.get("created_at") or ""
        mtime = _parse_ts(last_at)
        unchanged = known and known[4] == "ok" and known[5] >= INDEX_VERSION and mtime > 0 and abs(known[2] - mtime) < 1e-6
        if unchanged:
            return False
        export = client.export_session_json(sid)
        extracted, meta = build_extracted(session, export)
        units = merge_small_units(extracted.units)
        extracted.units = units
        chunks = chunk_units(units)
        digest = hashlib.sha256(json.dumps(export.get("messages", []), sort_keys=True, default=str).encode("utf-8")).hexdigest()
        self.documents.replace(collection.id, sid, extracted, chunks, extracted.chars, mtime or time.time(), digest, meta=meta)
        return True


class FaustusScheduler:
    """Polls every `poll_minutes` (per source) and enqueues a sync on the shared IndexWorker."""

    def __init__(self, collections: CollectionStore, worker, check_seconds: float = 60.0):
        self.collections = collections
        self.worker = worker
        self.check_seconds = check_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="borges-faustus-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.check_seconds):
            self._tick()

    def _tick(self) -> None:
        now = time.time()
        for collection in self.collections.list_by_kind("faustus"):
            if not collection.enabled:
                continue
            poll_minutes = float(collection.config.get("poll_minutes") or DEFAULT_POLL_MINUTES)
            last_run = float((collection.sync_status or {}).get("last_run") or 0)
            if now - last_run >= max(poll_minutes, 1) * 60:
                self.worker.enqueue(collection.id)
