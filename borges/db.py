"""The schema migrations; the connection, lock, transactions and migrations runner are the shared `hoard_link.sqlkit`."""

from __future__ import annotations

import sqlite3

from .hoard_link import sqlkit

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
    # 2: chunking rules version per document (0 = indexed before the rule existed → re-chunked on next reindex)
    """
    ALTER TABLE documents ADD COLUMN index_version INTEGER NOT NULL DEFAULT 0;
    """,
    # 3: sources beyond folders (kind + config on collections; free-form metadata on documents, used by
    # the Faustus source for conversation_id/project/created_at/updated_at/model/date/raw title).
    """
    ALTER TABLE collections ADD COLUMN kind TEXT NOT NULL DEFAULT 'folder';
    ALTER TABLE collections ADD COLUMN config TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE collections ADD COLUMN sync_status TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE documents ADD COLUMN meta TEXT NOT NULL DEFAULT '{}';
    """,
    # 4: question keys (2-3 generated questions per chunk as extra searchable keys; off by default, see question_keys.py)
    """
    CREATE TABLE chunk_questions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
      question TEXT NOT NULL
    );
    CREATE INDEX chunk_questions_chunk ON chunk_questions(chunk_id);
    CREATE VIRTUAL TABLE questions_fts USING fts5(
      question,
      content='chunk_questions', content_rowid='id',
      tokenize = 'unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER chunk_questions_ai AFTER INSERT ON chunk_questions BEGIN
      INSERT INTO questions_fts(rowid, question) VALUES (new.id, new.question);
    END;
    CREATE TRIGGER chunk_questions_ad AFTER DELETE ON chunk_questions BEGIN
      INSERT INTO questions_fts(questions_fts, rowid, question) VALUES ('delete', old.id, old.question);
    END;
    CREATE TABLE question_embeddings (
      question_id INTEGER PRIMARY KEY REFERENCES chunk_questions(id) ON DELETE CASCADE,
      model TEXT NOT NULL,
      dim INTEGER NOT NULL,
      vector BLOB NOT NULL
    );
    CREATE TABLE chunk_question_state (
      chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
      state TEXT NOT NULL,
      model TEXT NOT NULL DEFAULT '',
      generated_at REAL NOT NULL
    );
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


class Database(sqlkit.Database):
    """One connection shared by every thread behind a re-entrant lock (`hoard_link.sqlkit`); the app is the only
    writer, the MCP bridge never opens this file."""

    def __init__(self, path, **kw):
        check_sqlite()
        super().__init__(path, migrations=MIGRATIONS, **kw)
