"""The family embedding service: `embed_texts` and `embed_status` (docs/commons/services.md section 6), through the agent routes the
hub and the MCP bridge use."""

import numpy as np
import pytest
from conftest import make_config
from fastapi.testclient import TestClient

from borges import agent_tools
from borges.embedder import Embedder
from borges.main import create_app


def call(client, name, arguments=None):
    return client.post("/api/agent/call", json={"name": name, "arguments": arguments or {}}, headers={"Authorization": f"Bearer {client.app.state.services.token}"})


def test_embed_texts_returns_one_unit_vector_per_text_in_order(client):
    texts = ["la aldea de Valdeniebla", "quarterly maintenance budget", "la aldea de Valdeniebla"]
    body = call(client, "embed_texts", {"texts": texts}).json()
    assert body["model"] == "fake-hash" and body["dim"] == 32 and body["normalized"] is True
    matrix = np.array(body["vectors"])
    assert matrix.shape == (3, 32) and np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)
    assert np.allclose(matrix[0], matrix[2]) and not np.allclose(matrix[0], matrix[1])  # same text, same vector; the order is the input's
    assert np.allclose(matrix, client.services.embedder.embed_documents(texts), atol=1e-6)


def test_the_query_side_uses_embed_query(client, monkeypatch):
    seen = []
    embedder = client.services.embedder
    original = embedder.embed_query
    monkeypatch.setattr(embedder, "embed_query", lambda t: seen.append(t) or original(t))
    body = call(client, "embed_texts", {"texts": ["uno", "dos"], "kind": "query"}).json()
    assert seen == ["uno", "dos"] and len(body["vectors"]) == 2
    assert call(client, "embed_texts", {"texts": ["uno"], "kind": "sideways"}).status_code == 400


def test_the_answer_is_not_cut_to_the_assistants_size_budget(client):
    body = call(client, "embed_texts", {"texts": [f"texto número {n}" for n in range(512)]}).json()
    assert len(body["vectors"]) == 512 and "truncated" not in body
    assert len(str(body)) > 20_000  # the other tools' answers are capped at about 20 KB; these are for programs


def test_long_texts_are_cut_and_the_limits_are_reported_with_codes(client):
    assert call(client, "embed_texts", {"texts": ["palabra " * 5000]}).status_code == 200
    too_many = call(client, "embed_texts", {"texts": ["x"] * 513})
    assert too_many.status_code == 400 and too_many.json()["code"] == "too_many_texts"
    empty = call(client, "embed_texts", {"texts": []})
    assert empty.status_code == 400 and empty.json()["code"] == "invalid_arguments"
    assert call(client, "embed_texts", {}).status_code == 400


def test_embeddings_disabled_is_a_400_and_status_says_disabled(tmp_path):
    with TestClient(create_app(make_config(tmp_path, embed_backend="none")), base_url="http://127.0.0.1") as client:
        refused = call(client, "embed_texts", {"texts": ["hola"]})
        assert refused.status_code == 400 and refused.json()["code"] == "embeddings_disabled"
        status = call(client, "embed_status").json()
        assert status["state"] == "disabled" and status["ready"] is False and status["backend"] == "none"


class SlowEmbedder(Embedder):
    name, model, dim = "slow", "slow-model", 4

    def __init__(self, fail=False):
        self.fail, self.loaded = fail, False

    def ready(self):
        return self.loaded

    def ensure_loaded(self):
        import time

        time.sleep(0.6)
        self.loaded = not self.fail
        return self.loaded

    def embed_documents(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32) / 2

    def embed_query(self, text):
        return np.ones(4, dtype=np.float32) / 2

    def status(self):
        return {"backend": "slow", "model": self.model, "state": "error" if self.fail else ("ready" if self.loaded else "downloading"),
                "dim": 4, "error": "no network" if self.fail else None, "load_seconds": None}


def test_a_loading_model_answers_503_after_the_wait_and_works_once_loaded(client, monkeypatch):
    client.services.embedder = SlowEmbedder()
    monkeypatch.setattr(agent_tools, "EMBED_WAIT_S", 0.1)
    waiting = call(client, "embed_texts", {"texts": ["hola"]})
    assert waiting.status_code == 503 and waiting.json()["code"] == "embedder_loading"
    status = call(client, "embed_status").json()
    assert status["state"] in ("downloading", "ready") and status["backend"] == "slow"
    monkeypatch.setattr(agent_tools, "EMBED_WAIT_S", 5.0)
    done = call(client, "embed_texts", {"texts": ["hola"]})
    assert done.status_code == 200 and done.json()["model"] == "slow-model" and done.json()["dim"] == 4


def test_a_model_that_cannot_load_answers_503_with_the_reason(client, monkeypatch):
    client.services.embedder = SlowEmbedder(fail=True)
    monkeypatch.setattr(agent_tools, "EMBED_WAIT_S", 5.0)
    failed = call(client, "embed_texts", {"texts": ["hola"]})
    assert failed.status_code == 503 and failed.json()["code"] == "embedder_error" and "no network" in failed.json()["error"]


def test_embed_status_shape_for_the_fake_and_the_unloaded_real_model(client, tmp_path):
    fake = call(client, "embed_status").json()
    assert fake["backend"] == "fake" and fake["model"] == "fake-hash" and fake["dim"] == 32 and fake["state"] == "ready" and fake["ready"] is True
    assert {"backend", "model", "dim", "state", "error", "load_seconds", "ready"} <= set(fake)
    with TestClient(create_app(make_config(tmp_path / "b", embed_backend="auto", models_dir=tmp_path / "models")), base_url="http://127.0.0.1") as real:
        status = call(real, "embed_status").json()
        assert status["backend"] == "fastembed" and status["state"] in ("loading", "downloading", "error", "ready") and status["ready"] in (True, False)


def test_the_tools_are_in_the_catalogue_for_the_hub_and_the_bridge(client):
    tools = {t["name"]: t for t in client.get("/api/agent/tools").json()["tools"]}
    for name in ("embed_texts", "embed_status"):
        assert tools[name]["annotations"]["readOnlyHint"] is True and "Sinónimos:" in tools[name]["description"]
    assert tools["embed_texts"]["inputSchema"]["required"] == ["texts"]
    assert "kind" in tools["embed_texts"]["inputSchema"]["properties"] and "normalize" in tools["embed_texts"]["inputSchema"]["properties"]
