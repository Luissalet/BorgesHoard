import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { markTerms, unitLabel } from "../format.js";
import { Marked } from "./ui.jsx";

/**
 * Side panel: the full page/section that contains a hit, with the hit text highlighted
 * and prev/next navigation. `target` = { document_id, unit_id?, page?, chunk_id?, title, citation, terms }.
 */
export default function Passage({ target, onClose }) {
  const [unit, setUnit] = useState(null);
  const [chunk, setChunk] = useState(null);
  const [error, setError] = useState(null);
  const [similar, setSimilar] = useState(null);

  useEffect(() => {
    let alive = true;
    setUnit(null);
    setChunk(null);
    setError(null);
    setSimilar(null);
    if (!target) return undefined;
    const params = target.unit_id ? { unit: target.unit_id } : target.page ? { page: target.page } : {};
    Promise.all([api.text(target.document_id, params), target.chunk_id ? api.chunk(target.chunk_id) : null])
      .then(([u, c]) => {
        if (!alive) return;
        setUnit(u);
        setChunk(c);
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [target]);

  const pieces = useMemo(() => {
    if (!unit) return [];
    const text = unit.text || "";
    if (chunk && chunk.unit_id === unit.id && text.slice(chunk.char_start, chunk.char_end) === chunk.text) {
      const before = text.slice(0, chunk.char_start);
      const inside = text.slice(chunk.char_start, chunk.char_end);
      const after = text.slice(chunk.char_end);
      return [
        ...markTerms(before, target.terms),
        { hit: markTerms(inside, target.terms) },
        ...markTerms(after, target.terms),
      ];
    }
    return markTerms(text, target?.terms);
  }, [unit, chunk, target]);

  const go = (next) => {
    if (!next) return;
    setUnit(null);
    setChunk(null);
    api.text(target.document_id, { unit: next.id }).then(setUnit).catch((e) => setError(e.message));
  };

  if (!target) return null;
  return (
    <aside className="side flex max-h-[calc(100dvh-2rem)] flex-col md:sticky md:top-4" aria-label="Pasaje">
      <div className="flex items-start gap-3 border-b p-4" style={{ borderColor: "var(--line)" }}>
        <div className="min-w-0 flex-1">
          <div className="cite truncate text-[14px]">{target.citation}</div>
          <div className="help truncate">{target.title}</div>
        </div>
        <a className="btn btn-sm" href={`#/biblioteca/${target.document_id}`}>Documento</a>
        <button type="button" className="btn btn-sm" onClick={onClose} aria-label="Cerrar pasaje">Cerrar</button>
      </div>
      <div className="flex items-center gap-2 border-b px-4 py-2 text-[12px]" style={{ borderColor: "var(--line)" }}>
        <button type="button" className="btn btn-sm" disabled={!unit?.prev} onClick={() => go(unit?.prev)}>← Anterior</button>
        <span className="num flex-1 text-center">{unit ? `${unitLabel(unit)} · ${unit.ordinal + 1} de ${unit.total}` : "…"}</span>
        <button type="button" className="btn btn-sm" disabled={!unit?.next} onClick={() => go(unit?.next)}>Siguiente →</button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-5">
        {error && <p className="help" role="alert">{error}</p>}
        {!unit && !error && <p className="help">Cargando…</p>}
        {unit && (
          <div className="passage">
            {pieces.map((piece, index) => {
              if (typeof piece === "string") return <React.Fragment key={index}>{piece}</React.Fragment>;
              if (piece.hit) {
                return (
                  <span key={index} className="rounded-sm" style={{ background: "#fdf3dc", boxShadow: "0 0 0 3px #fdf3dc" }}>
                    <Marked pieces={piece.hit} />
                  </span>
                );
              }
              return <mark key={index}>{piece.mark}</mark>;
            })}
          </div>
        )}
        {target.chunk_id && (
          <div className="mt-5 border-t pt-4" style={{ borderColor: "var(--line)" }}>
            {similar === null ? (
              <button type="button" className="btn-link text-[12px]" onClick={() => api.similar(target.chunk_id, 6).then((r) => setSimilar(r.hits)).catch((e) => setError(e.message))}>
                ¿Dónde más hablo de esto? — pasajes parecidos
              </button>
            ) : (
              <>
                <div className="help mb-2">Pasajes parecidos</div>
                {similar.length === 0 && <p className="help">No hay pasajes parecidos.</p>}
                <ul className="space-y-1 text-[13px]">
                  {similar.map((h) => (
                    <li key={h.chunk_id}>
                      <a className="btn-link" href={`#/biblioteca/${h.document_id}?unit=${h.unit_id}`}>{h.citation}</a>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
