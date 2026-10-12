// What the shared copilot launcher asks this app for: the real objects open in the current view and the unsaved form fields,
// plus a way to refresh the open view when the assistant finishes a turn. Nothing here renders anything.
import { api } from "./api.js";

const PAGE_NAMES = { buscar: "search", biblioteca: "library", colecciones: "collections", fuentes: "sources", estado: "status" };
const EDITOR_ORDER = { "search-form": 0, "collection-form": 1, "faustus-source-form": 2, "links-source-form": 3 };
const PREVIEW_CHARS = 240;

// Fields of `draft` whose value differs from `initial` (compared as JSON, so arrays and "" clears count).
export function changedFields(initial, draft) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key])));
}

// Snapshot a form answers with: only the fields that differ from what it started with. `secretsTyped` marks a form where the user
// typed a token/password: it counts as dirty but the secret itself never leaves the page.
export function editorContext(kind, objectId, initial, draft, secretsTyped = false) {
  const fields = changedFields(initial, draft);
  return { kind, object_id: objectId ?? null, dirty: secretsTyped || Object.keys(fields).length > 0, fields };
}

export function parseRoute(hash) {
  const [path, query = ""] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { page: parts[0] || "buscar", param: parts[1] || null, query: new URLSearchParams(query) };
}

// What the open page tells us that is not in the URL (the search filters, the passage panel, the unit shown in a document).
const viewState = {};
export function setViewState(key, value) {
  if (value === null || value === undefined) delete viewState[key];
  else viewState[key] = value;
}
export const getViewState = () => ({ ...viewState });

// Every open form answers this event with a snapshot of what the user has typed: { kind, object_id, dirty, fields }.
export function collectEditors(target = globalThis.window) {
  const found = [];
  if (!target?.dispatchEvent) return found;
  target.dispatchEvent(new CustomEvent("borges:context-request", { detail: { provide: (snapshot) => { if (snapshot) found.push(snapshot); } } }));
  return found.sort((a, b) => (EDITOR_ORDER[a.kind] ?? 9) - (EDITOR_ORDER[b.kind] ?? 9));
}

const pick = (source, keys) => Object.fromEntries(keys.filter((k) => source?.[k] !== undefined && source[k] !== null && source[k] !== "").map((k) => [k, source[k]]));
const wholeId = (value) => { const n = Number(value); return Number.isInteger(n) && n > 0 ? n : null; };

export function documentObject(d) {
  return {
    kind: "document", object_id: d.id, title: d.title || d.filename || String(d.id), doc_kind: d.kind,
    ...pick(d, ["collection", "rel_path", "pages", "units", "chunks", "status", "indexed_at"]),
    ...(d.needs_ocr ? { needs_ocr: true } : {}),
  };
}

export function passageObject(chunk, citation) {
  return {
    kind: "passage", object_id: chunk.id, title: citation || chunk.doc_title || String(chunk.id), document_id: chunk.document_id,
    ...pick(chunk, ["page", "section", "line"]),
    preview: String(chunk.text || "").slice(0, PREVIEW_CHARS),
  };
}

async function safe(fn) {
  try { return await fn(); } catch { return null; }
}

// Pure core (dependencies injected so it can be tested): never throws; unknown ids yield no object.
export async function buildContext({ hash, editors = [], state = {}, getDocument, getCollection, getChunk }) {
  const route = parseRoute(hash);
  const page = PAGE_NAMES[route.page] || route.page;
  const active = [];
  const seen = new Set();
  const add = (object) => { const key = `${object.kind}:${object.object_id}`; if (!seen.has(key)) { seen.add(key); active.push(object); } };
  let project = null;
  let viewObject = null;
  const view = { page };

  const docId = route.page === "biblioteca" ? wholeId(route.param) : wholeId(state.passage?.document_id);
  if (docId) {
    const doc = await safe(() => getDocument(docId));
    if (doc?.id) {
      add(documentObject(doc));
      if (route.page === "biblioteca") viewObject = doc.id;
      if (doc.collection_id && doc.collection) project = { external_project_id: String(doc.collection_id), title: doc.collection };
      const unit = wholeId(state.unit?.id ?? route.query.get("unit"));
      if (route.page === "biblioteca" && unit) view.unit = { id: unit, ...(state.unit?.label ? { label: state.unit.label } : {}) };
    }
  }
  const chunkId = route.page === "buscar" ? wholeId(state.passage?.chunk_id) : null;
  if (chunkId) {
    const chunk = await safe(() => getChunk(chunkId));
    if (chunk?.id) add(passageObject(chunk, state.passage?.citation));
  }
  const filterId = route.page === "buscar" ? wholeId(state.collection) : null;
  if (!project && filterId) {
    const collection = await safe(() => getCollection(filterId));
    if (collection?.id) project = { external_project_id: String(collection.id), title: collection.name };
  }
  if (viewObject) view.object_id = viewObject;
  const editor = editors.find((e) => e.dirty) || editors[0];
  if (editor) view.editor = editor;
  if (editors.length > 1) view.editors = editors;
  return { ...(project ? { project } : {}), active_objects: active, view };
}

export function context() {
  return buildContext({
    hash: globalThis.window?.location?.hash,
    editors: collectEditors(),
    state: getViewState(),
    getDocument: (id) => api.document(id),
    getCollection: (id) => api.collection(id),
    getChunk: (id) => api.chunk(id),
  });
}

// ---- refresh when the assistant finishes a turn -------------------------------------------------------------------------
const handlers = new Set();
let timer = null;

function runRefresh() {
  for (const fn of [...handlers]) {
    try { fn(); } catch { /* a refresh must never break the page */ }
  }
}

function scheduleRefresh() {
  clearTimeout(timer);
  timer = setTimeout(runRefresh, 250);
}

// Each mounted piece registers what "reload what is on screen" means for it (without touching the forms the user is typing in).
export function addRefreshHandler(fn) {
  handlers.add(fn);
  return () => handlers.delete(fn);
}

export const onTurnEnd = () => scheduleRefresh();

// Once at start: the object the launcher looks for, and the window event it dispatches on turn end (the debounce merges the two).
export function installCopilot(target = globalThis.window) {
  if (!target || target.hoardCopilot) return;
  target.hoardCopilot = { context, onTurnEnd };
  target.addEventListener("hoard:copilot-turn-end", scheduleRefresh);
}
