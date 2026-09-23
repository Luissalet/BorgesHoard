import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Empty, PageHeader, Progress, Switch } from "../components/ui.jsx";
import { PHASE_LABEL, when } from "../format.js";

const splitGlobs = (text) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

export default function Colecciones() {
  const { status, act } = useApp();
  const [collections, setCollections] = useState(null);
  const [form, setForm] = useState({ path: "", name: "", include: "", exclude: "", watch: false, code: false });
  const [showExclude, setShowExclude] = useState(false);
  const [errorsOpen, setErrorsOpen] = useState({});

  const load = () => api.collections().then((r) => setCollections(r.collections)).catch(() => {});
  useEffect(() => {
    load();
  }, [status]); // status polls every few seconds → progress bars follow

  const submit = async (e) => {
    e.preventDefault();
    const body = { path: form.path.trim(), name: form.name.trim(), include: splitGlobs(form.include), watch: form.watch, code: form.code };
    if (showExclude) body.exclude = splitGlobs(form.exclude);
    const created = await act(() => api.addCollection(body), "Carpeta añadida: la indexación ha empezado.");
    if (created) {
      setForm({ path: "", name: "", include: "", exclude: "", watch: false, code: false });
      load();
    }
  };
  const patch = (id, changes, message) => act(() => api.updateCollection(id, changes), message).then(load);
  const remove = (c) => {
    if (!window.confirm(`¿Quitar «${c.name}» de la biblioteca? Los archivos no se tocan; solo se borra el índice.`)) return;
    act(() => api.removeCollection(c.id), "Colección eliminada.").then(load);
  };

  const stats = Object.fromEntries((status?.collections || []).map((c) => [c.id, c]));

  return (
    <div>
      <PageHeader title="Colecciones" description="Cada colección es una carpeta de tu ordenador. Borges la recorre, extrae el texto y solo vuelve a leer lo que cambia." />
      <form className="panel mb-6 grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="path">Ruta de la carpeta</label>
          <input id="path" className="field" placeholder="C:\Users\...\Documentos\Tesis" value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} required />
        </div>
        <div>
          <label className="label" htmlFor="name">Nombre</label>
          <input id="name" className="field" placeholder="Tesis" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="include">Incluir (globs, opcional)</label>
          <input id="include" className="field" placeholder="**/*.pdf, **/*.md — vacío = todos los formatos compatibles" value={form.include} onChange={(e) => setForm({ ...form, include: e.target.value })} />
          {showExclude ? (
            <>
              <label className="label mt-2" htmlFor="exclude">Excluir (globs)</label>
              <input id="exclude" className="field" placeholder="**/borradores/**" value={form.exclude} onChange={(e) => setForm({ ...form, exclude: e.target.value })} />
            </>
          ) : (
            <button type="button" className="btn-link mt-1 text-[12px]" onClick={() => setShowExclude(true)}>Personalizar exclusiones (por defecto: .git, node_modules, venv, ocultos)</button>
          )}
        </div>
        <div className="flex flex-col gap-2 text-[13px]">
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.watch} onChange={(e) => setForm({ ...form, watch: e.target.checked })} /> Vigilar cambios</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.code} onChange={(e) => setForm({ ...form, code: e.target.checked })} /> Incluir archivos de código</label>
          <button type="submit" className="btn btn-primary mt-auto">Añadir carpeta</button>
        </div>
      </form>

      {status?.reindex_needed > 0 && (
        <p className="help mb-3" style={{ color: "var(--warn-ink)" }}>Reindexación necesaria: {status.reindex_needed} documentos se trocearon con reglas antiguas y se están actualizando en segundo plano.</p>
      )}
      {collections && collections.length === 0 && (
        <Empty title="Aún no hay colecciones">Añade una carpeta con el formulario de arriba: apuntes, tesis, libros, artículos… Cualquier carpeta con PDF, DOCX, Markdown, EPUB, HTML o TXT.</Empty>
      )}
      <div className="space-y-3">
        {(collections || []).map((c) => {
          const p = c.progress;
          const s = stats[c.id] || {};
          const running = p && !["done", "error", "cancelled"].includes(p.phase);
          return (
            <section key={c.id} className="panel-white">
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-[16px] font-semibold">{c.name}</h2>
                  <div className="help truncate">{c.path}</div>
                  <div className="help mt-1 num">
                    {s.documents ?? 0} documentos · {s.chunks ?? 0} pasajes{s.errors ? ` · ${s.errors} con error` : ""}{s.needs_ocr ? ` · ${s.needs_ocr} sin texto (OCR)` : ""} · última indexación {when(c.last_indexed_at)}
                  </div>
                  {(c.include.length > 0 || c.code) && <div className="help mt-1">{c.include.length > 0 ? `incluye ${c.include.join(", ")}` : ""}{c.code ? " · con código" : ""}</div>}
                </div>
                <div className="flex flex-wrap items-center gap-3 text-[12px]">
                  <label className="flex items-center gap-2">Activa <Switch checked={c.enabled} label="Activa" onChange={(v) => patch(c.id, { enabled: v }, v ? "Colección activada." : "Colección desactivada.")} /></label>
                  <label className="flex items-center gap-2">Vigilar <Switch checked={c.watch} label="Vigilar cambios" onChange={(v) => patch(c.id, { watch: v }, v ? "Vigilando cambios." : "Ya no se vigila.")} /></label>
                  <button type="button" className="btn btn-sm" disabled={running} onClick={() => act(() => api.reindex(c.id), "Reindexación en cola.").then(load)}>Reindexar</button>
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(c)}>Quitar</button>
                </div>
              </div>
              {p && (
                <div className="mt-3 text-[12px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`chip ${p.phase === "done" ? "chip-ok" : p.phase === "error" ? "chip-danger" : "chip-accent"}`}>{PHASE_LABEL[p.phase] || p.phase}</span>
                    {p.phase === "extracting" && <span className="num">{p.files_done} / {p.files_total} archivos · {p.current_file}</span>}
                    {p.phase === "embedding" && <span className="num">{p.chunks_embedded} / {p.chunks_to_embed} pasajes</span>}
                    {p.phase === "done" && <span className="help num">{p.files_changed} cambiados · {p.files_removed} eliminados · {p.chunks_embedded} pasajes con embeddings · {Math.max(0, Math.round((p.finished_at - p.started_at) * 10) / 10)} s</span>}
                    {p.phase === "error" && <span className="help">{p.message}</span>}
                  </div>
                  {running && <Progress progress={p} />}
                  {p.error_count > 0 && (
                    <div className="mt-2">
                      <button type="button" className="btn-link" onClick={() => setErrorsOpen({ ...errorsOpen, [c.id]: !errorsOpen[c.id] })}>
                        {p.error_count} archivo(s) con error {errorsOpen[c.id] ? "▾" : "▸"}
                      </button>
                      {errorsOpen[c.id] && (
                        <ul className="mt-1 max-h-48 space-y-1 overflow-auto rounded-md border p-2" style={{ borderColor: "var(--danger-line)", background: "var(--danger-bg)", color: "var(--danger-ink)" }}>
                          {p.errors.map((e, i) => (
                            <li key={i}><span className="font-semibold">{e.path}</span>: {e.error}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
