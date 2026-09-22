import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Passage from "../components/Passage.jsx";
import { Empty, PageHeader, Snippet, TypeIcon } from "../components/ui.jsx";
import { KIND_LABEL, queryTerms } from "../format.js";

const MODES = [
  { id: "hybrid", label: "Híbrida" },
  { id: "bm25", label: "Palabras" },
  { id: "dense", label: "Significado" },
];

export default function Buscar() {
  const { status } = useApp();
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [collection, setCollection] = useState("");
  const [collections, setCollections] = useState([]);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const input = useRef(null);
  const timer = useRef(null);

  useEffect(() => {
    api.collections().then((r) => setCollections(r.collections)).catch(() => {});
    input.current?.focus();
  }, []);

  const run = async (query = q, m = mode, c = collection) => {
    if (!query.trim()) {
      setResult(null);
      return;
    }
    setLoading(true);
    try {
      setResult(await api.search({ q: query, mode: m, collection: c, limit: 20 }));
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onChange = (value) => {
    setQ(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => run(value), 350);
  };
  const change = (setter) => (value) => {
    setter(value);
    const next = { mode, collection };
    if (setter === setMode) next.mode = value;
    else next.collection = value;
    run(q, next.mode, next.collection);
  };

  const empty = status && status.counts.documents === 0;
  const modelState = status?.model?.state;
  const terms = queryTerms(q);

  return (
    <div>
      <PageHeader title="Buscar" description="¿Dónde leí o escribí sobre esto? Escribe la idea con tus palabras; los resultados citan documento y página o sección." />
      <form
        className="mb-4 flex flex-col gap-3 md:flex-row md:items-center"
        onSubmit={(e) => {
          e.preventDefault();
          clearTimeout(timer.current);
          run();
        }}
      >
        <input ref={input} className="field field-lg flex-1" placeholder="p. ej. la memoria como vertedero, hojas de los árboles, presupuesto del observatorio…" value={q} onChange={(e) => onChange(e.target.value)} aria-label="Buscar en la biblioteca" />
        <div className="flex flex-wrap items-center gap-2">
          <div className="seg" role="group" aria-label="Modo de búsqueda">
            {MODES.map((m) => (
              <button key={m.id} type="button" aria-pressed={mode === m.id} onClick={() => change(setMode)(m.id)} disabled={m.id === "dense" && modelState !== "ready"}>
                {m.label}
              </button>
            ))}
          </div>
          <select className="field" style={{ width: "auto" }} value={collection} onChange={(e) => change(setCollection)(e.target.value)} aria-label="Colección">
            <option value="">Todas las colecciones</option>
            {collections.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
      </form>
      {modelState && modelState !== "ready" && (
        <p className="help mb-3">
          Modelo de embeddings: {modelState === "downloading" ? "descargándose (~240 MB, solo la primera vez)" : modelState === "loading" ? "cargándose" : modelState === "error" ? `error (${status.model.error})` : "sin cargar"}.
          Mientras tanto la búsqueda es solo por palabras.
        </p>
      )}
      {error && <p className="help mb-3" role="alert">{error}</p>}

      {empty && !result && (
        <Empty title="La biblioteca está vacía" action={<a className="btn btn-primary" href="#/colecciones">Añadir una carpeta</a>}>
          Añade una carpeta con tus PDF, DOCX, Markdown, EPUB o textos en Colecciones y Borges la indexará en segundo plano.
        </Empty>
      )}
      {!empty && !result && !loading && (
        <p className="help">Consejo: la búsqueda híbrida combina palabras exactas y significado; «Palabras» exige los términos tal cual; «Significado» encuentra paráfrasis.</p>
      )}

      <div className={`grid min-w-0 grid-cols-1 gap-4 ${open ? "md:grid-cols-[minmax(0,1fr)_minmax(320px,44%)]" : ""}`}>
        <div className="min-w-0 space-y-2">
          {loading && <p className="help">Buscando…</p>}
          {result && result.hits.length === 0 && !loading && (
            <Empty title="Nada relevante">Ningún pasaje coincide con «{result.query}». Prueba otras palabras o el modo «Significado».</Empty>
          )}
          {result?.hits.map((h) => (
            <button key={h.chunk_id} type="button" className="hit" aria-current={open?.chunk_id === h.chunk_id ? "true" : undefined} onClick={() => setOpen({ ...h, terms })}>
              <div className="flex items-center gap-3">
                <TypeIcon kind={h.kind} />
                <div className="min-w-0 flex-1">
                  <div className="cite truncate text-[14px]">{h.citation}</div>
                  <div className="help truncate">{h.title !== h.citation ? h.title : ""}{h.title ? " · " : ""}{h.rel_path} · {h.collection} · {KIND_LABEL[h.kind]}</div>
                </div>
                <span className="help shrink-0 text-[11px]">abrir pasaje →</span>
              </div>
              <p className="mt-2 text-[13px] leading-relaxed"><Snippet html={h.snippet} /></p>
            </button>
          ))}
        </div>
        {open && <Passage target={open} onClose={() => setOpen(null)} />}
      </div>
    </div>
  );
}
