"""Hybrid search: FTS5 BM25 ∪ dense cosine → Reciprocal Rank Fusion, with highlighted snippets."""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable

import numpy as np

from .db import Database
from .embedder import Embedder
from .queries import Queries, citation
from .store import DocumentStore

RRF_K = 60
MIN_QUERY_CHARS = 3  # shorter queries are refused
PREFIX_MIN_CHARS = 3  # terms shorter than this match whole words only (no prefix expansion)
PER_SECTION = 1  # hits shown per (document, page/section); the rest go into `also`
CANDIDATES = 40  # per retriever before fusion
DENSE_MIN_SCORE = 0.2  # cosine below this is noise for MiniLM-class models
SNIPPET_CHARS = 240
_WORD = re.compile(r"\w+", re.UNICODE)

Reranker = Callable[[str, list[dict]], list[dict]]  # hook: (query, hits) -> hits, reordered


def fold(text: str) -> str:
    """Lowercase and strip diacritics keeping the same length (one char per char) so offsets map back."""
    out = []
    for ch in text.lower():
        decomposed = unicodedata.normalize("NFD", ch)
        out.append(decomposed[0] if decomposed and unicodedata.category(decomposed[0]) != "Mn" else ch)
    return "".join(out)


STOPWORDS = frozenset(
    "de la el los las un una unos unas y o u e en del al a que con por para se su sus lo le les es son no ni como más muy ya "
    "mi mis tu tus me te sobre acerca dónde donde qué cuál cuáles cómo cuándo quién hay he ha leí escribí dice dijo del este esta esto "
    "the of an and or in on to is are was were for with that this about where what which how when who did i my".split()
)


def stem(word: str) -> str:
    """Very light Spanish-friendly stem used only as an FTS prefix: hojas→hoja, árboles→árbol, ciudades→ciudad."""
    w = word.lower()
    if len(w) > 5 and w.endswith("es"):
        return w[:-2]
    if len(w) > 4 and w.endswith("s"):
        return w[:-1]
    return w


def content_words(q: str) -> list[str]:
    words = [w for w in _WORD.findall(q) if w.strip("_")]
    kept = [w for w in words if fold(w) not in STOPWORDS and len(w) > 1]
    return kept or words


def fts_query(q: str) -> tuple[str, str]:
    """(AND query, OR query) for FTS5; stopwords dropped, terms of PREFIX_MIN_CHARS+ letters match as prefixes of their stem."""
    words = content_words(q)
    if not words:
        return "", ""
    quoted = [f'"{stem(w)}"*' if len(w) >= PREFIX_MIN_CHARS else f'"{w}"' for w in words]
    return " AND ".join(quoted), " OR ".join(quoted)


def query_terms(q: str) -> list[str]:
    """Folded stems used to highlight matches at word starts."""
    return [fold(stem(w)) if len(w) >= PREFIX_MIN_CHARS else fold(w) for w in content_words(q)]


