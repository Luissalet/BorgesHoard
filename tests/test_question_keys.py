"""Question keys: generation with a scripted model, retrieval by rank fusion, model down, API and MCP tools, offline eval."""

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_question_keys as ev  # noqa: E402
from borges.question_keys import GeneratorUnavailable, LocalModelGenerator, parse_questions  # noqa: E402
from borges.services import Services  # noqa: E402
from conftest import make_config  # noqa: E402


@pytest.fixture
def library_with_questions(tmp_path):
    folder = tmp_path / "corpus"
    ev.build_library(folder)
    return folder


@pytest.fixture
def qk_services(tmp_path, library_with_questions):
    svc = Services(make_config(tmp_path))
    svc.worker.start()
    yield svc, library_with_questions
    svc.stop()


def index(svc, folder):
    collection = svc.add_collection("corpus", str(folder), [], None, False, False)
    assert svc.worker.wait_idle(60)
    return collection


def doc_id(svc, name):
    return next(d["id"] for d in svc.queries.list_documents(None, "", 100, None)["documents"] if d["rel_path"] == name)


# ---------- parsing and the model client ----------
def test_parse_questions_cleans_a_model_answer():
    raw = "1. ¿Cuánto cuesta reparar un espejo?\n- ¿Cuánto cuesta reparar un espejo?\n* Qué presupuesto hay?\nEsto no es una pregunta\n3) ¿Y?\n\"¿Dónde se guardan las cajas de madera?\""
    assert parse_questions(raw, 3) == ["¿Cuánto cuesta reparar un espejo?", "Qué presupuesto hay?", "¿Dónde se guardan las cajas de madera?"]
    assert parse_questions("", 3) == [] and parse_questions("sin signos", 3) == []


def test_local_model_generator_talks_to_an_openai_compatible_endpoint(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None, trust_env=None):
        seen.update(url=url, body=json, headers=headers)
        content = "<think>pensando</think>1. ¿Qué abre a las nueve?\n2. ¿Cuándo cierra el archivo?"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url)))
    generator = LocalModelGenerator("http://127.0.0.1:9/v1/", "qwen-mini", api_key="k")
    assert generator.available()
    assert generator.generate("texto del fragmento " * 10) == ["¿Qué abre a las nueve?", "¿Cuándo cierra el archivo?"]
    assert seen["url"] == "http://127.0.0.1:9/v1/chat/completions" and seen["body"]["model"] == "qwen-mini" and seen["headers"] == {"Authorization": "Bearer k"}
    assert not LocalModelGenerator("http://127.0.0.1:9/v1", "").available()  # no model chosen: nothing to ask


def test_local_model_generator_reports_an_unreachable_model(monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)
    generator = LocalModelGenerator("http://127.0.0.1:9/v1", "qwen-mini")
    assert generator.available() is False
    with pytest.raises(GeneratorUnavailable):
        generator.generate("texto " * 30)


# ---------- settings ----------
def test_off_by_default_and_indexing_adds_no_keys(qk_services):
    svc, folder = qk_services
    generator = ev.ScriptedGenerator()
    svc.question_keys.generator_override = generator
    assert svc.question_keys.settings()["enabled"] is False
    index(svc, folder)
    assert generator.calls == 0
    status = svc.question_keys.status()
    assert status["keys_exist"] is False and status["chunks_with_keys"] == 0 and status["chunks_pending"] == status["chunks"] > 0
    assert svc.status()["question_keys"]["enabled"] is False


def test_settings_roundtrip_hides_the_api_key(qk_services):
    svc, _ = qk_services
    cfg = svc.question_keys.update_settings({"enabled": True, "base_url": "http://127.0.0.1:1234/v1", "model": "m", "api_key": "secret"})
    assert cfg == {"enabled": True, "base_url": "http://127.0.0.1:1234/v1", "model": "m", "api_key": "********"}
    assert svc.question_keys.settings(redact=False)["api_key"] == "secret"
    assert svc.question_keys.update_settings({"enabled": False})["enabled"] is False


# ---------- indexing with the setting on ----------
def test_indexing_with_the_setting_on_generates_embeds_and_finds(qk_services):
    svc, folder = qk_services
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    svc.question_keys.update_settings({"enabled": True})
    index(svc, folder)
    status = svc.question_keys.status()
    assert status["chunks_with_keys"] == len(ev.CORPUS) and status["chunks_without_keys"] == 0
    assert status["questions"] == sum(len(q) for _, _, q in ev.CORPUS) and status["questions_pending_embedding"] == 0
    query, target = ev.QUERIES[0]
    plain = svc.search.search(query, "hybrid", 5, questions=False)["hits"]
    keyed = svc.search.search(query, "hybrid", 5)["hits"]
    assert Path(plain[0]["rel_path"]).name != target
    assert Path(keyed[0]["rel_path"]).name == target and keyed[0]["matched_question"] == ev.CORPUS[0][2][0]
    assert len({h["chunk_id"] for h in keyed}) == len(keyed)  # deduplicated to the chunk
    assert all("matched_question" not in h for h in plain)


