"""The Links source: index the user's saved links (Links Hoard) as citable documents.

A collection of kind `links` holds in its `config`: `state` (which links to take: `all`, `unread`, `read`
or `archived`; default `all`), optional `tag` (only links carrying it), `poll_minutes`, and — only when
the hub is not there — `base_url` + `token` to reach Links Hoard directly. The normal path goes through
the Hoard Hub proxy (`family.call("links", "list_links", ...)`): Borges never needs Links Hoard's port or
token file, only its own.

One saved link becomes one document (`kind="link"`, `rel_path` = the link id) whose text is what Links
Hoard extracted from the page (`read_link`), preceded by the user's own note and the page's excerpt. The
existing chunking/embedding/FTS pipeline is reused unchanged, and the citation reads
`[enlace «Title» · site]`. A link is re-read only when its fingerprint (title, word count, fetch status,
note, tags) changed since the last sync; a link deleted in Links Hoard leaves the index.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

import httpx

from .chunking import INDEX_VERSION, chunk_units, merge_small_units
from .extract.base import Extracted, Unit, finish
from .hoard_link import family
from .indexer import Progress
from .store import Collection, CollectionStore, DocumentStore

log = logging.getLogger("borges.links")

DEFAULT_POLL_MINUTES = 30
PAGE = 100  # list_links maximum page
READ_CHARS = 20000  # read_link maximum slice
MAX_LINK_CHARS = 400_000
STATES = ("all", "unread", "read", "archived")

Caller = Callable[[str, dict], dict]  # (tool, arguments) -> the tool's result


class LinksError(RuntimeError):
    """Anything that stops a sync: hub or Links Hoard unreachable, refused token, unexpected shape."""


def links_unique_key(base_url: str = "") -> str:
    """`collections.path` for a links source: one per Links Hoard instance (the hub's, or a direct URL)."""
    base = (base_url or "").strip().rstrip("/").lower()
    return "links://" + (base or "hub")


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class LinksClient:
    """Calls Links Hoard's agent tools — through the hub by default, directly when `base_url` is set.

    `caller` is only ever set by tests: a function `(tool, arguments) -> result` standing in for the network.
    """

    def __init__(self, base_url: str = "", token: str = "", timeout: float = 30.0, caller: Caller | None = None,
                 client: httpx.Client | None = None):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self._caller = caller
        self._client = client
        self.via = "test" if caller else ("direct" if self.base_url else "hub")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def call(self, tool: str, arguments: dict) -> dict:
        if self._caller is not None:
            return self._caller(tool, arguments)
        if self.base_url:
            return self._direct(tool, arguments)
        answer = family.call("links", tool, arguments, timeout=self.timeout)
        if not answer.get("ok"):
            raise LinksError(f"links/{tool} through the hub: {answer.get('error') or 'HTTP ' + str(answer.get('status'))}")
        result = answer.get("result")
        if not isinstance(result, dict):
            raise LinksError(f"Unexpected result from links/{tool} (expected an object).")
        return result

    def _direct(self, tool: str, arguments: dict) -> dict:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = self._client.post("/api/agent/call", json={"name": tool, "arguments": arguments, "caller": "borges"}, headers=headers)
        except httpx.HTTPError as error:
            raise LinksError(f"Could not reach Links Hoard at {self.base_url}: {error}") from error
        if response.status_code != 200:
            raise LinksError(f"Links Hoard {tool} returned HTTP {response.status_code}: {response.text[:300]}")
        body = response.json()
        result = body.get("result") if isinstance(body, dict) and "result" in body else body
        if not isinstance(result, dict):
            raise LinksError(f"Unexpected result from Links Hoard {tool}.")
        return result

    def list_links(self, state: str = "all", tag: str | None = None) -> list[dict]:
        """Every link of the requested state, following `next_cursor` page by page."""
        out: list[dict] = []
        cursor = 0
        for _ in range(500):  # 50 000 links is plenty; never loop forever on a misbehaving server
            args: dict[str, Any] = {"state": state if state in STATES else "all", "limit": PAGE, "cursor": cursor}
            if tag:
                args["tag"] = tag
            page = self.call("list_links", args)
            items = page.get("items")
            if not isinstance(items, list):
                raise LinksError("Unexpected response from list_links (expected items).")
            out.extend(i for i in items if isinstance(i, dict) and i.get("id"))
            nxt = page.get("next_cursor")
            if nxt is None or not items or int(nxt) <= cursor:
                break
            cursor = int(nxt)
        return out

    def read_link(self, link_id: str) -> tuple[dict, str]:
        """The link's extracted text in full (paged by `offset`), plus the header read_link returns."""
        header: dict = {}
        parts: list[str] = []
        offset = 0
        total = 0
        while True:
            page = self.call("read_link", {"id": link_id, "offset": offset, "max_chars": READ_CHARS})
            if not header:
                header = page
            text = page.get("text") or ""
            parts.append(text)
            total += len(text)
            if not page.get("has_more") or not text or total >= MAX_LINK_CHARS:
                break
            offset += len(text)
        return header, "".join(parts)


def build_client(config: dict) -> LinksClient:
    return LinksClient(config.get("base_url", ""), config.get("token", ""))


# ---------------------------------------------------------------------------
# link -> document
# ---------------------------------------------------------------------------


def _date_str(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10]


def _parse_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def fingerprint(link: dict) -> str:
    """What must change for a link to be re-read: cheap, from the listing alone (no read_link call)."""
    keys = (link.get("title"), link.get("word_count"), link.get("fetch_status"), link.get("notes"),
            sorted(link.get("tags") or []), link.get("url"), link.get("excerpt"))
    return hashlib.sha256(json.dumps(keys, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_extracted(link: dict, text: str) -> tuple[Extracted, dict]:
    """One document per link: a header unit (title, site, note, excerpt) and the extracted text.

    A link whose page could not be fetched still becomes a document out of its title, excerpt and the
    user's note — that is what the user saved, and it is enough to find the link again.
    """
    title = (link.get("title") or link.get("url") or "").strip() or "(sin título)"
    site = (link.get("site") or "").strip()
    saved_at = link.get("saved_at") or ""
    date = _date_str(saved_at)
    lines = [f"{title}"]
    if site or link.get("byline"):
        lines.append(" · ".join(p for p in (site, (link.get("byline") or "").strip()) if p))
    if link.get("url"):
        lines.append(str(link["url"]))
    if link.get("tags"):
        lines.append("Etiquetas: " + ", ".join(str(t) for t in link["tags"]))
    if (link.get("notes") or "").strip():
        lines.append("Nota: " + str(link["notes"]).strip())
    if (link.get("excerpt") or "").strip():
        lines.append(str(link["excerpt"]).strip())
    raw_units = [Unit(kind="section", number=1, title="ficha", text="\n".join(lines)),
                 Unit(kind="section", number=2, title="texto", text=(text or "")[:MAX_LINK_CHARS])]
    units = finish(raw_units)
    extracted = Extracted(title=f"Enlace: {title}", kind="link", units=units, needs_ocr=False, pages=0)
    meta = {
        "link_id": link.get("id") or "", "url": link.get("url") or "", "site": site, "title": title, "date": date,
        "saved_at": saved_at, "byline": link.get("byline") or "", "tags": list(link.get("tags") or []),
        "fetch_status": link.get("fetch_status") or "", "word_count": int(link.get("word_count") or 0),
    }
    return extracted, meta


# ---------------------------------------------------------------------------
# indexer (same shape as Indexer.index_collection, so IndexWorker treats it identically)
# ---------------------------------------------------------------------------


class LinksIndexer:
    def __init__(self, collections: CollectionStore, documents: DocumentStore, embed_indexer, on_change=None):
        self.collections = collections
        self.documents = documents
        self.embed_indexer = embed_indexer
        self.on_change = on_change or (lambda: None)

    def index_collection(self, collection: Collection, progress: Progress, cancel: threading.Event | None = None) -> Progress:
        cancel = cancel or threading.Event()
        progress.phase = "scanning"
        progress.started_at = time.time()
        client: LinksClient | None = None
        try:
            client = build_client(collection.config)
            links = client.list_links(collection.config.get("state") or "all", collection.config.get("tag") or None)
            progress.files_total = len(links)

            known = self.documents.fingerprints(collection.id)
            present = {str(l.get("id")) for l in links}
            for rel, (doc_id, *_rest) in known.items():
                if rel not in present:
                    self.documents.remove(doc_id)
                    progress.files_removed += 1

            progress.phase = "extracting"
            for link in links:
                if cancel.is_set():
                    progress.phase = "cancelled"
                    return progress
                lid = str(link.get("id"))
                progress.current_file = link.get("title") or link.get("url") or lid
                try:
                    if self._sync_one(collection, client, link, known.get(lid)):
                        progress.files_changed += 1
                        self.on_change()
                except Exception as error:  # one bad link must not stop the sync
                    log.warning("links %s: %s", lid, error)
                    progress.errors.append({"path": lid, "error": f"{type(error).__name__}: {error}"[:500]})
                progress.files_done += 1
            progress.current_file = ""

            self.embed_indexer.embed_pending(progress, collection.id, cancel)
            self.collections.mark_indexed(collection.id)
            progress.phase = "cancelled" if cancel.is_set() else "done"
            self._write_status(collection.id, progress, client.via)
            if progress.files_changed or progress.files_removed:
                family.emit("borges.source.synced", {"kind": "links", "collection_id": collection.id, "name": collection.name,
                                                     "changed": progress.files_changed, "removed": progress.files_removed})
        except Exception as error:
            progress.phase = "error"
            progress.message = f"{type(error).__name__}: {error}"
            log.warning("Links sync of %s failed: %s", collection.name, progress.message)
            self._write_status(collection.id, progress, client.via if client else "hub")
        finally:
            progress.finished_at = time.time()
            if client is not None:
                client.close()
            self.on_change()
        return progress

    def _write_status(self, collection_id: int, progress: Progress, via: str) -> None:
        self.collections.set_sync_status(collection_id, {
            "last_run": time.time(), "ok": progress.phase == "done", "via": via,
            "links_total": progress.files_total, "links_changed": progress.files_changed,
            "links_removed": progress.files_removed, "error_count": len(progress.errors),
            "last_error": progress.message or (progress.errors[-1]["error"] if progress.errors else None),
        })

    def _sync_one(self, collection: Collection, client: LinksClient, link: dict, known: tuple | None) -> bool:
        """Read and (re)index one link when its fingerprint changed. Returns whether it did."""
        lid = str(link["id"])
        digest = fingerprint(link)
        if known and known[4] == "ok" and known[5] >= INDEX_VERSION and known[3] == digest:
            return False
        text = ""
        if (link.get("fetch_status") or "") in ("ok", "done", "fetched") or int(link.get("word_count") or 0) > 0:
            _header, text = client.read_link(lid)
        extracted, meta = build_extracted(link, text)
        units = merge_small_units(extracted.units)
        extracted.units = units
        chunks = chunk_units(units)
        mtime = _parse_ts(link.get("saved_at")) or time.time()
        self.documents.replace(collection.id, lid, extracted, chunks, extracted.chars, mtime, digest, meta=meta)
        return True
