"""Dense embeddings behind a small interface: fastembed (CPU, downloaded on first use), fake (tests), none."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("borges.embed")

FAKE_DIM = 32


class Embedder:
    """Base: subclasses embed batches of texts into unit-length float32 vectors."""

    name = "none"
    model = ""
    dim = 0

    def ready(self) -> bool:
        return False

    def ensure_loaded(self) -> bool:
        return False

    def embed_documents(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    def status(self) -> dict:
        return {"backend": self.name, "model": self.model, "state": "disabled", "dim": self.dim, "error": None, "load_seconds": None}


class NoneEmbedder(Embedder):
    """Dense search disabled: only BM25 works."""


class FakeEmbedder(Embedder):
    """Deterministic bag-of-words hashing so tests can check fusion without a model."""

    name = "fake"
    model = "fake-hash"
    dim = FAKE_DIM

    def ready(self) -> bool:
        return True

    def ensure_loaded(self) -> bool:
        return True

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(FAKE_DIM, dtype=np.float32)
        for word in text.lower().split():
            digest = hashlib.md5(word.encode("utf-8")).digest()
            vec[digest[0] % FAKE_DIM] += 1.0
            vec[digest[1] % FAKE_DIM] += 0.5
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vector(t) for t in texts]) if texts else np.zeros((0, FAKE_DIM), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)

    def status(self) -> dict:
        return {**super().status(), "state": "ready"}


class FastembedEmbedder(Embedder):
    """ONNX model through fastembed; loaded lazily (and downloaded on first use into `cache_dir`)."""

    name = "fastembed"

    def __init__(self, model: str, cache_dir: Path, providers: tuple[str, ...] = ()):
        self.model = model
        self.cache_dir = cache_dir
        self.providers = providers
        self._model = None
        self._lock = threading.Lock()
        self._state = "not_loaded"
        self._error: str | None = None
        self._load_seconds: float | None = None
        self.dim = 0

    def ready(self) -> bool:
        return self._model is not None

    def _cached(self) -> bool:
        return any(self.cache_dir.glob("models--*/blobs/*")) or any(self.cache_dir.glob("**/model*.onnx"))

    def ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            if self._state == "error":
                return False
            self._state = "loading" if self._cached() else "downloading"
            started = time.time()
            try:
                from fastembed import TextEmbedding

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                kwargs = {"cache_dir": str(self.cache_dir)}
                if self.providers:
                    kwargs["providers"] = list(self.providers)
                model = TextEmbedding(self.model, **kwargs)
                probe = next(iter(model.embed(["hola"])))
                self.dim = int(len(probe))
                self._model = model
                self._state = "ready"
                self._load_seconds = round(time.time() - started, 1)
                log.info("embedding model %s ready (%d dims) in %.1fs", self.model, self.dim, self._load_seconds)
                return True
            except Exception as error:  # download failure, missing wheel, etc.
                self._state = "error"
                self._error = f"{type(error).__name__}: {error}"
                log.warning("embedding model unavailable: %s", self._error)
                return False

    def _as_matrix(self, vectors) -> np.ndarray:
        matrix = np.asarray(list(vectors), dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        if not self.ensure_loaded():
            raise RuntimeError(self._error or "embedding model not loaded")
        return self._as_matrix(self._model.embed(texts, batch_size=32))

    def embed_query(self, text: str) -> np.ndarray:
        if not self.ensure_loaded():
            raise RuntimeError(self._error or "embedding model not loaded")
        return self._as_matrix(self._model.query_embed(text))[0]

    def status(self) -> dict:
        return {"backend": self.name, "model": self.model, "state": self._state, "dim": self.dim, "error": self._error,
                "load_seconds": self._load_seconds, "cache_dir": str(self.cache_dir)}


def make_embedder(backend: str, model: str, cache_dir: Path, providers: tuple[str, ...] = ()) -> Embedder:
    if backend == "fake":
        return FakeEmbedder()
    if backend == "none":
        return NoneEmbedder()
    return FastembedEmbedder(model, cache_dir, providers)