def test_a_chunk_found_by_text_and_by_questions_is_one_hit(qk_services):
    svc, folder = qk_services
    index(svc, folder)
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    svc.question_keys.generate(collection_id=1)
    for mode in ("hybrid", "bm25", "dense"):
        hits = svc.search.search("espejos del telescopio", mode, 10)["hits"]
        assert len({h["chunk_id"] for h in hits}) == len(hits), mode


def test_bm25_and_dense_modes_use_the_keys_too(qk_services):
    svc, folder = qk_services
    index(svc, folder)
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    svc.question_keys.generate(collection_id=1)
    query, target = ev.QUERIES[2]
    assert Path(svc.search.search(query, "bm25", 3)["hits"][0]["rel_path"]).name == target
    # the hashing fake embedder is too coarse to promise rank 1 in dense mode, but the question leg must contribute and be reported
    dense = {Path(h["rel_path"]).name: h for h in svc.search.search(query, "dense", 5)["hits"]}
    assert dense[target]["matched_question"] == "Se puede comer dentro de la sala de lectura?"
    assert "matched_question" not in {Path(h["rel_path"]).name: h for h in svc.search.search(query, "dense", 5, questions=False)["hits"]}.get(target, {})


def test_search_scoped_to_a_collection_keeps_keys_inside_it(qk_services, tmp_path):
    svc, folder = qk_services
    first = index(svc, folder)
    other = tmp_path / "otra"
    other.mkdir()
    (other / "nota.txt").write_text("Una nota cualquiera sobre jardines y senderos de la aldea.\n", encoding="utf-8")
    second = svc.add_collection("otra", str(other), [], None, False, False)
    assert svc.worker.wait_idle(30)
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    svc.question_keys.generate(collection_id=first.id)
    query, _ = ev.QUERIES[0]
    assert svc.search.search(query, "hybrid", 5, collection_id=first.id)["hits"]
    assert all(h["collection_id"] == second.id for h in svc.search.search(query, "hybrid", 5, collection_id=second.id)["hits"])


# ---------- the model is not there ----------
def test_model_unavailable_skips_silently_and_marks_the_chunks(qk_services):
    svc, folder = qk_services
    down = ev.ScriptedGenerator(available=False)
    svc.question_keys.generator_override = down
    svc.question_keys.update_settings({"enabled": True})
    index(svc, folder)  # must complete normally
    status = svc.question_keys.status()
    assert status["chunks_unavailable"] == len(ev.CORPUS) and status["keys_exist"] is False and status["generator"]["available"] is False
    assert down.calls == 0
    assert svc.search.search(*[ev.QUERIES[0][0]], "hybrid", 3)["hits"]  # plain search is untouched
    # the model comes back: the next run picks those chunks up
    up = ev.ScriptedGenerator()
    svc.question_keys.generator_override = up
    result = svc.question_keys.generate(collection_id=1)
    assert result["generated"] == len(ev.CORPUS) and svc.question_keys.status()["chunks_unavailable"] == 0


def test_a_model_that_dies_midway_stops_after_three_failures(qk_services):
    svc, folder = qk_services
    index(svc, folder)

    class Flaky(ev.ScriptedGenerator):
        def generate(self, text, n=3):
            self.calls += 1
            raise GeneratorUnavailable("gone")

    flaky = Flaky()
    svc.question_keys.generator_override = flaky
    result = svc.question_keys.generate(collection_id=1)
    assert flaky.calls == 3 and result["model_available"] is False
    assert svc.question_keys.status()["chunks_unavailable"] == len(ev.CORPUS)


def test_force_regenerates_and_the_old_questions_stop_matching(qk_services):
    svc, folder = qk_services
    index(svc, folder)
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    svc.question_keys.generate(collection_id=1)
    document = doc_id(svc, "optica.txt")
    before = svc.question_keys.status(document)["questions"]

    class Rewriter(ev.ScriptedGenerator):
        def generate(self, text, n=3):
            return ["¿Qué violines suenan en la orquesta nocturna?"]

    svc.question_keys.generator_override = Rewriter()
    again = svc.question_keys.generate(document_id=document)
    assert again["processed"] == 0  # chunks that have keys are left alone without force
    forced = svc.question_keys.generate(document_id=document, force=True)
    assert forced["generated"] == 1 and svc.question_keys.status(document)["questions"] == 1 and before == 2
    with svc.db.lock:
        chunk_id = svc.db.conn.execute("SELECT id FROM chunks WHERE document_id = ?", (document,)).fetchone()["id"]
    assert chunk_id not in [cid for cid, _ in svc.question_keys.bm25(['"espejo"*'], 10, None)[0]]  # the old keys left the FTS index
    assert [cid for cid, _ in svc.question_keys.bm25(['"violin"*'], 5, None)[0]] == [chunk_id]


