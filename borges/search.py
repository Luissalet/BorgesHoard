"""Hybrid search: FTS5 BM25 ∪ dense cosine → Reciprocal Rank Fusion, with highlighted snippets."""

from __future__ import annotations

import re
import threading
import unicodedata
from pathlib import Path
from typing import Callable

import numpy as np

from .db import Database
from .embedder import Embedder
from .queries import Queries, citation
from .store import DocumentStore

RRF_K = 60
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
    """(AND query, OR query) for FTS5 from free text; stopwords dropped, words of 4+ letters match as prefixes of their stem."""
    words = content_words(q)
    if not words:
        return "", ""
    quoted = [f'"{stem(w)}"*' if len(w) >= 4 else f'"{w}"' for w in words]
    return " AND ".join(quoted), " OR ".join(quoted)


def query_terms(q: str) -> list[str]:
    """Folded stems used to highlight matches at word starts."""
    return [fold(stem(w)) if len(w) >= 4 else fold(w) for w in content_words(q)]


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


class Search:
    def __init__(self, db: Database, docs: DocumentStore, queries: Queries, embedder: Embedder, reranker: Reranker | None = None):
        self.db = db
        self.docs = docs
        self.queries = queries
        self.embedder = embedder
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
    def bm25(self, q: str, limit: int, collection_id: int | None) -> list[tuple[int, float]]:
        and_q, or_q = fts_query(q)
        if not and_q:
            return []
        for match in (and_q, or_q):
            rows = self._fts(match, limit, collection_id)
            if rows:
                return rows
        return []

    def _fts(self, match: str, limit: int, collection_id: int | None) -> list[tuple[int, float]]:
        sql = """SELECT c.id, bm25(chunks_fts, 1.0, 0.5) AS score FROM chunks_fts
                 JOIN chunks c ON c.id = chunks_fts.rowid JOIN documents d ON d.id = c.document_id
                 WHERE chunks_fts MATCH ?"""
        params: list = [match]
        if collection_id is not None:
            sql += " AND d.collection_id = ?"
            params.append(collection_id)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        with self.db.lock:
            try:
                rows = self.db.conn.execute(sql, params).fetchall()
            except Exception:
                return []
        return [(r["id"], -float(r["score"])) for r in rows]

    def dense(self, q: str, limit: int, collection_id: int | None, vector: np.ndarray | None = None, exclude: int | None = None) -> list[tuple[int, float]]:
        if not self.embedder.ready():
            return []
        ids, matrix = self._dense_index()
        if len(ids) == 0:
            return []
        query = vector if vector is not None else self.embedder.embed_query(q)
        scores = matrix @ query.astype(np.float32)
        if collection_id is not None:
            allowed = self.queries.collection_chunk_ids(collection_id)
            mask = np.fromiter((int(i) in allowed for i in ids), dtype=bool, count=len(ids))
            scores = np.where(mask, scores, -np.inf)
        if exclude is not None:
            scores = np.where(ids == exclude, -np.inf, scores)
        k = min(limit, len(ids))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(ids[i]), float(scores[i])) for i in top if np.isfinite(scores[i]) and scores[i] >= DENSE_MIN_SCORE]

    # ---------- fusion ----------
    @staticmethod
    def rrf(rankings: list[list[tuple[int, float]]]) -> list[tuple[int, float]]:
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, (chunk_id, _score) in enumerate(ranking, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        return sorted(fused.items(), key=lambda item: -item[1])

    def search(self, q: str, mode: str = "hybrid", limit: int = 10, collection_id: int | None = None) -> dict:
        q = q.strip()
        if not q:
            return {"query": q, "mode": mode, "hits": [], "dense_available": self.embedder.ready()}
        dense_ok = self.embedder.ready()
        if mode == "dense" and not dense_ok:
            mode = "bm25"
        rankings: list[list[tuple[int, float]]] = []
        if mode in ("hybrid", "bm25"):
            rankings.append(self.bm25(q, CANDIDATES, collection_id))
        if mode in ("hybrid", "dense") and dense_ok:
            rankings.append(self.dense(q, CANDIDATES, collection_id))
        fused = self.rrf(rankings) if mode == "hybrid" else [(cid, s) for cid, s in (rankings[0] if rankings else [])]
        hits = self._hydrate(fused[: limit * 2], q)
        hits = self._dedupe(hits)[:limit]
        if self.reranker is not None:
            hits = self.reranker(q, hits)
        return {"query": q, "mode": mode, "hits": hits, "dense_available": dense_ok}

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
            doc = {"title": row["title"], "kind": row["kind"], "filename": Path(row["rel_path"]).name}
            hits.append({
                "chunk_id": chunk_id, "document_id": row["document_id"], "unit_id": row["unit_id"], "collection_id": row["collection_id"],
                "collection": row["collection"], "title": row["title"], "kind": row["kind"], "rel_path": row["rel_path"],
                "path": str(Path(row["collection_path"]) / row["rel_path"]), "page": row["page"], "section": row["section"], "line": row["line"],
                "score": round(float(score), 6), "citation": citation(doc, row["page"], row["section"], row["line"]),
                "snippet": highlight(row["text"], terms),
            })
        return hits

    @staticmethod
    def _dedupe(hits: list[dict]) -> list[dict]:
        """Keep at most two hits per (document, unit) so one long page cannot fill the list."""
        seen: dict[tuple[int, int], int] = {}
        out = []
        for hit in hits:
            key = (hit["document_id"], hit["unit_id"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] <= 2:
                out.append(hit)
        return out
