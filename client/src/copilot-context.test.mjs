import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addRefreshHandler, buildContext, changedFields, collectEditors, editorContext, installCopilot, onTurnEnd, parseRoute, setViewState, getViewState,
} from "./copilot-context.js";

const doc = { id: 7, title: "Dune", kind: "pdf", collection: "Novelas", collection_id: 2, rel_path: "sf/dune.pdf", pages: 412, chunks: 900, status: "ok", needs_ocr: false, outline: [{ id: 1 }], path: "C:\\private\\sf\\dune.pdf" };
const chunk = { id: 55, document_id: 7, doc_title: "Dune", page: 12, text: "x".repeat(1000) };

test("changedFields only reports differences, including cleared values", () => {
  assert.deepEqual(changedFields({ a: "1", b: [1], c: "x" }, { a: "1", b: [1, 2], c: "" }), { b: [1, 2], c: "" });
  assert.deepEqual(changedFields(null, { a: "" }), { a: "" });
  assert.deepEqual(changedFields({ a: 1 }, null), {});
});

test("editorContext is clean when nothing changed and dirty with a secret typed", () => {
  assert.deepEqual(editorContext("collection-form", null, { path: "" }, { path: "" }), { kind: "collection-form", object_id: null, dirty: false, fields: {} });
  const secret = editorContext("faustus-source-form", null, { name: "" }, { name: "" }, true);
  assert.equal(secret.dirty, true);
  assert.deepEqual(secret.fields, {});
});

test("parseRoute reads page, id and query from the hash", () => {
  assert.deepEqual({ ...parseRoute("#/biblioteca/12?unit=5"), query: undefined }, { page: "biblioteca", param: "12", query: undefined });
  assert.equal(parseRoute("#/biblioteca/12?unit=5").query.get("unit"), "5");
  assert.equal(parseRoute("").page, "buscar");
});

test("a document view gives the real document, its collection as project, and the unit", async () => {
  const ctx = await buildContext({ hash: "#/biblioteca/7?unit=3", state: { unit: { id: 9, label: "p. 4" } }, getDocument: async () => doc });
  assert.deepEqual(ctx.project, { external_project_id: "2", title: "Novelas" });
  assert.equal(ctx.active_objects.length, 1);
  assert.equal(ctx.active_objects[0].kind, "document");
  assert.equal(ctx.active_objects[0].object_id, 7);
  assert.equal(ctx.active_objects[0].title, "Dune");
  assert.equal(JSON.stringify(ctx).includes("private"), false, "no filesystem path leaks");
  assert.deepEqual(ctx.view, { page: "library", object_id: 7, unit: { id: 9, label: "p. 4" } });
});

test("unknown id, fetch error and non-numeric ids give no object and never throw", async () => {
  const fail = async () => { throw new Error("boom"); };
  for (const [hash, getter] of [["#/biblioteca/999", async () => null], ["#/biblioteca/7", fail], ["#/biblioteca/new", fail], ["#/biblioteca/abc", fail]]) {
    const ctx = await buildContext({ hash, getDocument: getter, getCollection: fail, getChunk: fail });
    assert.deepEqual(ctx.active_objects, []);
    assert.equal(ctx.project, undefined);
    assert.equal(ctx.view.page, "library");
  }
});

test("the search view reports the open passage with a short preview and the filtered collection as project", async () => {
  const state = { collection: "2", passage: { chunk_id: 55, document_id: 7, citation: "«Dune», p. 12" } };
  const ctx = await buildContext({
    hash: "#/buscar", state, getDocument: async () => ({ ...doc, collection_id: undefined }), getChunk: async () => chunk, getCollection: async () => ({ id: 2, name: "Novelas" }),
    editors: [{ kind: "search-form", object_id: null, dirty: true, fields: { q: "arena" } }],
  });
  assert.deepEqual(ctx.project, { external_project_id: "2", title: "Novelas" });
  assert.deepEqual(ctx.active_objects.map((o) => o.kind), ["document", "passage"]);
  const passage = ctx.active_objects[1];
  assert.equal(passage.object_id, 55);
  assert.equal(passage.title, "«Dune», p. 12");
  assert.equal(passage.preview.length, 240);
  assert.equal(ctx.view.page, "search");
  assert.deepEqual(ctx.view.editor.fields, { q: "arena" });
  assert.equal(ctx.view.editors, undefined);
});

test("the search view without a passage or filter has no project and no objects", async () => {
  const ctx = await buildContext({ hash: "#/buscar", state: {}, editors: [{ kind: "search-form", object_id: null, dirty: false, fields: {} }] });
  assert.deepEqual(ctx.active_objects, []);
  assert.equal(ctx.project, undefined);
});

test("editor choice: first dirty form, all of them when several", async () => {
  const a = { kind: "faustus-source-form", object_id: null, dirty: false, fields: {} };
  const b = { kind: "links-source-form", object_id: null, dirty: true, fields: { tag: "x" } };
  const ctx = await buildContext({ hash: "#/fuentes", editors: [a, b] });
  assert.equal(ctx.view.page, "sources");
  assert.equal(ctx.view.editor, b);
  assert.deepEqual(ctx.view.editors, [a, b]);
});

test("collectEditors gathers snapshots in a stable order and ignores empty answers", () => {
  const listeners = [];
  const target = {
    addEventListener: (_n, fn) => listeners.push(fn),
    dispatchEvent: (event) => { assert.equal(event.type, "borges:context-request"); listeners.forEach((fn) => fn(event)); return true; },
  };
  target.addEventListener("x", (e) => e.detail.provide({ kind: "links-source-form", dirty: false, fields: {} }));
  target.addEventListener("x", (e) => e.detail.provide(null));
  target.addEventListener("x", (e) => e.detail.provide({ kind: "search-form", dirty: true, fields: {} }));
  assert.deepEqual(collectEditors(target).map((e) => e.kind), ["search-form", "links-source-form"]);
});

test("view state is set and cleared", () => {
  setViewState("collection", "3");
  assert.equal(getViewState().collection, "3");
  setViewState("collection", null);
  assert.equal("collection" in getViewState(), false);
});

test("installCopilot registers once and turn-end events and onTurnEnd share one debounced refresh", async () => {
  const events = {};
  const target = { addEventListener: (name, fn) => { events[name] = fn; } };
  installCopilot(target);
  const first = target.hoardCopilot;
  assert.equal(typeof first.context, "function");
  assert.equal(typeof first.onTurnEnd, "function");
  installCopilot(target);
  assert.equal(target.hoardCopilot, first);
  let calls = 0;
  const off = addRefreshHandler(() => { calls += 1; });
  addRefreshHandler(() => { throw new Error("a broken handler must not stop the others"); });
  events["hoard:copilot-turn-end"]();
  onTurnEnd();
  first.onTurnEnd();
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(calls, 1);
  off();
});
