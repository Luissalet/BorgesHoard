"""Wiring of database, stores, embedder, indexer, worker and watcher."""

from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time

from . import __version__
from .config import Config
from .db import Database
from .embedder import make_embedder
from .faustus_source import FaustusIndexer, FaustusScheduler
from .indexer import Indexer
from .links_source import LinksIndexer
from .queries import Queries
from .search import Search
from .store import CollectionStore, DocumentStore
from .watcher import Watcher
from .worker import IndexWorker

log = logging.getLogger("borges")


def write_token(config: Config) -> str:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    config.token_path.write_text(token, encoding="utf-8")
    try:
        config.token_path.chmod(0o600)
    except OSError:
        pass
    return token


class Services:
    def __init__(self, config: Config):
        self.config = config
        self.started_at = time.time()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.token = write_token(config)
        self.db = Database(config.db_path)
        self.collections = CollectionStore(self.db)
        self.documents = DocumentStore(self.db)
        self.queries = Queries(self.db)
        self.embedder = make_embedder(config.embed_backend, config.model_name, config.model_cache, config.embed_providers)
        self.search = Search(self.db, self.documents, self.queries, self.embedder, collections=self.collections)
        self.indexer = Indexer(self.collections, self.documents, self.embedder, on_change=self.search.invalidate)
        self.faustus_indexer = FaustusIndexer(self.collections, self.documents, self.indexer, on_change=self.search.invalidate)
        self.links_indexer = LinksIndexer(self.collections, self.documents, self.indexer, on_change=self.search.invalidate)
        self.worker = IndexWorker({"folder": self.indexer, "faustus": self.faustus_indexer, "links": self.links_indexer}, self.collections)
        self.watcher = Watcher(self.worker.enqueue)
        self.faustus_scheduler = FaustusScheduler(self.collections, self.worker)
        self._preload: threading.Thread | None = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.worker.start()
        stale = self.documents.count_stale()
        if stale:
            log.info("%d documents were chunked with older rules: they will be re-chunked by the startup reindex", stale)
        if self.config.autostart or stale:
            self._preload = threading.Thread(target=self._warm_up, name="borges-model", daemon=True)
            self._preload.start()
            self.worker.enqueue_all()
        if self.config.watch:
            self.watcher.sync(self.collections.list())
        if self.config.autostart:
            self.faustus_scheduler.start()

    def _warm_up(self) -> None:
        if self.embedder.ensure_loaded():
            self.search.invalidate()

    def stop(self) -> None:
        self.faustus_scheduler.stop()
        self.watcher.stop()
        self.worker.stop()
        self.db.close()

    # ---------- collections ----------
    def add_collection(self, name: str, path: str, include: list[str], exclude: list[str] | None, watch: bool, code: bool):
        collection, created = self.collections.add(name, path, include, exclude, watch, code)
        if created:
            self.worker.enqueue(collection.id)
        if self.config.watch:
            self.watcher.sync(self.collections.list())
        return collection

    def update_collection(self, collection_id: int, patch: dict):
        collection = self.collections.update(collection_id, patch)
        if collection is None:
            return None
        if self.config.watch:
            self.watcher.sync(self.collections.list())
        if collection.enabled and any(k in patch and patch[k] is not None for k in ("include", "exclude", "code")):
            self.worker.enqueue(collection.id)
        return collection

    def remove_collection(self, collection_id: int) -> bool:
        self.worker.cancel(collection_id)
        removed = self.collections.remove(collection_id)
        if removed:
            self.search.invalidate()
            if self.config.watch:
                self.watcher.sync(self.collections.list())
        return removed

    def reindex(self, collection_id: int) -> bool:
        collection = self.collections.get(collection_id)
        if collection is None:
            raise LookupError("Collection not found.")
        return self.worker.enqueue(collection_id)

    # ---------- sources (Faustus, and any future non-folder source) ----------
    def add_faustus_source(self, name: str, config: dict, enabled: bool = True):
        from .faustus_source import faustus_unique_key

        collection, created = self.collections.add_source("faustus", name, faustus_unique_key(config.get("base_url", "")), config, enabled)
        if created:
            self.worker.enqueue(collection.id)
        return collection

    def add_links_source(self, name: str, config: dict, enabled: bool = True):
        """A Links Hoard source: through the hub proxy unless `base_url` (+ `token`) points at it directly."""
        from .links_source import links_unique_key

        collection, created = self.collections.add_source("links", name or "Mis enlaces", links_unique_key(config.get("base_url", "")), config, enabled)
        if created:
            self.worker.enqueue(collection.id)
        return collection

    def update_source(self, collection_id: int, name: str | None, config_patch: dict, enabled: bool | None, kinds=("faustus", "links")):
        collection = self.collections.get(collection_id)
        if collection is None or collection.kind not in kinds:
            return None
        if config_patch:
            collection = self.collections.update_config(collection_id, config_patch)
        patch = {}
        if name is not None:
            patch["name"] = name
        if enabled is not None:
            patch["enabled"] = enabled
        if patch:
            collection = self.collections.update(collection_id, patch)
        return collection

    def update_faustus_source(self, collection_id: int, name: str | None, config_patch: dict, enabled: bool | None):
        return self.update_source(collection_id, name, config_patch, enabled, kinds=("faustus",))

    def sync_source(self, collection_id: int) -> bool:
        """Manual 'sync now' for a source; identical machinery to a folder reindex."""
        return self.reindex(collection_id)

    # ---------- status ----------
    def status(self) -> dict:
        counts = self.documents.counts()
        try:
            disk_free = shutil.disk_usage(self.config.data_dir).free
        except OSError:
            disk_free = None
        try:
            db_bytes = self.config.db_path.stat().st_size
        except OSError:
            db_bytes = 0
        model = self.embedder.status()
        pending = self.documents.count_without_embedding(self.embedder.model) if self.embedder.model else 0
        return {
            "service": "borges-hoard",
            "version": __version__,
            "data_dir": str(self.config.data_dir),
            "db_bytes": db_bytes,
            "disk_free_bytes": disk_free,
            "model": model,
            "chunks_pending_embedding": pending,
            "reindex_needed": self.documents.count_stale(),
            "worker": self.worker.status(),
            "watching": self.watcher.watching(),
            "watch_error": self.watcher.error,
            "counts": {"documents": counts["documents"], "chunks": counts["chunks"], "errors": counts["errors"], "needs_ocr": counts["needs_ocr"]},
            "collections": counts["collections"],
            "started_at": self.started_at,
        }
