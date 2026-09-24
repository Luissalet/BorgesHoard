import React, { useEffect } from "react";
import { KIND_LABEL } from "../format.js";

export function Toast({ message, onClose }) {
  useEffect(() => {
    if (!message) return undefined;
    const timer = setTimeout(onClose, 4000);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  if (!message) return null;
  return (
    <div className="toast" role="status">
      {message} <button type="button" className="ml-3 underline" onClick={onClose}>Cerrar</button>
    </div>
  );
}

export function Switch({ checked, onChange, label }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="switch" onClick={() => onChange(!checked)} />;
}

export function Empty({ title, children, action }) {
  return (
    <div className="rounded-lg border border-dashed p-8 text-center" style={{ borderColor: "var(--field-line)" }}>
      <p className="font-semibold">{title}</p>
      <p className="help mt-1">{children}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function PageHeader({ title, description, children }) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[26px] font-semibold leading-tight md:text-[30px]">{title}</h1>
        {description && <p className="help mt-1 max-w-[70ch]">{description}</p>}
      </div>
      {children}
    </header>
  );
}

const KIND_GLYPH = { pdf: "PDF", docx: "DOC", md: "MD", txt: "TXT", epub: "EPUB", html: "HTML", csv: "CSV", code: "</>", chat: "CHAT" };

export function TypeIcon({ kind }) {
  return (
    <span
      className="grid h-8 w-10 shrink-0 place-items-center rounded-md text-[10px] font-bold tracking-wide"
      style={{ background: "var(--accent-soft)", color: "var(--accent-hover)" }}
      title={KIND_LABEL[kind] || kind}
      aria-label={KIND_LABEL[kind] || kind}
    >
      {KIND_GLYPH[kind] || kind}
    </span>
  );
}

/** Renders a server snippet that contains <mark> tags and nothing else. */
export function Snippet({ html }) {
  const parts = String(html || "").split(/(<mark>|<\/mark>)/);
  let inMark = false;
  return (
    <span>
      {parts.map((part, index) => {
        if (part === "<mark>") { inMark = true; return null; }
        if (part === "</mark>") { inMark = false; return null; }
        const text = decode(part);
        return inMark ? <mark key={index}>{text}</mark> : <React.Fragment key={index}>{text}</React.Fragment>;
      })}
    </span>
  );
}

function decode(text) {
  return text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

export function Marked({ pieces, className }) {
  return (
    <>
      {pieces.map((piece, index) =>
        typeof piece === "string" ? <React.Fragment key={index}>{piece}</React.Fragment> : <mark key={index} className={className}>{piece.mark}</mark>,
      )}
    </>
  );
}

export function Progress({ progress }) {
  if (!progress) return null;
  const total = progress.files_total || 0;
  const pct = progress.phase === "done" ? 100 : progress.phase === "embedding" ? 100 : total ? Math.round((progress.files_done / total) * 100) : 0;
  return (
    <div className="bar mt-2" aria-hidden="true">
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}