def highlight(text: str, terms: list[str], window: int = SNIPPET_CHARS) -> str:
    """Return an excerpt centred on the first term match, with matches wrapped in <mark>."""
    folded = fold(text)
    positions: list[tuple[int, int]] = []
    for term in terms:
        for match in re.finditer(re.escape(term), folded):
            start = match.start()
            if start > 0 and folded[start - 1].isalnum():
                continue  # only word starts
            end = match.end()
            while end < len(folded) and folded[end].isalnum():
                end += 1  # mark the whole word, not just the stem
            positions.append((start, end))
    positions.sort()
    if positions:
        first = positions[0][0]
        start = max(0, first - window // 3)
    else:
        start = 0
    end = min(len(text), start + window)
    # snap to word boundaries
    if start > 0:
        space = text.rfind(" ", max(0, start - 20), start)
        start = space + 1 if space != -1 else (0 if start < 20 else start)
    if end < len(text):
        space = text.find(" ", end, min(len(text), end + 20))
        end = space if space != -1 else end
    pieces: list[str] = []
    cursor = start
    for s, e in positions:
        if s < start or e > end or s < cursor:
            continue
        pieces.append(_esc(text[cursor:s]))
        pieces.append(f"<mark>{_esc(text[s:e])}</mark>")
        cursor = e
    pieces.append(_esc(text[cursor:end]))
    excerpt = "".join(pieces).replace("\n", " ")
    return ("…" if start > 0 else "") + excerpt + ("…" if end < len(text) else "")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    def __init__(self, db: Database, docs: DocumentStore, queries: Queries, embedder: Embedder, collections=None, reranker: Reranker | None = None):
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
        and_q, or_q = fts_query(q)
        if not and_q:
            return []
        for match in (and_q, or_q):
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
        scores = matrix @ query.astype(np.float32)
        if collection_ids is not None:
            allowed = self.queries.chunk_ids_for_collections(collection_ids)
            mask = np.fromiter((int(i) in allowed for i in ids), dtype=bool, count=len(ids))
            scores = np.where(mask, scores, -np.inf)
        if exclude is not None:
            scores = np.where(ids == exclude, -np.inf, scores)
        k = min(limit, len(ids))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(ids[i]), float(scores[i])) for i in top if np.isfinite(scores[i]) and scores[i] >= DENSE_MIN_SCORE]

    def _resolve_collection_ids(self, collection_id: int | None, source: str | None) -> set[int] | None:
        """Combine a single-collection filter with a source-kind filter (`source='faustus'`)."""
        ids: set[int] | None = {collection_id} if collection_id is not None else None
        if source:
            by_kind = {c.id for c in self.collections.list() if c.kind == source} if self.collections else set()
            ids = by_kind if ids is None else (ids & by_kind)
        return ids

    # ---------- fusion ----------
    @staticmethod
    def rrf(rankings: list[list[tuple[int, float]]]) -> list[tuple[int, float]]:
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, (chunk_id, _score) in enumerate(ranking, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        return sorted(fused.items(), key=lambda item: -item[1])

    def search(self, q: str, mode: str = "hybrid", limit: int = 10, collection_id: int | None = None,
               source: str | None = None, since: str | None = None, until: str | None = None) -> dict:
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
        fused = self.rrf(rankings) if mode == "hybrid" else [(cid, s) for cid, s in (rankings[0] if rankings else [])]
        hits = self._hydrate(fused[: limit * 4 if (since or until) else limit * 2], q)
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
            terms = query_terms(chunk["text"][:200])
        else:
            words = " ".join(chunk["text"].split()[:12])
            ranking = [(cid, s) for cid, s in self.bm25(words, limit * 2, None) if cid != chunk_id]
            terms = query_terms(words)
        hits = self._hydrate(ranking, " ".join(terms[:8]))
        return self._dedupe(hits)[:limit]

    # ---------- shaping ----------
    def _hydrate(self, ranking: list[tuple[int, float]], q: str) -> list[dict]:
        rows = self.queries.chunks_by_ids([cid for cid, _ in ranking])
        terms = query_terms(q)
        hits = []
        for chunk_id, score in ranking:
            row = rows.get(chunk_id)
            if not row:
                continue
            meta = _meta(row["doc_meta"]) if row["kind"] == "chat" else {}
            doc = {"title": row["title"], "kind": row["kind"], "filename": Path(row["rel_path"]).name, "meta": meta}
            hits.append({
                "chunk_id": chunk_id, "document_id": row["document_id"], "unit_id": row["unit_id"], "collection_id": row["collection_id"],
                "collection": row["collection"], "source_kind": row["source_kind"], "title": row["title"], "kind": row["kind"], "rel_path": row["rel_path"],
                "path": str(Path(row["collection_path"]) / row["rel_path"]), "page": row["page"], "section": row["section"], "line": row["line"],
                "date": meta.get("date"), "project": meta.get("project") or None,
                "score": round(float(score), 6), "citation": citation(doc, row["page"], row["section"], row["line"]),
                "snippet": highlight(row["text"], terms),
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