def test_reindexing_a_changed_document_drops_its_keys_and_marks_new_chunks_pending(qk_services):
    svc, folder = qk_services
    index(svc, folder)
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    svc.question_keys.generate(collection_id=1)
    path = folder / "optica.txt"
    path.write_text("Texto nuevo del archivo de optica, con otras palabras distintas para el fragmento y su presupuesto anual.\n", encoding="utf-8")
    svc.reindex(1)
    assert svc.worker.wait_idle(30)
    status = svc.question_keys.status(doc_id(svc, "optica.txt"))
    assert status["questions"] == 0 and status["chunks_pending"] == status["chunks"] >= 1
    assert svc.question_keys.status()["questions"] == sum(len(q) for n, _, q in ev.CORPUS if n != "optica.txt")


# ---------- API and MCP ----------
def test_api_settings_state_generate(client, library_with_questions):
    svc = client.services
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    response = client.post("/api/collections", json={"path": str(library_with_questions), "name": "corpus"})
    assert response.status_code in (200, 201)
    assert svc.worker.wait_idle(60)
    assert client.get("/api/question-keys/settings").json()["enabled"] is False
    assert client.put("/api/question-keys/settings", json={"enabled": True, "model": "m"}).json()["enabled"] is True
    state = client.get("/api/question-keys").json()
    assert state["keys_exist"] is False and state["chunks_pending"] == len(ev.CORPUS) and state["enabled"] is True
    document = doc_id(svc, "optica.txt")
    result = client.post("/api/question-keys/generate", json={"document_id": document}).json()
    assert result["generated"] == 1 and result["questions"] == 2 and result["model_available"] is True
    assert client.get("/api/question-keys", params={"document": document}).json()["chunks_with_keys"] == 1
    hit = client.get("/api/search", params={"q": ev.QUERIES[0][0]}).json()["hits"][0]
    assert hit["rel_path"] == "optica.txt" and hit["matched_question"]
    plain = client.get("/api/search", params={"q": ev.QUERIES[0][0], "questions": "false"}).json()["hits"][0]
    assert "matched_question" not in plain
    assert client.get(f"/api/question-keys/chunk/{hit['chunk_id']}").json()["questions"] == ev.CORPUS[0][2]
    assert client.post("/api/question-keys/generate", json={"document_id": 99999}).status_code == 404
    assert client.get("/api/question-keys", params={"collection": 99999}).status_code == 404
    assert client.get("/api/status").json()["question_keys"]["questions"] == 2


def test_mcp_tools_report_and_regenerate(client, library_with_questions):
    svc = client.services
    svc.question_keys.generator_override = ev.ScriptedGenerator()
    client.post("/api/collections", json={"path": str(library_with_questions), "name": "corpus"})
    assert svc.worker.wait_idle(60)
    auth = {"Authorization": f"Bearer {svc.token}"}

    def call(name, **arguments):
        response = client.post("/api/agent/call", json={"name": name, "arguments": arguments}, headers=auth)
        return response.status_code, response.json()

    code, body = call("library_question_keys")
    assert code == 200 and body["keys_exist"] is False and "No question keys yet" in body["note"]
    code, body = call("library_question_keys_settings")
    assert body["settings"]["enabled"] is False and body["changed"] == []
    code, body = call("library_question_keys_settings", enabled=True, model="m")
    assert body["settings"]["enabled"] is True and body["changed"] == ["enabled", "model"]
    assert call("library_generate_questions")[0] == 400  # needs a document or a collection
    document = doc_id(svc, "horarios.txt")
    code, body = call("library_generate_questions", document_id=document)
    assert code == 200 and body["generated"] == 1 and body["note"] is None
    code, body = call("library_question_keys", document_id=document)
    assert body["chunks_with_keys"] == 1 and body["questions"] == 2
    code, body = call("library_search", q=ev.QUERIES[1][0])
    assert body["hits"][0]["rel_path"] == "horarios.txt" and body["hits"][0]["matched_question"]
    assert call("library_generate_questions", document_id=99999)[0] == 404
    svc.question_keys.generator_override = ev.ScriptedGenerator(available=False)
    code, body = call("library_generate_questions", document_id=doc_id(svc, "normas.txt"))
    assert code == 200 and body["model_available"] is False and "could not be reached" in body["note"]


# ---------- the offline eval ----------
def test_offline_eval_the_keys_win_where_plain_search_does_not(tmp_path):
    result = ev.run_eval(tmp_path)
    first = result["rows"][0]
    assert first["plain"] != 1 and first["with_keys"] == 1  # the passage never uses the question's words
    assert result["recall_at_1_with_keys"] > result["recall_at_1_plain"]
    assert all(r["with_keys"] == 1 for r in result["rows"])
