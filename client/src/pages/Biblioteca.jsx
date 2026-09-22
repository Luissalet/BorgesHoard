import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Empty, PageHeader, TypeIcon } from "../components/ui.jsx";
import { KIND_LABEL, bytes, when } from "../format.js";

export default function Biblioteca() {
  const { status } = useApp();
  const [collections, setCollections] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const [c, d] = await Promise.all([api.collections(), api.documents({ q, limit: 500 })]);
      setCollections(c.collections);
      setDocuments(d.documents);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [q, status?.counts?.documents]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = collections.map((c) => ({ collection: c, docs: documents.filter((d) => d.collection_id === c.id) })).filter((g) => g.docs.length || !q);

  return (
    <div>
      <PageHeader title="Biblioteca" description="Todos los documentos indexados, agrupados por colección. Pulsa uno para ver su índice y su texto.">
        <input className="field" style={{ maxWidth: 280 }} placeholder="Filtrar por título o archivo" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filtrar documentos" />
      </PageHeader>
      {error && <p className="help mb-3" role="alert">{error}</p>}
      {collections.length === 0 && (
        <Empty title="Todavía no hay documentos" action={<a className="btn btn-primary" href="#/colecciones">Añadir una carpeta</a>}>
          Añade una carpeta en Colecciones: Borges extraerá el texto de cada PDF, DOCX, Markdown, EPUB, HTML o TXT y lo dejará listo para buscar.
        </Empty>
      )}
      <div className="space-y-6">
        {groups.map(({ collection, docs }) => (
          <section key={collection.id}>
            <h2 className="mb-2 flex items-baseline gap-2 text-[16px] font-semibold">
              {collection.name}
              <span className="help font-normal">{docs.length} documentos · {collection.path}</span>
            </h2>
            <div className="panel-white p-0">
              {docs.length === 0 && <p className="help p-4">Sin documentos{collection.progress && collection.progress.phase !== "done" ? " todavía (indexando…)" : ""}.</p>}
              {docs.map((d) => (
                <a key={d.id} href={`#/biblioteca/${d.id}`} className="row row-link">
                  <TypeIcon kind={d.kind} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-semibold">{d.title || d.filename}</div>
                    <div className="help truncate">{d.rel_path}</div>
                  </div>
                  <div className="hidden shrink-0 gap-2 text-[12px] md:flex md:items-center">
                    {d.needs_ocr && <span className="chip chip-warn">sin texto · necesita OCR</span>}
                    {d.status === "error" && <span className="chip chip-danger" title={d.error}>error</span>}
                    <span className="chip">{KIND_LABEL[d.kind]}</span>
                    <span className="num help w-16 text-right">{bytes(d.size)}</span>
                    <span className="num help w-20 text-right">{d.kind === "pdf" ? `${d.pages} pág.` : `${d.units} secc.`}</span>
                    <span className="num help w-32 text-right">{when(d.indexed_at)}</span>
                  </div>
                  {(d.needs_ocr || d.status === "error") && <span className="md:hidden">{d.needs_ocr ? <span className="chip chip-warn">OCR</span> : <span className="chip chip-danger">error</span>}</span>}
                </a>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
