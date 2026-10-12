"""Partial updates: a PATCH changes only the fields it carries, so an edit made meanwhile (by the user's form or by an agent) survives."""

import threading

from test_api import add_library


def test_collection_patch_touches_only_the_given_fields(client, library):
    created = add_library(client, library, include=["**/*.md"], watch=False)
    cid = created["id"]
    client.patch(f"/api/collections/{cid}", json={"name": "Lecturas"})
    client.patch(f"/api/collections/{cid}", json={"enabled": False})  # a second, different edit does not undo the first
    now = client.get(f"/api/collections/{cid}").json()
    assert now["name"] == "Lecturas" and now["enabled"] is False
    assert now["include"] == ["**/*.md"] and now["exclude"] == created["exclude"] and now["watch"] is False and now["code"] is False
    # an empty patch changes nothing
    assert client.patch(f"/api/collections/{cid}", json={}).json()["name"] == "Lecturas"
    # an explicit empty include list is a real change (clearing), not "not given"
    cleared = client.patch(f"/api/collections/{cid}", json={"include": []}).json()
    assert cleared["include"] == [] and cleared["name"] == "Lecturas"


def test_source_patch_merges_config_and_keeps_secrets(client):
    created = client.post("/api/sources", json={"base_url": "http://faustus.local", "name": "Mi Faustus", "token": "tok_secret", "poll_minutes": 10}).json()
    sid = created["id"]
    client.patch(f"/api/sources/{sid}", json={"poll_minutes": 30})
    client.patch(f"/api/sources/{sid}", json={"include_projects": ["Tesis"]})  # does not undo the first patch
    client.patch(f"/api/sources/{sid}", json={"name": "Renombrada"})
    stored = client.services.collections.get(sid)
    assert stored.name == "Renombrada" and stored.enabled is True
    assert stored.config["poll_minutes"] == 30 and stored.config["include_projects"] == ["Tesis"]
    assert stored.config["token"] == "tok_secret" and stored.config["base_url"] == "http://faustus.local"
    public = client.get(f"/api/sources/{sid}").json()
    assert "tok_secret" not in str(public)  # the client never sees the stored token


def test_concurrent_source_patches_of_different_fields_both_survive(client):
    sid = client.post("/api/sources", json={"base_url": "http://faustus.local", "token": "tok"}).json()["id"]
    store = client.services.collections
    barrier = threading.Barrier(2)

    def patch(change):
        barrier.wait()
        for i in range(25):
            store.update_config(sid, change(i))

    first = threading.Thread(target=patch, args=(lambda i: {"poll_minutes": 20 + i % 5},))
    second = threading.Thread(target=patch, args=(lambda i: {"include_projects": [f"p{i}"]},))
    first.start(), second.start()
    first.join(), second.join()
    config = store.get(sid).config
    assert 20 <= config["poll_minutes"] < 25 and config["include_projects"] == ["p24"]
    assert config["token"] == "tok" and config["base_url"] == "http://faustus.local"


def test_document_json_carries_what_the_copilot_context_reads(client, library):
    created = add_library(client, library)
    listed = client.get("/api/documents").json()["documents"]
    assert listed
    doc = client.get(f"/api/documents/{listed[0]['id']}").json()
    assert doc["collection_id"] == created["id"] and doc["collection"] == "Pruebas"
    for key in ("id", "title", "kind", "rel_path", "status", "outline"):
        assert key in doc
