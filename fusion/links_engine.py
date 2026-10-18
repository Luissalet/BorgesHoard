"""Native Links ASGI app. Its Node engine has no network listening socket."""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import h11
import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent.parent


class LinksEngine:
    def __init__(self, data_dir, port, automatic=False, native_call=None):
        self.data_dir, self.port, self.automatic = Path(data_dir), port, automatic
        self.process = None
        self.reader = None
        self.pending = {}
        self.native_call = native_call
        self.callbacks = set()
        self.input_ready = {}

    async def callback(self, message):
        try:
            result = await asyncio.to_thread(self.native_call, message['module'], message['tool'], message.get('arguments') or {})
        except httpx.HTTPStatusError as exc:
            result = {'ok': False, 'kind': 'unavailable' if exc.response.status_code in (409, 503) else 'error', 'error': str(exc)}
        except Exception as exc:
            result = {'ok': False, 'kind': 'unavailable', 'error': str(exc)}
        await self.send({'type': 'native-result', 'id': message['id'], 'result': result})

    async def send(self, message):
        if self.process.returncode is not None:
            raise RuntimeError('El motor de enlaces se ha detenido.')
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        await self.process.stdin.drain()

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if message.get('type') == 'ready':
                    self.ready.set_result(True)
                elif message.get('type') == 'native-call':
                    callback = asyncio.create_task(self.callback(message))
                    self.callbacks.add(callback)
                    callback.add_done_callback(self.callbacks.discard)
                elif message.get('type') == 'input-ready':
                    if message['id'] in self.input_ready:
                        self.input_ready[message['id']].set()
                elif message.get('id') in self.pending:
                    await self.pending[message['id']].put(message)
        finally:
            for ready in self.input_ready.values():
                ready.set()
            if not self.ready.done():
                self.ready.set_exception(RuntimeError('El motor de enlaces no ha podido arrancar. Consulta su salida de error.'))
            for queue in self.pending.values():
                queue.put_nowait({'type': 'error', 'error': 'El motor de enlaces se ha detenido.'})

    async def start(self):
        env = dict(os.environ, LINKS_DATA_DIR=str(self.data_dir), BORGES_PUBLIC_URL=f'http://127.0.0.1:{self.port}',
                   BORGES_LINKS_AUTOMATIC='1' if self.automatic else '0')
        env['PYTHON'] = sys.executable
        env['LINKS_WATCHES'] = '1' if self.automatic else '0'
        env['LINKS_VIDEO_AUTO'] = '1' if self.automatic else '0'
        env['LINKS_RESURFACE'] = '1' if self.automatic else '0'
        self.ready = asyncio.get_running_loop().create_future()
        self.process = await asyncio.create_subprocess_exec(shutil.which('node') or 'node', str(ROOT / 'fusion/links_worker.mjs'),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=env, cwd=ROOT,
            limit=16 * 1024 * 1024, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.reader = asyncio.create_task(self.read())
        await self.ready

    async def stop(self):
        if self.process and self.process.returncode is None:
            await self.send({'type': 'stop'})
            await self.process.wait()
        if self.reader:
            await self.reader
        if self.callbacks:
            await asyncio.gather(*self.callbacks, return_exceptions=True)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return
        request_id = uuid.uuid4().hex
        queue = self.pending[request_id] = asyncio.Queue()
        ready = self.input_ready[request_id] = asyncio.Event()
        connection = h11.Connection(h11.CLIENT, max_incomplete_event_size=16 * 1024 * 1024)
        headers = [(k, v) for k, v in scope['headers'] if k.lower() not in (b'transfer-encoding', b'connection')]
        headers += [(b'connection', b'close')]
        if not any(k.lower() == b'host' for k, _ in headers):
            headers.append((b'host', f'127.0.0.1:{self.port}'.encode()))
        target = scope.get('raw_path') or scope['path'].encode()
        if scope.get('query_string'):
            target += b'?' + scope['query_string']
        raw = connection.send(h11.Request(method=scope['method'], target=target, headers=headers))
        disconnect = None
        try:
            await self.send({'id': request_id, 'data': base64.b64encode(raw).decode()})
            await ready.wait()
            async def input_frame(data):
                for offset in range(0, len(data), 64 * 1024):
                    ready.clear()
                    await self.send({'id': request_id, 'type': 'input', 'data': base64.b64encode(data[offset:offset + 64 * 1024]).decode()})
                    await ready.wait()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                if message.get('body'):
                    await input_frame(connection.send(h11.Data(data=message['body'])))
                if not message.get('more_body'):
                    await input_frame(connection.send(h11.EndOfMessage()))
                    break
            disconnect = asyncio.create_task(receive())
            while True:
                received = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((received, disconnect), return_when=asyncio.FIRST_COMPLETED)
                if disconnect in done:
                    received.cancel()
                    await asyncio.gather(received, return_exceptions=True)
                    return
                message = received.result()
                if message['type'] == 'error':
                    raise RuntimeError(message['error'])
                if message['type'] == 'data':
                    connection.receive_data(base64.b64decode(message['data']))
                elif message['type'] == 'end':
                    connection.receive_data(b'')
                while True:
                    event = connection.next_event()
                    if event is h11.NEED_DATA or event is h11.PAUSED:
                        break
                    if isinstance(event, h11.Response):
                        await send({'type': 'http.response.start', 'status': event.status_code,
                                    'headers': [(k, v) for k, v in event.headers if k.lower() not in (b'transfer-encoding', b'connection')]})
                    elif isinstance(event, h11.Data):
                        await send({'type': 'http.response.body', 'body': bytes(event.data), 'more_body': True})
                    elif isinstance(event, h11.EndOfMessage):
                        await send({'type': 'http.response.body', 'body': b''})
                        if message['type'] == 'data':
                            await self.send({'id': request_id, 'type': 'ack'})
                        return
                if message['type'] == 'data':
                    await self.send({'id': request_id, 'type': 'ack'})
        finally:
            self.pending.pop(request_id, None)
            self.input_ready.pop(request_id, None)
            if disconnect:
                disconnect.cancel()
                await asyncio.gather(disconnect, return_exceptions=True)
            if self.process and self.process.returncode is None:
                await self.send({'id': request_id, 'type': 'cancel'})


def create_app(data_dir, port, automatic=False, native_call=None):
    engine = LinksEngine(data_dir, port, automatic, native_call)
    @asynccontextmanager
    async def lifespan(app):
        try:
            await engine.start()
            yield
        finally:
            await engine.stop()
    app = FastAPI(lifespan=lifespan)
    app.state.engine = engine
    app.mount('/', engine)
    return app
