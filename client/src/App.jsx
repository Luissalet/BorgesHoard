import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { Toast } from "./components/ui.jsx";
import { MODEL_STATE } from "./format.js";
import Buscar from "./pages/Buscar.jsx";
import Biblioteca from "./pages/Biblioteca.jsx";
import Documento from "./pages/Documento.jsx";
import Colecciones from "./pages/Colecciones.jsx";
import Estado from "./pages/Estado.jsx";

const PAGES = [
  { path: "buscar", label: "Buscar", icon: "M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4", component: Buscar },
  { path: "biblioteca", label: "Biblioteca", icon: "M4 5h5v15H4zM10 5h5v15h-5zM16 6l4-1 3 14-4 1z", component: Biblioteca },
  { path: "colecciones", label: "Colecciones", icon: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z", component: Colecciones },
  { path: "estado", label: "Estado", icon: "M4 20V10m5 10V4m5 16v-8m5 8V7", component: Estado },
];

const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

function useHashRoute() {
  const read = () => {
    const parts = window.location.hash.replace(/^#\/?/, "").split("/");
    const [param, qs] = (parts[1] || "").split("?");
    return { page: parts[0] || "buscar", param: param || null, query: new URLSearchParams(qs || "") };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function Icon({ d }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export default function App() {
  const route = useHashRoute();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  const notify = useCallback((message) => setToast(message), []);
  const act = useCallback(
    async (fn, okMessage) => {
      try {
        const result = await fn();
        if (okMessage) setToast(okMessage);
        await refresh();
        return result;
      } catch (e) {
        setToast(e.message);
        return null;
      }
    },
    [refresh],
  );
  const value = useMemo(() => ({ status, refresh, notify, act }), [status, refresh, notify, act]);

  const page = PAGES.find((p) => p.path === route.page) || PAGES[0];
  const Component = page.path === "biblioteca" && route.param ? Documento : page.component;
  const busy = status?.worker?.busy;
  const model = status?.model;

  return (
    <AppContext.Provider value={value}>
      <div className="min-h-dvh md:grid md:grid-cols-[224px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-10 border-b md:h-dvh md:border-b-0 md:border-r" style={{ background: "var(--sidebar)", borderColor: "var(--line)" }}>
          <div className="flex items-center gap-2 px-4 py-3 md:px-5 md:py-5">
            <span className="serif grid h-8 w-8 place-items-center rounded-md text-[15px] font-bold text-white" style={{ background: "var(--accent)" }}>B</span>
            <div className="leading-tight">
              <div className="text-[15px] font-semibold">Borges's Hoard</div>
              <div className="help text-[11px]">Biblioteca personal</div>
            </div>
          </div>
          <nav aria-label="Secciones" className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col md:px-3">
            {PAGES.map((p) => (
              <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0 text-[13px]" aria-current={p.path === page.path ? "page" : undefined}>
                <Icon d={p.icon} />
                {p.label}
              </a>
            ))}
          </nav>
          {status && (
            <div className="hidden px-5 pt-4 md:block">
              <div className="help text-[11px]">Biblioteca</div>
              <div className="text-[13px]"><span className="num font-semibold">{status.counts.documents}</span> documentos · <span className="num">{status.counts.chunks}</span> pasajes</div>
              <div className="help mt-2 text-[11px]">Modelo</div>
              <div className="text-[13px]">{MODEL_STATE[model?.state] || model?.state}{busy ? " · indexando…" : ""}</div>
              {status.reindex_needed > 0 && <div className="help mt-2 text-[11px]" style={{ color: "var(--warn-ink)" }}>Reindexación necesaria: {status.reindex_needed} documentos</div>}
            </div>
          )}
        </aside>
        <main className="min-w-0 px-4 py-4 md:px-10 md:py-8">
          {error && (
            <div className="mb-4 rounded-md border p-4 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }} role="alert">
              No se pudo contactar con Borges: {error}. <button type="button" className="btn-link" onClick={refresh}>Reintentar</button>
            </div>
          )}
          <Component param={route.param} query={route.query} />
        </main>
      </div>
      <Toast message={toast} onClose={() => setToast(null)} />
    </AppContext.Provider>
  );
}
