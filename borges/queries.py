"""Read-side queries for documents, outlines, passages and chunks."""

from __future__ import annotations

import json
from pathlib import Path

from .db import Database

KIND_LABEL = {"pdf": "PDF", "docx": "Word", "md": "Markdown", "txt": "Texto", "epub": "EPUB", "html": "HTML", "csv": "CSV", "code": "Código",
              "chat": "Chat"}


def _meta(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def document_json(row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"], "collection_id": row["collection_id"], "rel_path": row["rel_path"], "title": row["title"], "kind": row["kind"],
        "size": row["size"], "mtime": row["mtime"], "pages": row["pages"], "units": row["units"], "chunks": row["chunks"], "chars": row["chars"],
        "needs_ocr": bool(row["needs_ocr"]), "status": row["status"], "error": row["error"], "indexed_at": row["indexed_at"],
        "index_version": row["index_version"], "filename": Path(row["rel_path"]).name,
        "meta": _meta(row["meta"]) if "meta" in keys else {},
    }


def citation(doc: dict, page: int | None, section: str, line: int | None = None) -> str:
    """Human citation: «Título», p. 12 · archivo.md § Sección · «Libro», cap. 3 · [chat «Título» · fecha · turno N]."""
    kind = doc["kind"]
    if kind == "chat":
        meta = _meta(doc.get("meta"))
        title = meta.get("title") or doc["title"]
        date = meta.get("date") or ""
        parts = [p for p in (date, section) if p]
        return f"[chat «{title}»" + (f" · {' · '.join(parts)}]" if parts else "]")
    if kind == "pdf":
        return f"«{doc['title']}», p. {page}" if page else f"«{doc['title']}»"
    if kind == "epub":
        return f"«{doc['title']}», cap. {section}" if section else f"«{doc['title']}»"
    name = doc["filename"]
    if section:
        return f"{name} § {section}"
    if line:
        return f"{name}, l. {line}"
    return name


class Queries:
    def __init__(self, db: Database):
        self.db = db

    def list_documents(self, collection_id: int | None, q: str, limit: int, cursor: int | None, status: str | None = None) -> dict:
        sql = "SELECT d.* FROM documents d WHERE 1=1"
        params: list = []
        if collection_id is not None:
            sql += " AND d.collection_id = ?"
            params.append(collection_id)
        if q:
            sql += " AND (d.title LIKE ? OR d.rel_path LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if status:
            sql += " AND d.status = ?"
            params.append(status)
        if cursor:
            sql += " AND d.id > ?"
            params.append(cursor)
        sql += " ORDER BY d.id LIMIT ?"
        params.append(limit + 1)
        with self.db.lock:
            rows = self.db.conn.execute(sql, params).fetchall()
        docs = [document_json(r) for r in rows[:limit]]
        return {"documents": docs, "next_cursor": docs[-1]["id"] if len(rows) > limit else None}

    def document(self, document_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
            if not row:
                return None
            units = self.db.conn.execute(
                "SELECT id, ordinal, kind, number, title, line_start, LENGTH(text) AS chars FROM units WHERE document_id = ? ORDER BY ordinal", (document_id,)
            ).fetchall()
            col = self.db.conn.execute("SELECT name, path FROM collections WHERE id = ?", (row["collection_id"],)).fetchone()
        doc = document_json(row)
        doc["collection"] = col["name"] if col else None
        doc["path"] = str(Path(col["path"]) / row["rel_path"]) if col else row["rel_path"]
        doc["outline"] = [dict(u) for u in units]
        return doc

    def document_brief(self, document_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return document_json(row) if row else None

    def unit(self, document_id: int, page: int | None = None, section: int | None = None, unit_id: int | None = None) -> dict | None:
        """One page/section with its text plus prev/next pointers. Defaults to the first unit."""
        with self.db.lock:
            c = self.db.conn
            if unit_id is not None:
                row = c.execute("SELECT * FROM units WHERE id = ? AND document_id = ?", (unit_id, document_id)).fetchone()
            elif page is not None:
                row = c.execute("SELECT * FROM units WHERE document_id = ? AND kind = 'page' AND number = ?", (document_id, page)).fetchone()
                if row is None:  # a short page merged into a neighbour: serve the unit that now holds it
                    pages = c.execute("SELECT pages FROM documents WHERE id = ?", (document_id,)).fetchone()
                    if pages and 1 <= page <= pages["pages"]:
                        row = c.execute("SELECT * FROM units WHERE document_id = ? AND kind = 'page' AND number > ? ORDER BY number LIMIT 1", (document_id, page)).fetchone() \
                            or c.execute("SELECT * FROM units WHERE document_id = ? AND kind = 'page' AND number < ? ORDER BY number DESC LIMIT 1", (document_id, page)).fetchone()
            elif section is not None:
                row = c.execute("SELECT * FROM units WHERE document_id = ? AND kind != 'page' AND number = ?", (document_id, section)).fetchone()
            else:
                row = c.execute("SELECT * FROM units WHERE document_id = ? ORDER BY ordinal LIMIT 1", (document_id,)).fetchone()
            if not row:
                return None
            prev = c.execute("SELECT id, kind, number, title FROM units WHERE document_id = ? AND ordinal = ?", (document_id, row["ordinal"] - 1)).fetchone()
            nxt = c.execute("SELECT id, kind, number, title FROM units WHERE document_id = ? AND ordinal = ?", (document_id, row["ordinal"] + 1)).fetchone()
            total = c.execute("SELECT COUNT(*) FROM units WHERE document_id = ?", (document_id,)).fetchone()[0]
        return {"id": row["id"], "document_id": document_id, "ordinal": row["ordinal"], "kind": row["kind"], "number": row["number"],
                "title": row["title"], "line_start": row["line_start"], "text": row["text"], "total": total,
                "prev": dict(prev) if prev else None, "next": dict(nxt) if nxt else None}

    def chunk(self, chunk_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT c.*, d.title AS doc_title, d.kind AS doc_kind, d.rel_path FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ?",
                (chunk_id,),
            ).fetchone()
        return dict(row) if row else None

    def chunks_by_ids(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self.db.lock:
            rows = self.db.conn.execute(
                f"""SELECT c.id, c.document_id, c.unit_id, c.ordinal, c.page, c.section, c.line, c.char_start, c.char_end, c.text,
                           d.title, d.kind, d.rel_path, d.collection_id, d.meta AS doc_meta, col.name AS collection, col.path AS collection_path,
                           col.kind AS source_kind
                    FROM chunks c JOIN documents d ON d.id = c.document_id JOIN collections col ON col.id = d.collection_id
                    WHERE c.id IN ({marks})""",
                ids,
            ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def collection_chunk_ids(self, collection_id: int) -> set[int]:
        return self.chunk_ids_for_collections({collection_id})

    def chunk_ids_for_collections(self, collection_ids: set[int]) -> set[int]:
        if not collection_ids:
            return set()
        marks = ",".join("?" for _ in collection_ids)
        with self.db.lock:
            rows = self.db.conn.execute(
                f"SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.collection_id IN ({marks})",
                list(collection_ids),
            ).fetchall()
        return {r["id"] for r in rows}

    def recent_chats(self, limit: int = 10, project: str | None = None) -> list[dict]:
        """The most recently indexed Faustus conversations, newest first — for the `chats_recent` tool."""
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT d.id, d.title, d.meta, d.indexed_at, col.id AS collection_id, col.name AS collection
                   FROM documents d JOIN collections col ON col.id = d.collection_id
                   WHERE d.kind = 'chat' AND d.status = 'ok' ORDER BY d.indexed_at DESC LIMIT ?""",
                (max(limit, 1) * 5,),  # over-fetch: project filtering happens in Python below
            ).fetchall()
        out = []
        for row in rows:
            meta = _meta(row["meta"])
            if project and (meta.get("project") or "") != project:
                continue
            out.append({
                "document_id": row["id"], "title": meta.get("title") or row["title"], "date": meta.get("date"),
                "project": meta.get("project") or None, "conversation_id": meta.get("conversation_id"),
                "model": meta.get("model"), "collection": row["collection"], "collection_id": row["collection_id"],
                "indexed_at": row["indexed_at"],
            })
            if len(out) >= limit:
                break
        return out
