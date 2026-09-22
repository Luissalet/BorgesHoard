"""Persistence for collections, documents, units, chunks and embeddings."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chunking import Chunk
from .db import Database
from .extract.base import Extracted

DEFAULT_EXCLUDE = ["**/node_modules/**", "**/.git/**", "**/venv/**", "**/.venv/**", "**/__pycache__/**", "**/~$*", "**/.*"]


@dataclass
class Collection:
    id: int
    name: str
    path: str
    include: list[str]
    exclude: list[str]
    enabled: bool
    watch: bool
    code: bool
    created_at: float
    last_indexed_at: float | None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "path": self.path, "include": self.include, "exclude": self.exclude,
                "enabled": self.enabled, "watch": self.watch, "code": self.code, "created_at": self.created_at,
                "last_indexed_at": self.last_indexed_at}


def _collection(row) -> Collection:
    return Collection(row["id"], row["name"], row["path"], json.loads(row["include"] or "[]"), json.loads(row["exclude"] or "[]"),
                      bool(row["enabled"]), bool(row["watch"]), bool(row["code"]), row["created_at"], row["last_indexed_at"])


class CollectionStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[Collection]:
        with self.db.lock:
            return [_collection(r) for r in self.db.conn.execute("SELECT * FROM collections ORDER BY name COLLATE NOCASE")]

    def get(self, collection_id: int) -> Collection | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        return _collection(row) if row else None

    def by_path(self, path: str) -> Collection | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM collections WHERE path = ?", (path,)).fetchone()
        return _collection(row) if row else None

    def add(self, name: str, path: str, include: list[str], exclude: list[str] | None, watch: bool, code: bool) -> tuple[Collection, bool]:
        """Create a collection; returns (collection, created). Adding an existing folder returns it unchanged."""
        resolved = str(Path(path).expanduser().resolve())
        if not Path(resolved).is_dir():
            raise ValueError(f"The folder does not exist: {path}")
        existing = self.by_path(resolved)
        if existing:
            return existing, False  # idempotent by folder path
        exclude = DEFAULT_EXCLUDE if exclude is None else exclude
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO collections(name, path, include, exclude, enabled, watch, code, created_at) VALUES (?,?,?,?,1,?,?,?)",
                (name.strip() or Path(resolved).name, resolved, json.dumps(include), json.dumps(exclude), int(watch), int(code), time.time()),
            )
            new_id = cursor.lastrowid
        return self.get(new_id), True

    def update(self, collection_id: int, patch: dict) -> Collection | None:
        allowed = {"name", "include", "exclude", "enabled", "watch", "code"}
        fields = {k: v for k, v in patch.items() if k in allowed and v is not None}
        if not fields:
            return self.get(collection_id)
        sets, values = [], []
        for key, value in fields.items():
            sets.append(f"{key} = ?")
            values.append(json.dumps(value) if key in ("include", "exclude") else int(value) if isinstance(value, bool) else value)
        values.append(collection_id)
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE collections SET {', '.join(sets)} WHERE id = ?", values)
        return self.get(collection_id)

    def mark_indexed(self, collection_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE collections SET last_indexed_at = ? WHERE id = ?", (time.time(), collection_id))

    def remove(self, collection_id: int) -> bool:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,)).rowcount > 0


class DocumentStore:
    def __init__(self, db: Database):
        self.db = db

    # ---------- incremental bookkeeping ----------
    def fingerprints(self, collection_id: int) -> dict[str, tuple[int, int, float, str, str]]:
        """rel_path → (id, size, mtime, hash, status) for every document of a collection."""
        with self.db.lock:
            rows = self.db.conn.execute("SELECT id, rel_path, size, mtime, hash, status FROM documents WHERE collection_id = ?", (collection_id,)).fetchall()
        return {r["rel_path"]: (r["id"], r["size"], r["mtime"], r["hash"], r["status"]) for r in rows}

    def touch(self, document_id: int, size: int, mtime: float) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE documents SET size = ?, mtime = ? WHERE id = ?", (size, mtime, document_id))

    def remove(self, document_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def record_error(self, collection_id: int, rel_path: str, kind: str, size: int, mtime: float, file_hash: str, error: str) -> int:
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO documents(collection_id, rel_path, title, kind, size, mtime, hash, status, error, indexed_at)
                   VALUES (?,?,?,?,?,?,?,'error',?,?)
                   ON CONFLICT(collection_id, rel_path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, hash=excluded.hash,
                   status='error', error=excluded.error, indexed_at=excluded.indexed_at""",
                (collection_id, rel_path, Path(rel_path).stem, kind, size, mtime, file_hash, error[:1000], time.time()),
            )
            return conn.execute("SELECT id FROM documents WHERE collection_id = ? AND rel_path = ?", (collection_id, rel_path)).fetchone()["id"]

    # ---------- replace a document's content ----------
    def replace(self, collection_id: int, rel_path: str, extracted: Extracted, chunks: list[Chunk], size: int, mtime: float, file_hash: str) -> tuple[int, list[int]]:
        """Insert or fully replace a document with its units and chunks. Returns (document_id, chunk_ids)."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM documents WHERE collection_id = ? AND rel_path = ?", (collection_id, rel_path)).fetchone()
            if row:
                document_id = row["id"]
                conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
                conn.execute("DELETE FROM units WHERE document_id = ?", (document_id,))
                conn.execute(
                    """UPDATE documents SET title=?, kind=?, size=?, mtime=?, hash=?, pages=?, units=?, chunks=?, chars=?, needs_ocr=?,
                       status='ok', error=NULL, indexed_at=? WHERE id=?""",
                    (extracted.title, extracted.kind, size, mtime, file_hash, extracted.pages, len(extracted.units), len(chunks),
                     extracted.chars, int(extracted.needs_ocr), time.time(), document_id),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO documents(collection_id, rel_path, title, kind, size, mtime, hash, pages, units, chunks, chars, needs_ocr, status, indexed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'ok',?)""",
                    (collection_id, rel_path, extracted.title, extracted.kind, size, mtime, file_hash, extracted.pages, len(extracted.units),
                     len(chunks), extracted.chars, int(extracted.needs_ocr), time.time()),
                )
                document_id = cursor.lastrowid
            unit_ids: list[int] = []
            for ordinal, unit in enumerate(extracted.units):
                cursor = conn.execute(
                    "INSERT INTO units(document_id, ordinal, kind, number, title, line_start, text) VALUES (?,?,?,?,?,?,?)",
                    (document_id, ordinal, unit.kind, unit.number, unit.title, unit.line_start, unit.text),
                )
                unit_ids.append(cursor.lastrowid)
            chunk_ids: list[int] = []
            for chunk in chunks:
                cursor = conn.execute(
                    "INSERT INTO chunks(document_id, unit_id, ordinal, page, section, line, char_start, char_end, text) VALUES (?,?,?,?,?,?,?,?,?)",
                    (document_id, unit_ids[chunk.unit_index], chunk.ordinal, chunk.page, chunk.section, chunk.line, chunk.char_start, chunk.char_end, chunk.text),
                )
                chunk_ids.append(cursor.lastrowid)
        return document_id, chunk_ids

    # ---------- embeddings ----------
    def chunks_without_embedding(self, model: str, collection_id: int | None = None, limit: int = 256) -> list[tuple[int, str]]:
        sql = """SELECT c.id, c.text FROM chunks c
                 LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model = ?
                 JOIN documents d ON d.id = c.document_id
                 WHERE e.chunk_id IS NULL"""
        params: list = [model]
        if collection_id is not None:
            sql += " AND d.collection_id = ?"
            params.append(collection_id)
        sql += " ORDER BY c.id LIMIT ?"
        params.append(limit)
        with self.db.lock:
            return [(r["id"], r["text"]) for r in self.db.conn.execute(sql, params)]

    def count_without_embedding(self, model: str) -> int:
        with self.db.lock:
            return self.db.conn.execute(
                "SELECT COUNT(*) FROM chunks c LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model = ? WHERE e.chunk_id IS NULL", (model,)
            ).fetchone()[0]

    def store_embeddings(self, model: str, chunk_ids: list[int], matrix: np.ndarray) -> None:
        if not chunk_ids:
            return
        dim = int(matrix.shape[1])
        rows = [(cid, model, dim, np.ascontiguousarray(matrix[i], dtype=np.float32).tobytes()) for i, cid in enumerate(chunk_ids)]
        with self.db.transaction() as conn:
            conn.executemany("INSERT OR REPLACE INTO embeddings(chunk_id, model, dim, vector) VALUES (?,?,?,?)", rows)

    def load_embeddings(self, model: str) -> tuple[np.ndarray, np.ndarray]:
        """(chunk_ids int64[n], matrix float32[n, dim]) for the whole library."""
        with self.db.lock:
            rows = self.db.conn.execute("SELECT chunk_id, dim, vector FROM embeddings WHERE model = ? ORDER BY chunk_id", (model,)).fetchall()
        if not rows:
            return np.zeros(0, dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
        dim = rows[0]["dim"]
        ids = np.fromiter((r["chunk_id"] for r in rows), dtype=np.int64, count=len(rows))
        matrix = np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32).reshape(len(rows), dim)
        return ids, matrix

    def embedding_for(self, chunk_id: int, model: str) -> np.ndarray | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT vector FROM embeddings WHERE chunk_id = ? AND model = ?", (chunk_id, model)).fetchone()
        return np.frombuffer(row["vector"], dtype=np.float32) if row else None

    # ---------- counts ----------
    def counts(self) -> dict:
        with self.db.lock:
            c = self.db.conn
            documents = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            errors = c.execute("SELECT COUNT(*) FROM documents WHERE status = 'error'").fetchone()[0]
            ocr = c.execute("SELECT COUNT(*) FROM documents WHERE needs_ocr = 1").fetchone()[0]
            per = c.execute(
                """SELECT col.id, col.name, COUNT(d.id) AS documents, COALESCE(SUM(d.chunks), 0) AS chunks,
                          SUM(CASE WHEN d.status = 'error' THEN 1 ELSE 0 END) AS errors, SUM(d.needs_ocr) AS needs_ocr, MAX(d.indexed_at) AS indexed_at
                   FROM collections col LEFT JOIN documents d ON d.collection_id = col.id GROUP BY col.id ORDER BY col.name COLLATE NOCASE"""
            ).fetchall()
        return {"documents": documents, "chunks": chunks, "errors": errors, "needs_ocr": ocr,
                "collections": [dict(r) for r in per]}
