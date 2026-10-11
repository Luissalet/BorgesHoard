"""Hybrid search: FTS5 BM25 ∪ dense cosine → Reciprocal Rank Fusion, with highlighted snippets."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .db import Database
from .embedder import Embedder
from .hoard_link.docs import textsearch, vecmath
from .queries import Queries, citation
from .store import DocumentStore

RRF_K = 60
MIN_QUERY_CHARS = 3  # shorter queries are refused
PER_SECTION = 1  # hits shown per (document, page/section); the rest go into `also`
CANDIDATES = 40  # per retriever before fusion
DENSE_MIN_SCORE = 0.2  # cosine below this is noise for MiniLM-class models
SNIPPET_CHARS = 240
QUESTION_WEIGHT = 1.25  # a query that matches a question the chunk answers is a more precise signal than a word overlap with its text

Reranker = Callable[[str, list[dict]], list[dict]]  # hook: (query, hits) -> hits, reordered


def match_ladder(q: str) -> list[str]:
    """MATCH expressions to try in order until one returns rows: every content word (stopwords dropped, from 3 letters a
    prefix of its stem) AND'ed, then any of them. The words, the stemming and the quoting are the shared `textsearch`."""
    ladder: list[str] = []
    for mode in ("prefix", "or"):
        expr = textsearch.fts_query(q, mode=mode)
        if expr and expr not in ladder:
            ladder.append(expr)
    return ladder


