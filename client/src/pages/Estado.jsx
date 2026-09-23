import React from "react";
import { useApp } from "../App.jsx";
import { PageHeader } from "../components/ui.jsx";
import { MODEL_STATE, PHASE_LABEL, bytes, when } from "../format.js";

function Stat({ label, value, help }) {
  return (
    <div className="panel-white">
      <div className="help text-[11px] uppercase tracking-wide">{label}</div>
      <div className="num mt-1 text-[24px] font-semibold leading-tight">{value}</div>
      {help && <div className="help mt-1">{help}</div>}
    </div>
  );
}

export default function Estado() {
  const { status } = useApp();
  if (!status) return <p className="help">Cargando…</p>;
  const m = status.model;
  const w = status.worker;
  const modelChip = m.state === "ready" ? "chip-ok" : m.state === "error" ? "chip-danger" : "chip-warn";
  return (
    <div>
      <PageHeader title="Estado" description="Modelo de embeddings, tamaño del índice, cola de trabajo y disco." />
      <div className="grid gap-3 md:grid-cols-4">
        <Stat label="Documentos" value={status.counts.documents} help={`${status.counts.errors} con error · ${status.counts.needs_ocr} sin texto`} />
        <Stat label="Pasajes" value={status.counts.chunks} help={status.chunks_pending_embedding ? `${status.chunks_pending_embedding} sin embedding` : "todos con embedding"} />
        <Stat label="Índice en disco" value={bytes(status.db_bytes)} help={`libres ${bytes(status.disk_free_bytes)}`} />
        <Stat label="Cola" value={w.queue_depth + (w.current ? 1 : 0)} help={status.reindex_needed ? `reindexación necesaria: ${status.reindex_needed} documentos` : w.busy ? "indexando ahora" : "en reposo"} />
      </div>

      <section className="panel-white mt-4">
        <h2 className="text-[16px] font-semibold">Modelo de embeddings</h2>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
          <span className={`chip ${modelChip}`}>{MODEL_STATE[m.state] || m.state}</span>
          <span className="font-mono text-[12px]">{m.model}</span>
          {m.dim ? <span className="help">{m.dim} dimensiones</span> : null}
          {m.load_seconds != null && <span className="help">cargado en {m.load_seconds} s</span>}
          <span className="help">backend {m.backend}</span>
        </div>
        {m.error && <p className="mt-2 text-[13px]" style={{ color: "var(--danger-ink)" }}>{m.error}</p>}
        <p className="help mt-2">
          Multilingüe (español incluido), se ejecuta en CPU y se descarga una sola vez en <code>{m.cache_dir || `${status.data_dir}/models`}</code>. Sin modelo, la búsqueda sigue funcionando por palabras.
        </p>
      </section>

      <section className="panel-white mt-4">
        <h2 className="text-[16px] font-semibold">Colecciones</h2>
        {status.collections.length === 0 && <p className="help mt-2">Ninguna todavía. <a className="btn-link" href="#/colecciones">Añade una carpeta</a>.</p>}
        {status.collections.map((c) => {
          const p = w.progress[String(c.id)];
          return (
            <div key={c.id} className="row">
              <div className="min-w-0 flex-1">
                <div className="font-semibold">{c.name}</div>
                <div className="help num">{c.documents} documentos · {c.chunks} pasajes{c.errors ? ` · ${c.errors} errores` : ""}{c.needs_ocr ? ` · ${c.needs_ocr} sin texto` : ""} · {when(c.indexed_at)}</div>
              </div>
              <span className="chip">{p ? PHASE_LABEL[p.phase] || p.phase : status.watching.includes(c.id) ? "vigilada" : "—"}</span>
            </div>
          );
        })}
      </section>

      <section className="panel-white mt-4 text-[13px]">
        <h2 className="text-[16px] font-semibold">Datos</h2>
        <p className="help mt-2">Todo vive en <code>{status.data_dir}</code>: la base de datos SQLite (texto, índice FTS5 y vectores) y el modelo. Vigilando: {status.watching.length ? status.watching.join(", ") : "ninguna carpeta"}.{status.watch_error ? ` Aviso: ${status.watch_error}` : ""}</p>
        {w.last_error && <p className="mt-2" style={{ color: "var(--danger-ink)" }}>Último error del indexador: {w.last_error}</p>}
        <p className="help mt-2">Versión {status.version} · en marcha desde {when(status.started_at)}</p>
      </section>
    </div>
  );
}
