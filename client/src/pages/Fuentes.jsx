import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Empty, PageHeader, Progress, Switch } from "../components/ui.jsx";
import { PHASE_LABEL, when } from "../format.js";

const splitProjects = (text) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
const EMPTY_FORM = { base_url: "", name: "", username: "", password: "", token: "", include_projects: "", poll_minutes: 10 };
const EMPTY_LINKS = { name: "Mis enlaces", state: "all", tag: "", poll_minutes: 30 };
const STATE_LABEL = { all: "todos", unread: "pendientes", read: "leídos", archived: "archivados" };

export default function Fuentes() {
  const { status, act } = useApp();
  const [sources, setSources] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [useToken, setUseToken] = useState(true);
  const [linksForm, setLinksForm] = useState(EMPTY_LINKS);

  const load = () => api.sources().then((r) => setSources(r.sources)).catch(() => {});
  useEffect(() => {
    load();
  }, [status]);

  const submit = async (e) => {
    e.preventDefault();
    const body = {
      base_url: form.base_url.trim(), name: form.name.trim(), poll_minutes: Number(form.poll_minutes) || 10,
      include_projects: form.include_projects.trim() ? splitProjects(form.include_projects) : null,
    };
    if (useToken) body.token = form.token.trim();
    else {
      body.username = form.username.trim();
      body.password = form.password;
    }
    const created = await act(() => api.addSource(body), "Fuente Faustus añadida: la sincronización ha empezado.");
    if (created) {
      setForm(EMPTY_FORM);
      load();
    }
  };
  const submitLinks = async (e) => {
    e.preventDefault();
    const body = { name: linksForm.name.trim(), state: linksForm.state, tag: linksForm.tag.trim(), poll_minutes: Number(linksForm.poll_minutes) || 30 };
    const created = await act(() => api.addLinksSource(body), "Fuente de enlaces añadida: la sincronización ha empezado.");
    if (created) {
      setLinksForm(EMPTY_LINKS);
      load();
    }
  };
  const patch = (id, changes, message) => act(() => api.updateSource(id, changes), message).then(load);
  const remove = (s) => {
    if (!window.confirm(`¿Quitar la fuente «${s.name}»? Se borra el índice de esas conversaciones; Faustus no se toca.`)) return;
    act(() => api.removeSource(s.id), "Fuente eliminada.").then(load);
  };

  const stats = Object.fromEntries((status?.collections || []).map((c) => [c.id, c]));

  return (
    <div>
      <PageHeader title="Fuentes" description="Además de carpetas, Borges puede indexar tus conversaciones de Faustus y los enlaces que guardas en Links Hoard: cada chat y cada enlace pasa a ser un documento citable." />
      <form className="panel mb-6 grid gap-3 md:grid-cols-2" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="base_url">URL de Faustus</label>
          <input id="base_url" className="field" placeholder="http://127.0.0.1:7000" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} required />
        </div>
        <div>
          <label className="label" htmlFor="fname">Nombre</label>
          <input id="fname" className="field" placeholder="Mi Faustus" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="md:col-span-2 flex items-center gap-4 text-[13px]">
          <span className="label">Autenticación</span>
          <label className="flex items-center gap-2"><input type="radio" checked={useToken} onChange={() => setUseToken(true)} /> Token (recomendado)</label>
          <label className="flex items-center gap-2"><input type="radio" checked={!useToken} onChange={() => setUseToken(false)} /> Usuario y contraseña</label>
        </div>
        {useToken ? (
          <div className="md:col-span-2">
            <label className="label" htmlFor="token">Token de API de Faustus (alcance «sessions»)</label>
            <input id="token" className="field" type="password" placeholder="ody_…" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} />
          </div>
        ) : (
          <>
            <div>
              <label className="label" htmlFor="fuser">Usuario</label>
              <input id="fuser" className="field" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            </div>
            <div>
              <label className="label" htmlFor="fpass">Contraseña</label>
              <input id="fpass" className="field" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </div>
          </>
        )}
        <div>
          <label className="label" htmlFor="projects">Proyectos a incluir (opcional)</label>
          <input id="projects" className="field" placeholder="vacío = todos" value={form.include_projects} onChange={(e) => setForm({ ...form, include_projects: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="poll">Minutos entre sincronizaciones</label>
          <input id="poll" className="field" type="number" min="1" value={form.poll_minutes} onChange={(e) => setForm({ ...form, poll_minutes: e.target.value })} />
        </div>
        <div className="md:col-span-2">
          <button type="submit" className="btn btn-primary">Añadir fuente Faustus</button>
        </div>
      </form>

      <form className="panel mb-6 grid gap-3 md:grid-cols-4" onSubmit={submitLinks}>
        <div className="md:col-span-4 text-[13px]">
          <span className="font-semibold">Enlaces guardados (Links Hoard)</span>
          <span className="help ml-2">A través del Hoard Hub: no hace falta URL ni token. Cada enlace se indexa con el texto que Links Hoard extrajo; la cita es [enlace «Título» · sitio].</span>
        </div>
        <div>
          <label className="label" htmlFor="lname">Nombre</label>
          <input id="lname" className="field" value={linksForm.name} onChange={(e) => setLinksForm({ ...linksForm, name: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="lstate">Qué enlaces</label>
          <select id="lstate" className="field" value={linksForm.state} onChange={(e) => setLinksForm({ ...linksForm, state: e.target.value })}>
            {Object.entries(STATE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="ltag">Solo con etiqueta (opcional)</label>
          <input id="ltag" className="field" placeholder="vacío = todas" value={linksForm.tag} onChange={(e) => setLinksForm({ ...linksForm, tag: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="lpoll">Minutos entre sincronizaciones</label>
          <input id="lpoll" className="field" type="number" min="1" value={linksForm.poll_minutes} onChange={(e) => setLinksForm({ ...linksForm, poll_minutes: e.target.value })} />
        </div>
        <div className="md:col-span-4">
          <button type="submit" className="btn btn-primary">Añadir fuente de enlaces</button>
        </div>
      </form>

      {sources && sources.length === 0 && (
        <Empty title="Sin fuentes todavía">Conecta tu Faustus o tus enlaces guardados para que el asistente pueda citar lo que decidisteis o lo que guardaste.</Empty>
      )}
      <div className="space-y-3">
        {(sources || []).map((s) => {
          const p = s.progress;
          const st = stats[s.id] || {};
          const running = p && !["done", "error", "cancelled"].includes(p.phase);
          const sync = s.sync_status || {};
          return (
            <section key={s.id} className="panel-white">
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-[16px] font-semibold">{s.name} <span className="chip ml-2">{s.kind === "links" ? "enlaces" : "Faustus"}</span></h2>
                  <div className="help truncate">
                    {s.kind === "links"
                      ? `Links Hoard · ${STATE_LABEL[s.config?.state] || "todos"}${s.config?.tag ? ` · etiqueta ${s.config.tag}` : ""} · ${s.config?.base_url || (sync.via === "direct" ? "directo" : "vía Hoard Hub")}`
                      : s.config?.base_url}
                  </div>
                  <div className="help mt-1 num">
                    {st.documents ?? 0} {s.kind === "links" ? "enlaces" : "chats"} indexados{sync.error_count ? ` · ${sync.error_count} con error` : ""} · última sincronización {when(sync.last_run)}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-3 text-[12px]">
                  <label className="flex items-center gap-2">Activa <Switch checked={s.enabled} label="Activa" onChange={(v) => patch(s.id, { enabled: v }, v ? "Fuente activada." : "Fuente desactivada.")} /></label>
                  <button type="button" className="btn btn-sm" disabled={running} onClick={() => act(() => api.syncSource(s.id), "Sincronización en cola.").then(load)}>Sincronizar ahora</button>
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(s)}>Quitar</button>
                </div>
              </div>
              {p && (
                <div className="mt-3 text-[12px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`chip ${p.phase === "done" ? "chip-ok" : p.phase === "error" ? "chip-danger" : "chip-accent"}`}>{PHASE_LABEL[p.phase] || p.phase}</span>
                    {p.phase === "extracting" && <span className="num">{p.files_done} / {p.files_total} {s.kind === "links" ? "enlaces" : "chats"} · {p.current_file}</span>}
                    {p.phase === "embedding" && <span className="num">{p.chunks_embedded} / {p.chunks_to_embed} pasajes</span>}
                    {p.phase === "done" && <span className="help num">{p.files_changed} actualizados · {p.files_removed} eliminados</span>}
                    {p.phase === "error" && <span className="help">{p.message}</span>}
                  </div>
                  {running && <Progress progress={p} />}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