def _meta(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def _in_date_range(date: str | None, since: str | None, until: str | None) -> bool:
    """A hit with no date (every non-chat document today) always passes; a dated hit must fall in range."""
    if not date:
        return True
    if since and date < since:
        return False
    if until and date > until:
        return False
    return True


class Search:
    def __init__(self, db: Database, docs: DocumentStore, queries: Queries, embedder: Embedder, collections=None, reranker: Reranker | None = None,
                 question_keys=None):
        self.question_keys = question_keys  # question_keys.QuestionKeys or None
        self.db = db
        self.docs = docs
        self.queries = queries
        self.embedder = embedder
        self.collections = collections  # CollectionStore; used to resolve `source` (a collection kind) to ids
        self.reranker = reranker
        self._lock = threading.Lock()
        self._ids = np.zeros(0, dtype=np.int64)
        self._matrix = np.zeros((0, 0), dtype=np.float32)
        self._dirty = True

    # ---------- dense index cache ----------
    def invalidate(self) -> None:
        self._dirty = True
        if self.question_keys is not None:
            self.question_keys.invalidate()

    def _dense_index(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if self._dirty:
                self._ids, self._matrix = self.docs.load_embeddings(self.embedder.model)
                self._dirty = False
            return self._ids, self._matrix

    # ---------- retrievers ----------
    # `collection_ids` restricts to a set of collections (a single filter, a source-kind filter, or both
    # intersected — see `_resolve_collection_ids`); None means the whole library.
    def bm25(self, q: str, limit: int, collection_ids: set[int] | None) -> list[tuple[int, float]]:
        for match in match_ladder(q):
            rows = self._fts(match, limit, collection_ids)
            if rows:
                return rows
        return []

    def _fts(self, match: str, limit: int, collection_ids: set[int] | None) -> list[tuple[int, float]]:
        sql = """SELECT c.id, bm25(chunks_fts, 1.0, 0.5) AS score FROM chunks_fts
                 JOIN chunks c ON c.id = chunks_fts.rowid JOIN documents d ON d.id = c.document_id
                 WHERE chunks_fts MATCH ?"""
        params: list = [match]
        if collection_ids is not None:
            marks = ",".join("?" for _ in collection_ids) or "NULL"
            sql += f" AND d.collection_id IN ({marks})"
            params += list(collection_ids)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        with self.db.lock:
            try:
                rows = self.db.conn.execute(sql, params).fetchall()
            except Exception:
                return []
        return [(r["id"], -float(r["score"])) for r in rows]

    def dense(self, q: str, limit: int, collection_ids: set[int] | None, vector: np.ndarray | None = None, exclude: int | None = None) -> list[tuple[int, float]]:
        if not self.embedder.ready():
            return []
        ids, matrix = self._dense_index()
        if len(ids) == 0:
            return []
        query = vector if vector is not None else self.embedder.embed_query(q)
        keep = np.ones(len(ids), dtype=bool)
        if collection_ids is not None:
            keep &= np.isin(ids, np.fromiter(self.queries.chunk_ids_for_collections(collection_ids), dtype=np.int64))
        if exclude is not None:
            keep &= ids != exclude
        rows = np.flatnonzero(keep)
        if len(rows) == 0:
            return []
        searched = matrix if len(rows) == len(ids) else matrix[rows]  # a filter makes a smaller matrix; no filter, no copy
        top = vecmath.topk(searched, query, limit, DENSE_MIN_SCORE, normalized=True)
        return [(int(ids[rows[i]]), float(score)) for i, score in top]

    def _resolve_collection_ids(self, collection_id: int | None, source: str | None) -> set[int] | None:
        """Combine a single-collection filter with a source-kind filter (`source='faustus'` / `'links'`)."""
        ids: set[int] | None = {collection_id} if collection_id is not None else None
        if source:
            by_kind = {c.id for c in self.collections.list() if c.kind == source} if self.collections else set()
            ids = by_kind if ids is None else (ids & by_kind)
        return ids

    # ---------- fusion ----------
    @staticmethod
    def rrf(rankings: list[list[tuple[int, float]]]) -> list[tuple[int, float]]:
        """Reciprocal rank fusion of rankings of `(chunk_id, score)` (the scores are not used, only the order)."""
        return vecmath.rrf([[chunk_id for chunk_id, _ in ranking] for ranking in rankings], k=RRF_K)

    def _question_legs(self, q: str, mode: str, dense_ok: bool, collection_ids: set[int] | None) -> tuple[list[list[tuple[int, float]]], dict[int, str]]:
        """Rankings of chunks by their generated question keys (BM25 and, with the model loaded, cosine) and, per chunk,
        the question that matched. Empty when no keys exist."""
        keys = self.question_keys
        if keys is None or not keys.has_keys():
            return [], {}
        legs: list[list[tuple[int, float]]] = []
        why: dict[int, str] = {}
        if mode in ("hybrid", "bm25"):
            ranking, texts = keys.bm25(match_ladder(q), CANDIDATES, collection_ids)
            if ranking:
                legs.append(ranking)
                why.update(texts)
        if mode in ("hybrid", "dense") and dense_ok:
            allowed = self.queries.chunk_ids_for_collections(collection_ids) if collection_ids is not None else None
            ranking, texts = keys.dense(self.embedder.embed_query(q), CANDIDATES, allowed, DENSE_MIN_SCORE)
            if ranking:
                legs.append(ranking)
                for chunk_id, text in texts.items():
                    why.setdefault(chunk_id, text)
        return legs, why

    def search(self, q: str, mode: str = "hybrid", limit: int = 10, collection_id: int | None = None,
               source: str | None = None, since: str | None = None, until: str | None = None, questions: bool = True) -> dict:
        q = q.strip()
        if len(q) < MIN_QUERY_CHARS:
            raise ValueError(f"The query needs at least {MIN_QUERY_CHARS} characters.")
        started = time.perf_counter()
        dense_ok = self.embedder.ready()
        if mode == "dense" and not dense_ok:
            mode = "bm25"
        collection_ids = self._resolve_collection_ids(collection_id, source)
        rankings: list[list[tuple[int, float]]] = []
        if mode in ("hybrid", "bm25"):
            rankings.append(self.bm25(q, CANDIDATES, collection_ids))
        if mode in ("hybrid", "dense") and dense_ok:
            rankings.append(self.dense(q, CANDIDATES, collection_ids))
        question_legs, matched = self._question_legs(q, mode, dense_ok, collection_ids) if questions else ([], {})
        if question_legs:  # the keys join the fusion as extra rankings; RRF keys on the chunk id, so a chunk found by its text and by its questions is one hit
            fused = vecmath.rrf([[cid for cid, _ in r] for r in [*rankings, *question_legs]],
                                weights=[1.0] * len(rankings) + [QUESTION_WEIGHT] * len(question_legs), k=RRF_K)
        else:
            fused = self.rrf(rankings) if mode == "hybrid" else [(cid, s) for cid, s in (rankings[0] if rankings else [])]
        hits = self._hydrate(fused[: limit * 4 if (since or until) else limit * 2], q)
        for hit in hits:
            if hit["chunk_id"] in matched:
                hit["matched_question"] = matched[hit["chunk_id"]]
        hits = self._dedupe(hits)
        if since or until:
            hits = [h for h in hits if _in_date_range(h.get("date"), since, until)]
        hits = hits[:limit]
        if self.reranker is not None:
            hits = self.reranker(q, hits)
        return {"query": q, "mode": mode, "hits": hits, "dense_available": dense_ok, "took_ms": round((time.perf_counter() - started) * 1000, 1)}

    def similar(self, chunk_id: int, limit: int = 10) -> list[dict]:
        chunk = self.queries.chunk(chunk_id)
        if chunk is None:
            raise LookupError(f"Chunk {chunk_id} does not exist.")
        vector = self.docs.embedding_for(chunk_id, self.embedder.model) if self.embedder.ready() else None
        if vector is not None:
            ranking = self.dense("", limit * 2, None, vector=vector, exclude=chunk_id)
            terms = textsearch.query_terms(chunk["text"][:200])
        else:
            words = " ".join(chunk["text"].split()[:12])
            ranking = [(cid, s) for cid, s in self.bm25(words, limit * 2, None) if cid != chunk_id]
            terms = textsearch.query_terms(words)
        hits = self._hydrate(ranking, " ".join(terms[:8]))
        return self._dedupe(hits)[:limit]

    # ---------- shaping ----------
    def _hydrate(self, ranking: list[tuple[int, float]], q: str) -> list[dict]:
        rows = self.queries.chunks_by_ids([cid for cid, _ in ranking])
        terms = textsearch.query_terms(q)
        hits = []
        for chunk_id, score in ranking:
            row = rows.get(chunk_id)
            if not row:
                continue
            meta = _meta(row["doc_meta"]) if row["kind"] in ("chat", "link") else {}
            doc = {"title": row["title"], "kind": row["kind"], "filename": Path(row["rel_path"]).name, "meta": meta}
            hits.append({
                "chunk_id": chunk_id, "document_id": row["document_id"], "unit_id": row["unit_id"], "collection_id": row["collection_id"],
                "collection": row["collection"], "source_kind": row["source_kind"], "title": row["title"], "kind": row["kind"], "rel_path": row["rel_path"],
                "path": str(Path(row["collection_path"]) / row["rel_path"]), "page": row["page"], "section": row["section"], "line": row["line"],
                "date": meta.get("date"), "project": meta.get("project") or None, "url": meta.get("url") or None,
                "score": round(float(score), 6), "citation": citation(doc, row["page"], row["section"], row["line"]),
                "snippet": textsearch.highlight(row["text"], terms, window=SNIPPET_CHARS),
            })
        return hits

    @staticmethod
    def _dedupe(hits: list[dict], per_section: int = PER_SECTION) -> list[dict]:
        """One hit per (document, page/section); further hits from the same section ride in `also` (up to 3)."""
        first: dict[tuple[int, int], dict] = {}
        out = []
        for hit in hits:
            key = (hit["document_id"], hit["unit_id"])
            head = first.get(key)
            if head is None:
                hit["also"] = []
                hit["more"] = 0
                first[key] = hit
                out.append(hit)
            elif len(head["also"]) < 3 and per_section == 1:
                head["also"].append({k: hit[k] for k in ("chunk_id", "snippet", "score", "line")})
                head["more"] += 1
            else:
                head["more"] += 1
        return out
