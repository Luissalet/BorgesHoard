import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import Passage from "../components/Passage.jsx";
import { Empty, PageHeader, Snippet, TypeIcon } from "../components/ui.jsx";
import { queryTerms } from "../format.js";

const MIN_CHARS = 3;

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
  const [source, setSource] = useState("");
  const [collections, setCollections] = useState([]);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [expanded, setExpanded] = useState({});
  const input = useRef(null);
  const timer = useRef(null);

  useEffect(() => {
    api.collections().then((r) => setCollections(r.collections)).catch(() => {});
    input.current?.focus();
  }, []);

  const run = async (query = q, m = mode, c = collection, src = source) => {
    if (query.trim().length < MIN_CHARS) {
      setResult(null);
      return;
    }
    setLoading(true);
    try {
      setResult(await api.search({ q: query, mode: m, collection: c, source: src || undefined, limit: 20 }));
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
    const next = { mode, collection, source };
    if (setter === setMode) next.mode = value;
    else if (setter === setCollection) next.collection = value;
    else next.source = value;
    run(q, next.mode, next.collection, next.source);
  };

  const empty = status && status.counts.documents === 0;
  const modelState = status?.model?.state;
  const terms = queryTerms(q);
  const tooShort = q.trim().length > 0 && q.trim().length < MIN_CHARS;

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
          <select className="field" style={{ width: "auto" }} value={source} onChange={(e) => change(setSource)(e.target.value)} aria-label="Fuente">
            <option value="">Todo (documentos, chats, enlaces)</option>
            <option value="folder">Solo documentos</option>
            <option value="faustus">Solo chats (Faustus)</option>
            <option value="links">Solo enlaces (Links Hoard)</option>
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
      {tooShort && <p className="help mb-3">Escribe al menos {MIN_CHARS} caracteres para buscar.</p>}
      {status?.reindex_needed > 0 && (
        <p className="help mb-3">Reindexación necesaria: {status.reindex_needed} documentos se están volviendo a trocear en segundo plano; hasta entonces algunos resultados pueden estar fragmentados.</p>
      )}

      {empty && !result && (
        <Empty title="La biblioteca está vacía" action={<a className="btn btn-primary" href="#/colecciones">Añadir una carpeta</a>}>
          Añade una carpeta con tus PDF, DOCX, Markdown, EPUB o textos en Colecciones y Borges la indexará en segundo plano.
        </Empty>
      )}
      {!empty && !result && !loading && !tooShort && (
        <p className="help">Consejo: la búsqueda híbrida combina palabras exactas y significado; «Palabras» exige los términos tal cual; «Significado» encuentra paráfrasis.</p>
      )}

      <div className={`grid min-w-0 grid-cols-1 gap-4 ${open ? "md:grid-cols-[minmax(0,1fr)_minmax(320px,44%)]" : ""}`}>
        <div className="min-w-0 space-y-2">
          {loading && <p className="help">Buscando…</p>}
          {result && !loading && result.hits.length > 0 && (
            <p className="help num">{result.hits.length} {result.hits.length === 1 ? "pasaje" : "pasajes"} · {result.took_ms} ms · {MODES.find((m) => m.id === result.mode)?.label.toLowerCase()}</p>
          )}
          {result && result.hits.length === 0 && !loading && (
            <Empty title="Nada relevante">Ningún pasaje coincide con «{result.query}». Prueba otras palabras o el modo «Significado».</Empty>
          )}
          {result?.hits.map((h) => (
            <div key={h.chunk_id}>
              <button type="button" className="hit" aria-current={open?.chunk_id === h.chunk_id ? "true" : undefined} onClick={() => setOpen({ ...h, terms })}>
                <div className="flex items-center gap-3">
                  <TypeIcon kind={h.kind} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="cite truncate text-[14px]">{h.citation}</div>
                      {h.kind === "chat" && <span className="chip chip-accent">{h.date || "chat"}</span>}
                      {h.kind === "link" && <span className="chip chip-accent">enlace · {h.date || ""}</span>}
                    </div>
                    <div className="help truncate" title={`${h.collection} › ${h.rel_path}${h.section ? ` › ${h.section}` : ""}`}>
                      {h.kind === "chat"
                        ? `${h.collection}${h.project ? ` › ${h.project}` : ""}`
                        : h.kind === "link"
                        ? `${h.collection} › ${h.url || h.rel_path}`
                        : `${h.collection} › ${h.rel_path}${h.section && h.kind !== "md" && h.kind !== "html" && h.kind !== "docx" ? ` › ${h.section}` : ""}${h.line ? ` · l. ${h.line}` : ""}`}
                    </div>
                  </div>
                  <span className="help shrink-0 text-[11px]">abrir pasaje →</span>
                </div>
                <p className="mt-2 text-[13px] leading-relaxed"><Snippet html={h.snippet} /></p>
              </button>
              {h.more > 0 && !expanded[h.chunk_id] && (
                <button type="button" className="btn-link ml-4 mt-1 text-[12px]" onClick={() => setExpanded({ ...expanded, [h.chunk_id]: true })}>
                  ver más ({h.more} {h.more === 1 ? "pasaje más" : "pasajes más"} en esta {h.page ? "página" : "sección"})
                </button>
              )}
              {expanded[h.chunk_id] &&
                h.also.map((a) => (
                  <button key={a.chunk_id} type="button" className="hit ml-4 mt-1 w-[calc(100%-1rem)]" aria-current={open?.chunk_id === a.chunk_id ? "true" : undefined} onClick={() => setOpen({ ...h, ...a, terms })}>
                    <p className="text-[13px] leading-relaxed"><Snippet html={a.snippet} /></p>
                  </button>
                ))}
            </div>
          ))}
        </div>
        {open && <Passage target={open} onClose={() => setOpen(null)} />}
      </div>
    </div>
  );
}
