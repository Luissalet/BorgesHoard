"""The common launcher keeps every adopted native conversation namespace."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient
from fusion.app import create_app


def test_native_spaces_and_root_relative_relay_are_preserved(tmp_path, monkeypatch):
    class FakeFaustus(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(b'{"contract_version":"1.0"}')

        def do_POST(self):
            payload = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(payload)

    stub = ThreadingHTTPServer(('127.0.0.1', 0), FakeFaustus)
    thread = threading.Thread(target=stub.serve_forever, daemon=True); thread.start()
    monkeypatch.setenv('HOARD_FAUSTUS_URL', f'http://127.0.0.1:{stub.server_port}')
    monkeypatch.setenv('HOARD_COPILOT', '1')
    monkeypatch.setenv('BORGES_EMBED', 'none'); monkeypatch.setenv('BORGES_OCR', '0')
    monkeypatch.setenv('KAFKA_OFFLINE', '1'); monkeypatch.setenv('KAFKA_OCR', '0')
    spaces = {'library': '11111111-1111-4111-8111-111111111111',
              'links': '22222222-2222-4222-8222-222222222222',
              'paperwork': '33333333-3333-4333-8333-333333333333'}
    ids = {'library': 'borges', 'links': 'links', 'paperwork': 'kafka'}
    roots = {key: tmp_path / key for key in spaces}
    for key, root in roots.items():
        root.mkdir(); (root / 'copilot-space-id').write_text(spaces[key], encoding='utf-8')
    (tmp_path / 'fusion-modules.json').write_text(json.dumps({'enabled': [], 'data_paths': {k: str(v) for k,v in roots.items()}}), encoding='utf-8')
    try:
        with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:5184') as client:
            for key in spaces:
                headers = {'Referer': f'http://127.0.0.1:5184/{key}/', 'X-Hoard-Request': '1'}
                assert client.get('/api/copilot/info', headers=headers).status_code == 409
                assert not list(roots[key].glob('*.db'))
                assert client.put('/api/modules/' + key, json={'enabled': True}).status_code == 200
                for route in ['/api/copilot/info', f'/{key}/api/copilot/info']:
                    response = client.get(route, headers=headers)
                    assert response.status_code == 200, response.text
                    info = response.json()
                    assert info['app_id'] == info['tool_server_id'] == ids[key]
                    assert info['data_space_id'] == spaces[key]
                envelope = {'path': '/api/hoard-copilot/projects/resolve', 'method': 'POST',
                            'body': {'context': {'origin': {'app_id': 'wrong'}, 'view': {'page': 'synthetic'}}}}
                response = client.post('/api/copilot/proxy', headers=headers, json=envelope)
                assert response.status_code == 200, response.text
                origin = response.json()['context']['origin']
                assert origin['app_id'] == ids[key] and origin['data_space_id'] == spaces[key]
                assert client.put('/api/modules/' + key, json={'enabled': False}).status_code == 200
                assert (roots[key] / 'copilot-space-id').read_text() == spaces[key]
            central = client.get('/api/copilot/info').json()
            assert central['app_id'] == 'borges'
            assert central['data_space_id'] == (tmp_path / 'copilot-space-id').read_text()
            for referer in ['https://127.0.0.1:5184/links/', 'http://127.0.0.1:5185/links/',
                            'http://elsewhere.invalid/links/', 'http://127.0.0.1:5184/linksdifferent/',
                            'http://[invalid']:
                assert client.get('/api/copilot/info', headers={'Referer': referer}).json() == central
    finally:
        stub.shutdown(); stub.server_close(); thread.join()
