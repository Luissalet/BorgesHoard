"""Background indexing worker: one thread, a FIFO of collection ids, live progress per collection."""

from __future__ import annotations

import logging
import queue
import threading
import time

from .indexer import Indexer, Progress
from .store import CollectionStore

log = logging.getLogger("borges.worker")


class IndexWorker:
    """`indexers` maps a collection's `kind` (folder | faustus) to the object that indexes it.

    Every indexer implements `index_collection(collection, progress, cancel) -> Progress`, so the run
    loop below never needs to know what kind of source it is dispatching to.
    """

    def __init__(self, indexers: dict[str, Indexer] | Indexer, collections: CollectionStore):
        self.indexers = indexers if isinstance(indexers, dict) else {"folder": indexers}
        self.collections = collections
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._queued: set[int] = set()
        self._progress: dict[int, Progress] = {}
        self._cancel: dict[int, threading.Event] = {}
        self._current: int | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="borges-indexer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for event in list(self._cancel.values()):
            event.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=15)

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------- queueing ----------
    def enqueue(self, collection_id: int) -> bool:
        """Queue a (re)index; returns False when it was already queued or running."""
        with self._lock:
            if collection_id in self._queued or self._current == collection_id:
                return False
            self._queued.add(collection_id)
            self._progress[collection_id] = Progress(collection_id=collection_id)
        self._queue.put(collection_id)
        return True

    def cancel(self, collection_id: int) -> None:
        with self._lock:
            self._queued.discard(collection_id)
            event = self._cancel.get(collection_id)
            self._progress.pop(collection_id, None)
        if event:
            event.set()

    def enqueue_all(self) -> int:
        return sum(1 for c in self.collections.list() if c.enabled and self.enqueue(c.id))

    # ---------- state ----------
    def progress(self, collection_id: int) -> dict | None:
        p = self._progress.get(collection_id)
        return p.to_dict() if p else None

    def status(self) -> dict:
        with self._lock:
            queued = sorted(self._queued)
            current = self._current
        return {
            "running": self.running(),
            "current": current,
            "queued": queued,
            "queue_depth": len(queued),
            "busy": current is not None or bool(queued),
            "progress": {str(cid): p.to_dict() for cid, p in self._progress.items()},
            "last_error": self.last_error,
        }

    def wait_idle(self, timeout: float = 120.0) -> bool:
        """Block until the queue is drained (tests and the selftest script)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._current is None and not self._queued:
                    return True
            time.sleep(0.05)
        return False

    # ---------- loop ----------
    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None or self._stop.is_set():
                break
            with self._lock:
                if item not in self._queued:
                    continue  # cancelled while waiting
                self._queued.discard(item)
                self._current = item
                event = threading.Event()
                self._cancel[item] = event
                progress = self._progress.setdefault(item, Progress(collection_id=item))
            try:
                collection = self.collections.get(item)
                if collection is None:
                    progress.phase = "cancelled"
                else:
                    indexer = self.indexers.get(collection.kind)
                    if indexer is None:
                        progress.phase = "error"
                        progress.message = f"No indexer registered for source kind '{collection.kind}'."
                        self.last_error = progress.message
                    else:
                        indexer.index_collection(collection, progress, event)
                        if progress.phase == "error":
                            self.last_error = progress.message
            except Exception as error:  # pragma: no cover - defensive
                log.exception("worker crashed on %s", item)
                self.last_error = str(error)
            finally:
                with self._lock:
                    self._current = None
                    self._cancel.pop(item, None)
