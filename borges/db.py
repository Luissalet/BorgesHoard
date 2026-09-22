"""SQLite connection (WAL, FTS5) and ordered schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

MIN_SQLITE = (3, 35, 0)

MIGRATIONS: list[str] = [
    # 1: collections, documents, units (pages/sections), chunks, FTS, embeddings
    """
    CREATE TABLE collections (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      path TEXT NOT NULL UNIQUE,
      include TEXT NOT NULL DEFAULT '',
      exclude TEXT NOT NULL DEFAULT '',
      enabled INTEGER NOT NULL DEFAULT 1,
      watch INTEGER NOT NULL DEFAULT 0,
      code INTEGER NOT NULL DEFAULT 0,
      created_at REAL NOT NULL,
      last_indexed_at REAL
    );
    CREATE TABLE documents (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
      rel_path TEXT NOT NULL,
      title TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL,
      size INTEGER NOT NULL DEFAULT 0,
      mtime REAL NOT NULL DEFAULT 0,
      hash TEXT NOT NULL DEFAULT '',
      pages INTEGER NOT NULL DEFAULT 0,
      units INTEGER NOT NULL DEFAULT 0,
      chunks INTEGER NOT NULL DEFAULT 0,
      chars INTEGER NOT NULL DEFAULT 0,
      needs_ocr INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'ok',
      error TEXT,
      indexed_at REAL,
      UNIQUE(collection_id, rel_path)
    );
    CREATE INDEX documents_collection ON documents(collection_id, rel_path);
    CREATE TABLE units (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      ordinal INTEGER NOT NULL,
      kind TEXT NOT NULL,
      number INTEGER NOT NULL,
      title TEXT NOT NULL DEFAULT '',
      line_start INTEGER,
      text TEXT NOT NULL
    );
    CREATE INDEX units_document ON units(document_id, ordinal);
    CREATE TABLE chunks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
      ordinal INTEGER NOT NULL,
      page INTEGER,
      section TEXT NOT NULL DEFAULT '',
      line INTEGER,
      char_start INTEGER NOT NULL,
      char_end INTEGER NOT NULL,
      text TEXT NOT NULL
    );
    CREATE INDEX chunks_document ON chunks(document_id, ordinal);
    CREATE INDEX chunks_unit ON chunks(unit_id);
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
      text, section,
      content='chunks', content_rowid='id',
      tokenize = 'unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
      INSERT INTO chunks_fts(rowid, text, section) VALUES (new.id, new.text, new.section);
    END;
    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
      INSERT INTO chunks_fts(chunks_fts, rowid, text, section) VALUES ('delete', old.id, old.text, old.section);
    END;
    CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
      INSERT INTO chunks_fts(chunks_fts, rowid, text, section) VALUES ('delete', old.id, old.text, old.section);
      INSERT INTO chunks_fts(rowid, text, section) VALUES (new.id, new.text, new.section);
    END;
    CREATE TABLE embeddings (
      chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
      model TEXT NOT NULL,
      dim INTEGER NOT NULL,
      vector BLOB NOT NULL
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]


def check_sqlite() -> None:
    version = tuple(int(p) for p in sqlite3.sqlite_version.split("."))
    if version < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} is too old; need {'.'.join(map(str, MIN_SQLITE))}+.")
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError as error:  # pragma: no cover - depends on the build
        raise RuntimeError("This Python's SQLite has no FTS5 support; Borges needs it.") from error
    finally:
        probe.close()


class Database:
    """One connection shared by every thread, guarded by a re-entrant lock.

    The app is the only writer; the MCP bridge never opens this file.
    """

    def __init__(self, path: Path):
        check_sqlite()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for index, sql in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                script = f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({index});\nCOMMIT;"
                try:
                    self.conn.executescript(script)
                except Exception:
                    if self.conn.in_transaction:
                        self.conn.execute("ROLLBACK")
                    raise

    def transaction(self):
        """`with db.transaction():` — BEGIN IMMEDIATE / COMMIT (ROLLBACK on error) under the lock."""
        return _Transaction(self)

    def close(self) -> None:
        with self.lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.conn.close()


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.conn.execute("COMMIT")
            else:
                self.db.conn.execute("ROLLBACK")
        finally:
            self.db.lock.release()
        return False
