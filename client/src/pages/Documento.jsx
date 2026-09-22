import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { PageHeader, TypeIcon } from "../components/ui.jsx";
import { KIND_LABEL, bytes, unitLabel, when } from "../format.js";

/** Document view: metadata, outline (pages/sections) and the text of the selected unit. */
export default function Documento({ param, query }) {
  const id = Number(param);
  const [doc, setDoc] = useState(null);
  const [unit, setUnit] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDoc(null);
    setUnit(null);
    api.document(id)
      .then((d) => {
        setDoc(d);
        const wanted = Number(query?.get("unit")) || (d.outline[0] && d.outline[0].id);
        if (wanted) return api.text(id, { unit: wanted }).then(setUnit);
        return null;
      })
      .catch((e) => setError(e.message));
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const show = (unitId) => {
    setUnit(null);
    api.text(id, { unit: unitId }).then(setUnit).catch((e) => setError(e.message));
  };

  if (error) return <p className="help" role="alert">{error} <a className="btn-link" href="#/biblioteca">Volver a la biblioteca</a></p>;
  if (!doc) return <p className="help">Cargando…</p>;

  return (
    <div>
      <a className="btn-link text-[12px]" href="#/biblioteca">← Biblioteca</a>
      <PageHeader title={doc.title || doc.filename} description={doc.path}>
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <TypeIcon kind={doc.kind} />
          <span className="chip">{KIND_LABEL[doc.kind]}</span>
          {doc.needs_ocr && <span className="chip chip-warn">sin texto · necesita OCR</span>}
          {doc.status === "error" && <span className="chip chip-danger">error</span>}
          <span className="help num">{bytes(doc.size)} · {doc.kind === "pdf" ? `${doc.pages} páginas` : `${doc.units} secciones`} · {doc.chunks} pasajes · indexado {when(doc.indexed_at)}</span>
        </div>
      </PageHeader>
      {doc.error && <p className="mb-3 rounded-md border p-3 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }}>{doc.error}</p>}
      {doc.needs_ocr && <p className="help mb-3">Este PDF no tiene capa de texto (parece escaneado). Borges lo lista pero no puede buscar en él hasta que se le aplique OCR.</p>}
      {doc.outline.length > 0 && (
        <div className="grid gap-4 md:grid-cols-[260px_minmax(0,1fr)]">
          <nav className="panel-white max-h-[70dvh] overflow-auto p-2" aria-label="Índice">
            {doc.outline.map((u) => (
              <button
                key={u.id}
                type="button"
                className="block w-full rounded-md px-3 py-1.5 text-left text-[13px]"
                style={unit?.id === u.id ? { background: "var(--nav-active)", color: "var(--nav-active-ink)", fontWeight: 600 } : undefined}
                onClick={() => show(u.id)}
              >
                <span className="num">{unitLabel(u)}</span>
                <span className="help ml-2 text-[11px]">{u.chars} car.</span>
              </button>
            ))}
          </nav>
          <article className="panel-white">
            {!unit && <p className="help">Cargando…</p>}
            {unit && (
              <>
                <div className="mb-3 flex items-center gap-2 text-[12px]">
                  <button type="button" className="btn btn-sm" disabled={!unit.prev} onClick={() => show(unit.prev.id)}>← Anterior</button>
                  <span className="num flex-1 text-center">{unitLabel(unit)}{unit.line_start ? ` · línea ${unit.line_start}` : ""} · {unit.ordinal + 1} de {unit.total}</span>
                  <button type="button" className="btn btn-sm" disabled={!unit.next} onClick={() => show(unit.next.id)}>Siguiente →</button>
                </div>
                <div className="passage">{unit.text}</div>
              </>
            )}
          </article>
        </div>
      )}
    </div>
  );
}
