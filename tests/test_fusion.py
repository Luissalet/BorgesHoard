"""Actual native engines, isolated SQLite histories and no live providers."""
import json
import os
import sys
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fusion.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('BORGES_EMBED', 'none')
    monkeypatch.setenv('BORGES_OCR', '0')
    monkeypatch.setenv('KAFKA_OFFLINE', '1')
    monkeypatch.setenv('KAFKA_OCR', '0')
    monkeypatch.setenv('HOARD_COPILOT', '0')
    with TestClient(create_app(tmp_path, port=5184), base_url='http://127.0.0.1:5184') as value:
        yield value


def call(client, name, args=None):
    return client.post('/api/agent/call', json={'name': name, 'arguments': args or {}},
                       headers={'Authorization': 'Bearer ' + client.app.state.token})


def enable(client, key):
    response = client.put('/api/modules/' + key, json={'enabled': True})
    assert response.status_code == 200, response.text
    return response.json()


def test_all_off_does_not_open_native_histories(client, tmp_path):
    modules = client.get('/api/modules').json()['modules']
    assert len(modules) == 3
    assert not any(m['enabled'] or m['automatic'] for m in modules)
    assert not list(tmp_path.rglob('*.db'))
    tools = client.get('/api/agent/tools').json()['tools']
    assert len(tools) == 378
    assert len({t['name'] for t in tools}) == 378
    response = call(client, 'list_links')
    assert response.status_code == 409
    assert not list(tmp_path.rglob('*.db'))
    search = call(client, 'borges_search_all', {'q': 'synthetic'})
    assert search.status_code == 200
    assert all(s['status'] == 'module_disabled' for s in search.json()['sources'])


def test_native_library_only_and_manual_work(client, tmp_path):
    enable(client, 'library')
    assert client.get('/library/api/status').status_code == 200
    native = client.app.state.modules['library'].app.state.services
    assert not native.config.autostart and not native.config.watch
    assert not native.faustus_scheduler._thread
    assert client.get('/library/').status_code == 200
    assert not (tmp_path / 'links').exists()
    assert client.put('/api/modules/library', json={'enabled': False}).status_code == 200
    assert not native.worker.running()


def test_native_links_stdio_no_listener_and_persistence(client):
    data = client.app.state.modules['links'].data_dir
    script = """
import {init, close} from './links_native/server/db.js';
import {createLink, updateLink} from './links_native/server/links.js';
init(process.argv[1]);
const {link} = createLink({url:'https://example.invalid/synthetic'});
updateLink(link.id, {title:'Synthetic article'});
close();
"""
    subprocess.run(['node', '--input-type=module', '-e', script, str(data)], cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True)
    enable(client, 'links')
    modules = client.app.state.modules
    engine = modules['links'].app.state.engine
    assert engine.process.returncode is None
    assert call(client, 'list_links').status_code == 200
    token = (data / 'mcp-token').read_text().strip()
    large = json.dumps({'name': 'list_links', 'arguments': {'unused': 'x' * 200_000}})
    streamed = client.post('/links/api/agent/call', content=large, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    assert streamed.status_code == 200, streamed.text
    asset = next((Path(__file__).resolve().parents[1] / 'links_native/dist/assets').glob('*.js'))
    full = client.get('/links/assets/' + asset.name)
    assert full.content == asset.read_bytes()
    ranged = client.get('/links/assets/' + asset.name, headers={'Range': 'bytes=20-99'})
    assert ranged.status_code == 206
    assert ranged.content == asset.read_bytes()[20:100]
    result = client.get('/links/api/links').json()
    assert 'Synthetic article' in json.dumps(result)
    assert client.get('/links/').status_code == 200
    assert client.put('/api/modules/links', json={'enabled': False}).status_code == 200
    assert engine.process.returncode == 0
    enable(client, 'links')
    assert 'Synthetic article' in client.get('/links/api/links').text


def test_native_paperwork_preserves_document_tools_without_scheduler(client):
    enable(client, 'paperwork')
    native = client.app.state.modules['paperwork'].app.state.services
    assert not native.config.scheduler
    assert not native.scheduler._alive()
    assert client.get('/paperwork/api/dashboard').status_code == 200
    result = call(client, 'extract_preview', {'text': 'Factura de Prueba. Total: 27,50 EUR. Fecha: 18/10/2026.'})
    assert result.status_code == 200, result.text
    assert client.get('/paperwork/').status_code == 200
    assert client.post('/api/modules/stop-all').status_code == 200
    assert not any(m['enabled'] for m in client.get('/api/modules').json()['modules'])


def test_mapped_data_disabled_without_opening_files(tmp_path):
    root = tmp_path / 'shell'
    root.mkdir()
    native = tmp_path / 'native'
    native.mkdir()
    sentinel = native / 'unread.db'
    sentinel.write_bytes(b'not sqlite; must stay untouched')
    (root / 'fusion-modules.json').write_text(json.dumps({'enabled': [], 'automatic': [], 'data_paths': {'paperwork': str(native)}}), encoding='utf-8')
    with TestClient(create_app(root), base_url='http://127.0.0.1:5184') as client:
        assert client.get('/api/modules').status_code == 200
    assert sentinel.read_bytes() == b'not sqlite; must stay untouched'
