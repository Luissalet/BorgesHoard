"""Folder watching: a change in a watched collection queues a reindex after the debounce."""

import time

import pytest

from borges.services import Services
from conftest import make_config
from fixtures import make_md


@pytest.fixture
def watched(tmp_path, library):
    services = Services(make_config(tmp_path, watch=True))
    services.worker.start()
    try:
        collection = services.add_collection("Vigilada", str(library), [], None, True, False)
        assert services.worker.wait_idle(60)
        yield services, collection
    finally:
        services.stop()


def test_change_in_watched_folder_triggers_reindex(watched, library):
    services, collection = watched
    if not services.watcher.available:
        pytest.skip(f"watchdog unavailable here: {services.watcher.error}")
    assert services.watcher.watching() == [collection.id]
    before = services.queries.list_documents(None, "", 100, None)["documents"]
    make_md(library / "nueva_nota.md")
    deadline = time.time() + 20
    while time.time() < deadline:
        docs = services.queries.list_documents(None, "", 100, None)["documents"]
        if len(docs) == len(before) + 1 and not services.worker.status()["busy"]:
            break
        time.sleep(0.2)
    assert len(services.queries.list_documents(None, "", 100, None)["documents"]) == len(before) + 1


def test_unwatch_when_disabled(watched):
    services, collection = watched
    if not services.watcher.available:
        pytest.skip("watchdog unavailable here")
    services.update_collection(collection.id, {"watch": False})
    assert services.watcher.watching() == []
    services.update_collection(collection.id, {"watch": True})
    assert services.watcher.watching() == [collection.id]
    services.update_collection(collection.id, {"enabled": False})
    assert services.watcher.watching() == []
