"""Wiring of database, stores, embedder, indexer, worker and watcher."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from . import __version__
from .config import Config
from .db import Database
from .embedder import make_embedder
from .hoard_link import paths
from .hoard_link.tokens import read_or_create_token
from .faustus_source import FaustusIndexer, FaustusScheduler
from .indexer import Indexer
from .links_source import LinksIndexer
from .queries import Queries
from .question_keys import QuestionKeys
from .search import Search
from .store import CollectionStore, DocumentStore
from .watcher import Watcher
from .worker import IndexWorker

log = logging.getLogger("borges")


class Services:
    def __init__(self, config: Config):
        self.config = config
        self.started_at = time.time()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.token = read_or_create_token(config.token_path)  # stable across restarts: a second instance must not rotate it
        self.db = Database(config.db_path)
        self.collections = CollectionStore(self.db)
        self.documents = DocumentStore(self.db)
        self.queries = Queries(self.db)
        self.embedder = make_embedder(config.embed_backend, config.model_name, config.model_cache, config.embed_providers)
        self.question_keys = QuestionKeys(self.db, self.embedder)
        self.search = Search(self.db, self.documents, self.queries, self.embedder, collections=self.collections, question_keys=self.question_keys)
        self.question_keys.on_change = self.search.invalidate
        self.indexer = Indexer(self.collections, self.documents, self.embedder, on_change=self.search.invalidate, question_keys=self.question_keys)
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
            log.info("%d documents need updated indexing; startup will reindex them", stale)
        if self.config.autostart or (stale and not self.config.defer_stale):
            self._preload = threading.Thread(target=self._warm_up, name="borges-model", daemon=True)
            self._preload.start()
            self.worker.enqueue_all()
        if self.config.watch:
            self.watcher.sync(self.collections.list())
        if self.config.autostart:
            self.faustus_scheduler.start()

    def wait_for_embedder(self, timeout: float) -> bool:
        """True once the embedding model is loaded, waiting up to `timeout` seconds for a load or download that is running (or
        starting one). A model that failed to load stays False at once."""
        if self.embedder.ready():
            return True
        loader = self._preload if self._preload is not None and self._preload.is_alive() else None
        if loader is None:
            loader = threading.Thread(target=self.embedder.ensure_loaded, name="borges-model", daemon=True)
            loader.start()
        loader.join(timeout)
        return self.embedder.ready()

    def _warm_up(self) -> None:
        if self.embedder.ensure_loaded():
            self.search.invalidate()

    def stop(self) -> None:
        self.faustus_scheduler.stop()
        self.watcher.stop()
        self.worker.stop()
        if self._preload is not None and self._preload.is_alive():
            self._preload.join()
        self.db.close()

    # ---------- collections ----------
    def add_collection(self, name: str, path: str, include: list[str], exclude: list[str] | None, watch: bool, code: bool):
        path = paths.clean_user_path(path)  # Explorer's "Copy as path" arrives quoted
        if Path(path).is_dir():  # a missing folder keeps the store's own message
            problem = paths.unsafe_folder(path, data_dir=self.config.data_dir, lang="en")
            if problem:
                raise ValueError(f"This folder cannot be indexed: {problem}.")
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

    def reindex(self, collection_id: int, force_ocr: bool = False) -> bool:
        """`force_ocr`: every PDF page of the collection goes to OCR / vision, not just the pages without a text layer."""
        collection = self.collections.get(collection_id)
        if collection is None:
            raise LookupError("Collection not found.")
        return self.worker.enqueue(collection_id, force_ocr=force_ocr)

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
            "question_keys": self.question_keys.status(probe=False),
            "reindex_needed": self.documents.count_stale(),
            "worker": self.worker.status(),
            "watching": self.watcher.watching(),
            "watch_error": self.watcher.error,
            "counts": {"documents": counts["documents"], "chunks": counts["chunks"], "errors": counts["errors"], "needs_ocr": counts["needs_ocr"]},
            "collections": counts["collections"],
            "started_at": self.started_at,
        }
