"""Question keys: 2-3 questions a small local model writes for each chunk, stored as extra searchable keys.

A key is a short question the chunk answers ("how much does the telescope mirror repair cost?"). Keys are searched by
BM25 (FTS5) and, when the embedder is loaded, by cosine; both legs join the rank fusion of `search.Search` and collapse
back to the chunk, so a question-shaped query finds a passage that never uses the query's words.

Off by default (`settings`: question_keys.enabled). Never blocks indexing: with no model reachable the pending chunks
are marked `unavailable` and retried on the next run. Chunk states (table chunk_question_state):

  ok           the chunk has keys
  empty        the model answered but nothing usable (or the chunk is too short to ask about)
  unavailable  the model could not be reached when the chunk was processed; retried later
  (no row)     never processed
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Protocol

import httpx
import numpy as np

from .db import Database
from .embedder import Embedder

log = logging.getLogger("borges.questions")

QUESTIONS_PER_CHUNK = 3
MIN_CHUNK_CHARS = 60  # shorter chunks are not worth a model call
PROMPT_CHUNK_CHARS = 1500
MAX_QUESTION_CHARS = 200
MIN_QUESTION_CHARS = 8
MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # an OpenAI-compatible endpoint; this is where a local Ollama answers
EMBED_BATCH = 128

PROMPT = (
    "Write {n} short, different questions that the passage below answers. Use the same language as the passage and the words "
    "a person would use to ask, not the passage's own phrasing. One question per line, no numbering, no explanations.\n\n"
    "Passage:\n{text}\n\nQuestions:"
)

SETTING_KEYS = ("enabled", "base_url", "model", "api_key")


class GeneratorUnavailable(RuntimeError):
    """The question model cannot be reached right now."""


class QuestionGenerator(Protocol):
    name: str
    model: str

    def available(self) -> bool: ...

    def generate(self, text: str, n: int = QUESTIONS_PER_CHUNK) -> list[str]: ...


def parse_questions(raw: str, n: int = QUESTIONS_PER_CHUNK) -> list[str]:
    """Questions out of a model answer: one per line, list markers removed, duplicates and non-questions dropped."""
    out: list[str] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip('"').strip()
        if not text or len(text) < MIN_QUESTION_CHARS or not (text.endswith("?") or text.startswith("¿")):
            continue
        text = text[:MAX_QUESTION_CHARS]
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= n:
            break
    return out


class LocalModelGenerator:
    """Asks a local OpenAI-compatible chat endpoint (Ollama, llama.cpp, vLLM, a Hoard Hub model...) for the questions."""

    name = "openai-compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60.0):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._probe: tuple[float, bool] = (0.0, False)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def available(self) -> bool:
        if not self.model:
            return False
        stamp, ok = self._probe
        if time.time() - stamp < 20:
            return ok
        try:
            ok = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=3.0, trust_env=False).status_code < 500
        except Exception:  # noqa: BLE001 - anything means "not reachable"
            ok = False
        self._probe = (time.time(), ok)
        return ok

    def generate(self, text: str, n: int = QUESTIONS_PER_CHUNK) -> list[str]:
        body = {"model": self.model, "temperature": 0.2, "max_tokens": 220, "stream": False,
                "messages": [{"role": "user", "content": PROMPT.format(n=n, text=text[:PROMPT_CHUNK_CHARS])}]}
        try:
            response = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers(), timeout=self.timeout, trust_env=False)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"] or ""
        except Exception as error:  # noqa: BLE001
            self._probe = (time.time(), False)
            raise GeneratorUnavailable(f"{type(error).__name__}: {error}") from error
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)  # reasoning models
        return parse_questions(content, n)


class QuestionKeys:
    """Settings, generation, storage and the two search legs of the question keys."""

    def __init__(self, db: Database, embedder: Embedder, on_change: Callable[[], None] | None = None):
        self.db = db
        self.embedder = embedder
        self.on_change = on_change or (lambda: None)
        self.generator_override: QuestionGenerator | None = None  # tests and callers that bring their own model
        self._lock = threading.Lock()
        self._cache_dirty = True
        self._qids = np.zeros(0, dtype=np.int64)
        self._chunk_of = np.zeros(0, dtype=np.int64)
        self._matrix = np.zeros((0, 0), dtype=np.float32)
        self._generating = threading.Lock()

    # ---------- settings ----------
    def settings(self, redact: bool = True) -> dict:
        import os

        def get(key, default):
            value = self.db.get_setting(f"question_keys.{key}", None)
            return default if value is None else value

        key = get("api_key", "") or ""
        return {
            "enabled": bool(get("enabled", False)),
            "base_url": get("base_url", "") or os.environ.get("BORGES_QUESTIONS_URL", "") or DEFAULT_BASE_URL,
            "model": get("model", "") or os.environ.get("BORGES_QUESTIONS_MODEL", ""),
            "api_key": ("*" * 8 if key else "") if redact else (key or os.environ.get("BORGES_QUESTIONS_KEY", "")),
        }

    def update_settings(self, patch: dict) -> dict:
        for key in SETTING_KEYS:
            value = patch.get(key)
            if value is None:
                continue
            self.db.set_setting(f"question_keys.{key}", bool(value) if key == "enabled" else str(value).strip())
        return self.settings()

    @property
    def enabled(self) -> bool:
        return self.settings()["enabled"]

    def generator(self) -> QuestionGenerator:
        if self.generator_override is not None:
            return self.generator_override
        cfg = self.settings(redact=False)
        return LocalModelGenerator(cfg["base_url"], cfg["model"], cfg["api_key"])

    # ---------- state ----------
    def has_keys(self) -> bool:
        return bool(self.db.scalar("SELECT 1 FROM chunk_questions LIMIT 1"))

    def status(self, document_id: int | None = None, collection_id: int | None = None, probe: bool = True) -> dict:
        """Counts of chunks by key state. `probe` asks the model endpoint whether it answers (`available` is None when not asked)."""
        where, params = ["1=1"], []
        if document_id is not None:
            where.append("c.document_id = ?")
            params.append(document_id)
        if collection_id is not None:
            where.append("d.collection_id = ?")
            params.append(collection_id)
        cond = " AND ".join(where)
        with self.db.lock:
            conn = self.db.conn
            rows = conn.execute(
                f"""SELECT COALESCE(s.state, 'pending') AS state, COUNT(*) AS n FROM chunks c JOIN documents d ON d.id = c.document_id
                    LEFT JOIN chunk_question_state s ON s.chunk_id = c.id WHERE {cond} GROUP BY 1""", params).fetchall()
            questions = conn.execute(
                f"""SELECT COUNT(*) FROM chunk_questions q JOIN chunks c ON c.id = q.chunk_id JOIN documents d ON d.id = c.document_id
                    WHERE {cond}""", params).fetchone()[0]
            unembedded = conn.execute(
                """SELECT COUNT(*) FROM chunk_questions q LEFT JOIN question_embeddings e ON e.question_id = q.id AND e.model = ?
                   WHERE e.question_id IS NULL""", (self.embedder.model,)).fetchone()[0] if self.embedder.model else 0
        counts = {"ok": 0, "empty": 0, "unavailable": 0, "pending": 0}
        counts.update({r["state"]: r["n"] for r in rows})
        cfg = self.settings()
        generator = self.generator()
        reachable = None
        if probe:
            try:
                reachable = bool(generator.available())
            except Exception:  # noqa: BLE001
                reachable = False
        return {
            "enabled": cfg["enabled"],
            "generator": {"name": getattr(generator, "name", "?"), "model": getattr(generator, "model", ""), "base_url": cfg["base_url"],
                          "available": reachable},
            "scope": {"document_id": document_id, "collection_id": collection_id},
            "chunks": sum(counts.values()),
            "chunks_with_keys": counts["ok"],
            "chunks_without_keys": counts["empty"] + counts["unavailable"] + counts["pending"],
            "chunks_empty": counts["empty"],
            "chunks_unavailable": counts["unavailable"],
            "chunks_pending": counts["pending"],
            "questions": questions,
            "questions_pending_embedding": unembedded,
            "keys_exist": questions > 0,
        }

    def questions_for(self, chunk_id: int) -> list[str]:
        with self.db.lock:
            return [r["question"] for r in self.db.conn.execute("SELECT question FROM chunk_questions WHERE chunk_id = ? ORDER BY id", (chunk_id,))]

    # ---------- generation ----------
    def _select(self, document_id: int | None, collection_id: int | None, force: bool, limit: int) -> list[tuple[int, str]]:
        where, params = ["1=1"], []
        if document_id is not None:
            where.append("c.document_id = ?")
            params.append(document_id)
        if collection_id is not None:
            where.append("d.collection_id = ?")
            params.append(collection_id)
        if not force:
            where.append("(s.chunk_id IS NULL OR s.state = 'unavailable')")
        sql = (f"SELECT c.id, c.text FROM chunks c JOIN documents d ON d.id = c.document_id LEFT JOIN chunk_question_state s ON s.chunk_id = c.id "
               f"WHERE {' AND '.join(where)} ORDER BY c.id LIMIT ?")
        with self.db.lock:
            return [(r["id"], r["text"]) for r in self.db.conn.execute(sql, [*params, limit])]

    def _store(self, chunk_id: int, questions: list[str], state: str, model: str, replace: bool) -> None:
        with self.db.transaction() as conn:
            if replace:
                conn.execute("DELETE FROM chunk_questions WHERE chunk_id = ?", (chunk_id,))
            for question in questions:
                conn.execute("INSERT INTO chunk_questions(chunk_id, question) VALUES (?, ?)", (chunk_id, question))
            conn.execute("INSERT OR REPLACE INTO chunk_question_state(chunk_id, state, model, generated_at) VALUES (?,?,?,?)",
                         (chunk_id, state, model, time.time()))

    def generate(self, document_id: int | None = None, collection_id: int | None = None, force: bool = False,
                 max_chunks: int | None = None, cancel: threading.Event | None = None) -> dict:
        """Write keys for the chunks that lack them (all of them with `force`), up to `max_chunks`. Never raises for a missing model:
        the pending chunks are marked `unavailable` and the answer says so."""
        started = time.time()
        cancel = cancel or threading.Event()
        generator = self.generator()
        limit = max_chunks if max_chunks and max_chunks > 0 else 10_000_000
        result = {"processed": 0, "generated": 0, "questions": 0, "empty": 0, "failed": 0, "model_available": True, "remaining": 0}
        with self._generating:
            try:
                reachable = bool(generator.available())
            except Exception:  # noqa: BLE001
                reachable = False
            todo = self._select(document_id, collection_id, force, limit)
            if not reachable:
                result["model_available"] = False
                for chunk_id, _ in todo:
                    if not force or not self.questions_for(chunk_id):
                        self._store(chunk_id, [], "unavailable", getattr(generator, "model", ""), replace=False)
                        result["failed"] += 1
                result["processed"] = len(todo)
            else:
                failures = 0
                model = getattr(generator, "model", "")
                for index, (chunk_id, text) in enumerate(todo):
                    if cancel.is_set():
                        break
                    if len(text.strip()) < MIN_CHUNK_CHARS:
                        self._store(chunk_id, [], "empty", model, replace=force)
                        result["empty"] += 1
                        result["processed"] += 1
                        continue
                    try:
                        questions = generator.generate(text, QUESTIONS_PER_CHUNK)
                        failures = 0
                    except Exception as error:  # noqa: BLE001
                        failures += 1
                        log.info("question model failed on chunk %s: %s", chunk_id, error)
                        if not force or not self.questions_for(chunk_id):
                            self._store(chunk_id, [], "unavailable", model, replace=False)
                        result["failed"] += 1
                        result["processed"] += 1
                        if failures >= MAX_CONSECUTIVE_FAILURES:  # the model went away: stop, mark the rest, retry on the next run
                            result["model_available"] = False
                            for rest_id, _ in todo[index + 1:]:
                                if not force or not self.questions_for(rest_id):
                                    self._store(rest_id, [], "unavailable", model, replace=False)
                                    result["failed"] += 1
                                result["processed"] += 1
                            break
                        continue
                    if questions:
                        self._store(chunk_id, questions, "ok", model, replace=True)
                        result["generated"] += 1
                        result["questions"] += len(questions)
                    else:
                        self._store(chunk_id, [], "empty", model, replace=force)
                        result["empty"] += 1
                    result["processed"] += 1
            result["remaining"] = len(self._select(document_id, collection_id, False, 10_000_000)) if not force else max(0, len(todo) - result["processed"])
        result["embedded"] = self.embed_pending()
        result["seconds"] = round(time.time() - started, 2)
        self._cache_dirty = True
        self.on_change()
        return result

    def run_pending(self, collection_id: int | None, cancel: threading.Event | None = None) -> dict | None:
        """Called by the indexers after embedding: a no-op unless the setting is on."""
        if not self.enabled:
            return None
        try:
            return self.generate(collection_id=collection_id, cancel=cancel)
        except Exception:  # noqa: BLE001 - keys must never fail an index run
            log.exception("question keys failed")
            return None

    # ---------- embeddings of the questions ----------
    def embed_pending(self) -> int:
        if not self.embedder.ensure_loaded():
            return 0
        model, done = self.embedder.model, 0
        while True:
            with self.db.lock:
                rows = self.db.conn.execute(
                    """SELECT q.id, q.question FROM chunk_questions q LEFT JOIN question_embeddings e ON e.question_id = q.id AND e.model = ?
                       WHERE e.question_id IS NULL ORDER BY q.id LIMIT ?""", (model, EMBED_BATCH)).fetchall()
            if not rows:
                break
            matrix = self.embedder.embed_documents([r["question"] for r in rows])
            dim = int(matrix.shape[1])
            payload = [(r["id"], model, dim, np.ascontiguousarray(matrix[i], dtype=np.float32).tobytes()) for i, r in enumerate(rows)]
            with self.db.transaction() as conn:
                conn.executemany("INSERT OR REPLACE INTO question_embeddings(question_id, model, dim, vector) VALUES (?,?,?,?)", payload)
            done += len(rows)
        if done:
            self._cache_dirty = True
            self.on_change()
        return done

    # ---------- search legs ----------
    def invalidate(self) -> None:
        self._cache_dirty = True

    def _dense_index(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with self._lock:
            if self._cache_dirty:
                with self.db.lock:
                    rows = self.db.conn.execute(
                        """SELECT q.id, q.chunk_id, e.dim, e.vector FROM question_embeddings e JOIN chunk_questions q ON q.id = e.question_id
                           WHERE e.model = ? ORDER BY q.id""", (self.embedder.model,)).fetchall()
                if rows:
                    dim = rows[0]["dim"]
                    self._qids = np.fromiter((r["id"] for r in rows), dtype=np.int64, count=len(rows))
                    self._chunk_of = np.fromiter((r["chunk_id"] for r in rows), dtype=np.int64, count=len(rows))
                    self._matrix = np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32).reshape(len(rows), dim)
                else:
                    self._qids = self._chunk_of = np.zeros(0, dtype=np.int64)
                    self._matrix = np.zeros((0, 0), dtype=np.float32)
                self._cache_dirty = False
            return self._qids, self._chunk_of, self._matrix

    def bm25(self, matches: list[str], limit: int, collection_ids: set[int] | None) -> tuple[list[tuple[int, float]], dict[int, str]]:
        """Best question per chunk by BM25. `matches` is the MATCH ladder of the query (first expression with rows wins)."""
        for match in matches:
            sql = """SELECT q.chunk_id, q.question, bm25(questions_fts) AS score FROM questions_fts
                     JOIN chunk_questions q ON q.id = questions_fts.rowid JOIN chunks c ON c.id = q.chunk_id JOIN documents d ON d.id = c.document_id
                     WHERE questions_fts MATCH ?"""
            params: list = [match]
            if collection_ids is not None:
                sql += f" AND d.collection_id IN ({','.join('?' for _ in collection_ids) or 'NULL'})"
                params += list(collection_ids)
            sql += " ORDER BY score LIMIT ?"
            params.append(limit * 3)
            with self.db.lock:
                try:
                    rows = self.db.conn.execute(sql, params).fetchall()
                except Exception:  # noqa: BLE001 - a malformed MATCH is "no rows"
                    continue
            if rows:
                return self._best_per_chunk([(r["chunk_id"], -float(r["score"]), r["question"]) for r in rows], limit)
        return [], {}

    def dense(self, query_vector: np.ndarray, limit: int, allowed_chunks: set[int] | None, min_score: float) -> tuple[list[tuple[int, float]], dict[int, str]]:
        from .hoard_link.docs import vecmath

        qids, chunk_of, matrix = self._dense_index()
        if len(qids) == 0:
            return [], {}
        rows = np.arange(len(qids))
        if allowed_chunks is not None:
            rows = np.flatnonzero(np.isin(chunk_of, np.fromiter(allowed_chunks, dtype=np.int64)))
            if len(rows) == 0:
                return [], {}
        searched = matrix if len(rows) == len(qids) else matrix[rows]
        top = vecmath.topk(searched, query_vector, limit * 3, min_score, normalized=True)
        triples = [(int(chunk_of[rows[i]]), float(score), int(qids[rows[i]])) for i, score in top]
        with self.db.lock:
            texts = {qid: (self.db.conn.execute("SELECT question FROM chunk_questions WHERE id = ?", (qid,)).fetchone() or {"question": ""})["question"]
                     for _, _, qid in triples}
        return self._best_per_chunk([(cid, score, texts[qid]) for cid, score, qid in triples], limit)

    @staticmethod
    def _best_per_chunk(triples: list[tuple[int, float, str]], limit: int) -> tuple[list[tuple[int, float]], dict[int, str]]:
        """Several questions of one chunk collapse to the chunk at its best question's rank."""
        ranking: list[tuple[int, float]] = []
        why: dict[int, str] = {}
        for chunk_id, score, question in triples:
            if chunk_id in why:
                continue
            why[chunk_id] = question
            ranking.append((chunk_id, score))
            if len(ranking) >= limit:
                break
        return ranking, why
