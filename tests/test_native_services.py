"""Native inter-module consumers with real jobs, IPC and CPU media processing."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient

from fusion.app import create_app

ROOT = Path(__file__).parents[1]
TEXT = 'Texto reconocido de la pagina escaneada: archivo sintético y referencia completa.'


@pytest.fixture
def native(tmp_path, monkeypatch):
    for key, value in {'BORGES_EMBED': 'none', 'BORGES_OCR': '1', 'KAFKA_OFFLINE': '1', 'KAFKA_OCR': '0',
                       'HOARD_COPILOT': '0', 'HOARD_EVENTS': '0', 'HOARD_HUB_URL': 'http://127.0.0.1:1'}.items():
        monkeypatch.setenv(key, value)
    from borges.extract import pdf
    from borges import links_source
    old_docs, old_links = pdf.fam_docs, links_source.native_caller
    try:
        with TestClient(create_app(tmp_path/'shell', port=5184), base_url='http://127.0.0.1:5184') as client:
            yield client
    finally:
        pdf.fam_docs, links_source.native_caller = old_docs, old_links


def enable(client, key):
    response = client.put('/api/modules/' + key, json={'enabled': True})
    assert response.status_code == 200, response.text
    return client.app.state.modules[key].app


class CpuOcr:
    def __init__(self, gate=None, mode='success'):
        self.gate, self.mode, self.calls = gate, mode, []
    def available(self):
        return self.mode != 'unavailable', 'synthetic-cpu'
    def recognise(self, image, lang):
        from kafka_hoard.ocr import OcrResult
        if self.gate is not None:
            assert self.gate.wait(5), 'test must release the real OCR job'
        self.calls.append((image.size, lang))
        return OcrResult(text=TEXT if self.mode == 'success' else '', backend='synthetic-cpu', error='synthetic OCR error' if self.mode == 'error' else '')


def scanned_pdf(path):
    import pymupdf
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page()
    doc.save(path)
    doc.close()
    return path


def prepare_pdf_job(client, monkeypatch, gate):
    paperwork = enable(client, 'paperwork').state.services
    paperwork.docsvc.ocr = CpuOcr(gate)
    enable(client, 'library')
    service = paperwork.docsvc
    start, status = service.ocr_pdf, service.status
    seen = []
    def immediate(path, **kwargs):
        assert 'timeout_s' not in kwargs
        kwargs['wait_s'] = 0
        result = start(path, **kwargs)
        seen.append(('start', result['job_id'], result['status']))
        return result
    def poll(job_id='', wait_s=0):
        seen.append(('poll', job_id, service.jobs.view(job_id)['status']))
        gate.set()
        return status(job_id, wait_s)
    monkeypatch.setattr(service, 'ocr_pdf', immediate)
    monkeypatch.setattr(service, 'status', poll)
    return service, seen


def test_library_pdf_consumes_real_native_pending_job(native, tmp_path, monkeypatch):
    from borges.extract import pdf
    gate = threading.Event()
    service, seen = prepare_pdf_job(native, monkeypatch, gate)
    try:
        result = pdf.extract_pdf(scanned_pdf(tmp_path/'scan.pdf'))
        assert len(result.units) == 2
        assert all(TEXT in unit.text for unit in result.units)
        assert all(page['method'] == 'ocr' for page in result.page_methods)
        assert not result.needs_ocr
        assert seen[0][2] in ('queued', 'running')
        assert any(item[0] == 'poll' for item in seen)
        assert service.jobs.view(seen[0][1])['status'] == 'done'
    finally:
        gate.set()


def test_native_ocr_timeout_preserves_resumable_real_job(native, tmp_path, monkeypatch):
    from borges.extract import pdf
    gate = threading.Event()
    service, seen = prepare_pdf_job(native, monkeypatch, gate)
    call = pdf.fam_docs.call
    def checked_call(module, tool, args):
        assert 'timeout_s' not in args
        return call(module, tool, args)
    monkeypatch.setattr(pdf.fam_docs, 'call', checked_call)
    original = service.status
    def pending(job_id='', wait_s=0):
        # Keep the real worker pending for the caller's deadline, not a fake job.
        return service.jobs.wait(job_id, wait_s)
    monkeypatch.setattr(service, 'status', pending)
    try:
        result = pdf.fam_docs.ocr_pdf(scanned_pdf(tmp_path/'slow.pdf'), timeout_s=.01)
        assert result['ok'] is False and result['kind'] == 'timeout', result
        assert result['via'] == 'kafka' and result['still_running']
        job_id = result['job_id']
        assert service.jobs.view(job_id)['status'] in ('queued', 'running')
        gate.set()
        monkeypatch.setattr(service, 'status', original)
        resumed = pdf.fam_docs.ocr_status(job_id, wait_s=3)
        assert resumed['ok'] and resumed['status'] == 'done'
        assert resumed['job_id'] == job_id and len(resumed['pages']) == 2
        error = pdf.fam_docs.ocr_pdf(str(tmp_path/'missing.pdf'))
        assert error['ok'] is False and error['via'] == 'kafka'
    finally:
        gate.set()


def seed_links(data_dir, clip=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    script = '''
import {init,close,db,now,setSetting} from './links_native/server/db.js';
import {createLink,updateLink} from './links_native/server/links.js';
init(process.argv[1]);
const {link}=createLink({url:'https://example.invalid/native-synthetic'});
updateLink(link.id,{title:'Native article'});
db().prepare("UPDATE links SET content_text=?,word_count=10,fetch_status='ok' WHERE id=?").run('Complete native saved article text for library indexing.',link.id);
if (process.argv[2]) {
  setSetting('digest',{ocr:true,vision:false,llm:false,frame_width:320,fps:1,min_segment_s:1,max_visual:4});
  setSetting('video',{max_frames:1});
  db().prepare("INSERT INTO link_media(link_id,status,source_path,transcript_status,created_at,updated_at) VALUES (?,'done',?,'no_audio',?,?)").run(link.id,process.argv[2],now(),now());
}
console.log(JSON.stringify({id:link.id}));close();
'''
    args = ['node', '--input-type=module', '-e', script, str(data_dir)] + ([str(clip)] if clip else [])
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)['id']


def test_links_library_real_caller_and_sync_without_old_http(native, monkeypatch):
    from borges import links_source
    def forbidden(*args, **kwargs):
        raise AssertionError('no legacy Hub or direct Links HTTP call is permitted')
    monkeypatch.setattr(links_source.family, 'call', forbidden)
    monkeypatch.setattr(links_source.LinksClient, '_direct', forbidden)
    data = native.app.state.modules['links'].data_dir
    link_id = seed_links(data)
    enable(native, 'links')
    service = enable(native, 'library').state.services
    caller = links_source.LinksClient()
    assert caller.list_links()[0]['id'] == link_id
    assert 'Complete native saved article' in caller.read_link(link_id)[1]
    response = native.post('/library/api/sources/links', json={'name': 'Native Links'})
    assert response.status_code == 201, response.text
    assert service.worker.wait_idle(10)
    collection = response.json()['id']
    assert service.worker.progress(collection)['phase'] == 'done'
    assert service.documents.counts()['documents'] == 1
    found = native.post('/api/agent/call', headers={'Authorization': 'Bearer '+native.app.state.token},
        json={'name': 'library_search', 'arguments': {'q': 'Complete', 'mode': 'bm25', 'source': 'links'}})
    assert found.status_code == 200, found.text
    hit = found.json()['hits'][0]
    read = native.post('/api/agent/call', headers={'Authorization': 'Bearer '+native.app.state.token},
        json={'name': 'library_read', 'arguments': {'document_id': hit['document_id']}})
    assert read.status_code == 200, read.text
    assert 'Complete native saved article text' in read.text


@pytest.mark.parametrize('mode', ['success', 'unavailable', 'error'])
def test_links_digest_real_ffmpeg_and_native_image_callback(native, tmp_path, mode):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('ffmpeg is not installed')
    clip = tmp_path/'local.mp4'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=5', '-t', '2', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(clip)], check=True, capture_output=True)
    data = native.app.state.modules['links'].data_dir
    link_id = seed_links(data, clip)
    fake = CpuOcr(mode=mode)
    enable(native, 'paperwork').state.services.docsvc.ocr = fake
    enable(native, 'links')
    token = (data/'mcp-token').read_text().strip()
    result = native.post('/links/api/agent/call', headers={'Authorization': 'Bearer '+token},
        json={'name':'video_digest','arguments':{'id':link_id,'path':str(clip),'wait':True,'timeout_s':30,'level':'visual'}})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['status'] in ('done', 'partial'), body
    assert body['stats']['distinct_frames'] >= 1, body
    if mode == 'success':
        assert body['stats']['ocr_frames'] >= 1, body
        assert body['stats']['engines']['ocr'] == 'kafka/synthetic-cpu', body
        assert TEXT in body['text'], body
        assert fake.calls
    elif mode == 'unavailable':
        assert 'Sin OCR' in body['detail'] or body['stats']['engines']['ocr'] == 'tesseract', body
    else:
        assert body['stats']['ocr_frames'] == 0
        assert 'OCR falló' in body['detail'] and 'Sin OCR' not in body['detail'], body
