"""Real embedding model (downloaded on first use). Run with `pytest -m model`.

Set BORGES_MODELS_DIR to reuse an existing download; otherwise the model goes to a temp folder.
"""

import os
import time
from pathlib import Path

import pytest

from borges.config import DEFAULT_MODEL, Config
from borges.services import Services
from fixtures import make_library

pytestmark = pytest.mark.model


def test_spanish_query_finds_spanish_passage(tmp_path):
    models_dir = Path(os.environ.get("BORGES_MODELS_DIR") or tmp_path / "models")
    library = tmp_path / "library"
    make_library(library)
    config = Config(data_dir=tmp_path / "data", embed_backend="auto", models_dir=models_dir, watch=False, autostart=False, data_dir_configured=True)
    services = Services(config)
    services.worker.start()
    try:
        started = time.time()
        assert services.embedder.ensure_loaded(), services.embedder.status()
        status = services.embedder.status()
        assert status["state"] == "ready" and status["dim"] == 384 and status["model"] == DEFAULT_MODEL
        print(f"\nmodel {status['model']} loaded in {time.time() - started:.1f}s, dim {status['dim']}")

        services.add_collection("Real", str(library), [], None, False, False)
        assert services.worker.wait_idle(300)
        assert services.documents.count_without_embedding(services.embedder.model) == 0

        # paraphrased Spanish query, no shared keyword with the passage except "hoja"/"árbol" stems
        result = services.search.search("¿quién recordaba todas las hojas de los árboles?", "dense", 3)
        assert result["mode"] == "dense" and result["hits"]
        assert "Funes" in result["hits"][0]["snippet"] or "hoja" in result["hits"][0]["snippet"]

        # a pure meaning query with no keyword overlap at all
        result = services.search.search("presupuesto de mantenimiento del observatorio", "dense", 3)
        assert result["hits"][0]["rel_path"] == "sub/budget.txt"  # cross-lingual: the passage is in English

        hybrid = services.search.search("tiempos que divergen y convergen", "hybrid", 3)
        assert any("bifurcan" in h["snippet"] or "divergentes" in h["snippet"] for h in hybrid["hits"])

        # throughput on this CPU
        texts = [f"Párrafo {i} sobre la biblioteca infinita de Valdeniebla y los senderos que se bifurcan en el tiempo. " * 6 for i in range(128)]
        started = time.time()
        matrix = services.embedder.embed_documents(texts)
        elapsed = time.time() - started
        print(f"embedded {len(texts)} chunks of ~{len(texts[0])} chars in {elapsed:.2f}s → {len(texts) / elapsed:.1f} chunks/s")
        assert matrix.shape == (128, 384)
    finally:
        services.stop()
