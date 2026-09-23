"""Walk a collection folder, extract changed files, chunk, store and embed. Incremental by size+mtime, then hash."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import INDEX_VERSION, chunk_units, merge_small_units
from .embedder import Embedder
from .extract import extract, kind_for
from .store import Collection, CollectionStore, DocumentStore

log = logging.getLogger("borges.index")

MAX_FILE_BYTES = 200 * 1024 * 1024
EMBED_BATCH = 64


@dataclass
class Progress:
    collection_id: int
    phase: str = "queued"  # queued | scanning | extracting | embedding | done | error | cancelled
    files_total: int = 0
    files_done: int = 0
    files_changed: int = 0
    files_removed: int = 0
    chunks_embedded: int = 0
    chunks_to_embed: int = 0
    current_file: str = ""
    errors: list[dict] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return {**self.__dict__, "errors": list(self.errors[-200:]), "error_count": len(self.errors)}


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a glob with ** into a regex over a posix relative path."""
    pattern = pattern.replace("\\", "/").strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


class Matcher:
    def __init__(self, include: list[str], exclude: list[str]):
        self.include = [glob_to_regex(p) for p in include if p.strip()]
        self.exclude = [glob_to_regex(p) for p in exclude if p.strip()]

    def accepts(self, rel: str) -> bool:
        if any(r.match(rel) for r in self.exclude):
            return False
        if not self.include:
            return True
        return any(r.match(rel) for r in self.include)

    def excludes_dir(self, rel: str) -> bool:
        probe = rel.rstrip("/") + "/x"
        return any(r.match(rel) or r.match(probe) for r in self.exclude)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def walk(collection: Collection) -> list[tuple[str, Path]]:
    """(rel_path, abs_path) of every indexable file, sorted."""
    root = Path(collection.path)
    matcher = Matcher(collection.include, collection.exclude)
    found: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(d for d in dirnames if not matcher.excludes_dir(f"{rel_dir}/{d}" if rel_dir else d))
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            path = Path(dirpath) / name
            if kind_for(path, code=collection.code) is None or not matcher.accepts(rel):
                continue
            found.append((rel, path))
    return found


class Indexer:
    def __init__(self, collections: CollectionStore, documents: DocumentStore, embedder: Embedder, on_change=None):
        self.collections = collections
        self.documents = documents
        self.embedder = embedder
        self.on_change = on_change or (lambda: None)

    def index_collection(self, collection: Collection, progress: Progress, cancel: threading.Event | None = None) -> Progress:
        cancel = cancel or threading.Event()
        progress.phase = "scanning"
        progress.started_at = time.time()
        try:
            if not Path(collection.path).is_dir():
                raise FileNotFoundError(f"Folder not found: {collection.path}")
            files = walk(collection)
            progress.files_total = len(files)
            known = self.documents.fingerprints(collection.id)
            present = {rel for rel, _ in files}
            for rel, (doc_id, *_rest) in known.items():
                if rel not in present:
                    self.documents.remove(doc_id)
                    progress.files_removed += 1
            progress.phase = "extracting"
            for rel, path in files:
                if cancel.is_set():
                    progress.phase = "cancelled"
                    return progress
                progress.current_file = rel
                try:
                    self._index_file(collection, rel, path, known.get(rel), progress)
                except Exception as error:  # one bad file must not stop the run
                    log.warning("%s: %s", path, error)
                    progress.errors.append({"path": rel, "error": f"{type(error).__name__}: {error}"[:500]})
                progress.files_done += 1
            progress.current_file = ""
            self.embed_pending(progress, collection.id, cancel)
            self.collections.mark_indexed(collection.id)
            progress.phase = "cancelled" if cancel.is_set() else "done"
        except Exception as error:
            progress.phase = "error"
            progress.message = f"{type(error).__name__}: {error}"
            log.exception("indexing %s failed", collection.path)
        finally:
            progress.finished_at = time.time()
            self.on_change()
        return progress

    def _index_file(self, collection: Collection, rel: str, path: Path, known: tuple | None, progress: Progress) -> None:
        stat = path.stat()
        size, mtime = stat.st_size, stat.st_mtime
        current = bool(known) and known[4] == "ok" and known[5] >= INDEX_VERSION
        if current and known[1] == size and abs(known[2] - mtime) < 1e-6:
            return  # unchanged (cheap check, no read)
        if size > MAX_FILE_BYTES:
            raise ValueError(f"file too large ({size // (1024 * 1024)} MB)")
        digest = file_hash(path)
        if current and known[3] == digest:
            self.documents.touch(known[0], size, mtime)  # touched but identical
            return
        kind = kind_for(path, code=collection.code) or "txt"
        try:
            extracted = extract(path, kind)
        except Exception as error:
            self.documents.record_error(collection.id, rel, kind, size, mtime, digest, f"{type(error).__name__}: {error}")
            raise
        extracted.units = merge_small_units(extracted.units)
        chunks = chunk_units(extracted.units)
        self.documents.replace(collection.id, rel, extracted, chunks, size, mtime, digest)
        progress.files_changed += 1
        self.on_change()

    def embed_pending(self, progress: Progress, collection_id: int | None = None, cancel: threading.Event | None = None) -> int:
        """Embed every chunk that lacks a vector for the current model. Returns how many were embedded."""
        cancel = cancel or threading.Event()
        if not self.embedder.ensure_loaded():
            return 0
        model = self.embedder.model
        progress.phase = "embedding"
        progress.chunks_to_embed = self.documents.count_without_embedding(model)
        done = 0
        while not cancel.is_set():
            batch = self.documents.chunks_without_embedding(model, collection_id, EMBED_BATCH)
            if not batch:
                break
            ids = [cid for cid, _ in batch]
            matrix = self.embedder.embed_documents([text for _, text in batch])
            self.documents.store_embeddings(model, ids, matrix)
            done += len(ids)
            progress.chunks_embedded += len(ids)
            self.on_change()
        return done
