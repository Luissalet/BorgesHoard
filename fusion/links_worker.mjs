// Native Express engine over stdio. HTTP is parsed in memory; no TCP listener.
import http from 'node:http';
import { Duplex } from 'node:stream';
import readline from 'node:readline';
const output = (message) => process.stdout.write(JSON.stringify(message) + '\n');
console.log = (...args) => console.error(...args);
await import('../links_native/server/no-sqlite-warning.js');
const { createApp } = await import('../links_native/server/app.js');
const { app } = createApp({ dataDir: process.env.LINKS_DATA_DIR, dataDirConfigured: true, automatic: process.env.BORGES_LINKS_AUTOMATIC === '1' });
const { setPublicUrl } = await import('../links_native/server/public-url.js');
setPublicUrl(process.env.BORGES_PUBLIC_URL + '/links');
if (process.env.BORGES_LINKS_AUTOMATIC === '1') (await import('../links_native/server/background.js')).startBackground();
const sockets = new Map();
const callbacks = new Map();
let nextCallback = 0;
function nativeCall(module, tool, args) {
  return new Promise((resolve) => {
    const id = 'native-' + (++nextCallback);
    callbacks.set(id, resolve);
    output({ type: 'native-call', id, module, tool, arguments: args });
  });
}
(await import('../links_native/server/videodigest.js')).setNativeOcrProvider(async (path, { lang }) => {
  const result = await nativeCall('paperwork', 'ocr_image', { path, lang });
  if (result?.ok === false || result?.error) return { ...result, ok: false, via: 'kafka', kind: result.kind === 'unavailable' ? 'unavailable' : 'error' };
  return { ...result, ok: true, via: 'kafka', backend: `kafka${result?.backend ? '/' + result.backend : ''}` };
});
const server = http.createServer((req, res) => {
  res.on('finish', () => { output({ id: req.socket.requestId, type: 'end' }); sockets.delete(req.socket.requestId); });
  app(req, res);
});
class MemorySocket extends Duplex {
  constructor(id) { super(); this.requestId = id; this.remoteAddress = '127.0.0.1'; }
  _read() {
    if (this.inputPending) { this.inputPending = false; output({ id: this.requestId, type: 'input-ready' }); }
  }
  _write(chunk, encoding, callback) {
    this.outputCallback = callback;
    output({ id: this.requestId, type: 'data', data: chunk.toString('base64') });
  }
  acknowledge() { const callback = this.outputCallback; this.outputCallback = null; callback?.(); }
  input(data) {
    if (this.push(data)) output({ id: this.requestId, type: 'input-ready' });
    else this.inputPending = true;
  }
  setTimeout() { return this; }
  setNoDelay() { return this; }
  setKeepAlive() { return this; }
  _destroy(error, callback) { callback(error); }
}
async function stop() {
  const { stopScheduler } = await import('../links_native/server/watches.js');
  const { stopBackground } = await import('../links_native/server/background.js');
  stopScheduler(); stopBackground();
  for (const socket of sockets.values()) socket.destroy();
  for (const [path, name] of [['site-login', 'shutdown'], ['videodigest', 'shutdownDigest'], ['videoinsight', 'shutdownVideo'], ['media', 'shutdownMedia'], ['fetcher', 'stop']]) {
    await (await import(`../links_native/server/${path}.js`))[name]();
  }
  (await import('../links_native/server/db.js')).close();
  output({ type: 'stopped' });
  process.exit(0);
}
const lines = readline.createInterface({ input: process.stdin });
lines.on('line', (line) => {
  try {
    const message = JSON.parse(line);
    if (message.type === 'native-result') { callbacks.get(message.id)?.(message.result); callbacks.delete(message.id); return; }
    if (message.type === 'ack') { sockets.get(message.id)?.acknowledge(); return; }
    if (message.type === 'input') {
      const socket = sockets.get(message.id);
      if (socket) socket.input(Buffer.from(message.data, 'base64'));
      else output({ id: message.id, type: 'input-ready' });
      return;
    }
    if (message.type === 'stop') { void stop().catch((error) => { console.error(error); process.exit(1); }); return; }
    if (message.type === 'cancel') { sockets.get(message.id)?.destroy(); sockets.delete(message.id); return; }
    const socket = new MemorySocket(message.id);
    socket.on('error', (error) => output({ id: message.id, type: 'error', error: error.message }));
    sockets.set(message.id, socket);
    server.emit('connection', socket);
    socket.input(Buffer.from(message.data, 'base64'));
  } catch (error) { output({ type: 'error', error: error.message }); }
});
lines.on('close', () => void stop());
output({ type: 'ready' });
