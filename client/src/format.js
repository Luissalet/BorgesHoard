export const KIND_LABEL = { pdf: "PDF", docx: "Word", md: "Markdown", txt: "Texto", epub: "EPUB", html: "HTML", csv: "CSV", code: "Código", chat: "Chat" };

export const PHASE_LABEL = {
  queued: "En cola",
  scanning: "Explorando carpeta",
  extracting: "Extrayendo texto",
  embedding: "Calculando embeddings",
  done: "Al día",
  error: "Error",
  cancelled: "Cancelado",
};

export const MODEL_STATE = {
  not_loaded: "sin cargar",
  downloading: "descargando (~240 MB)",
  loading: "cargando",
  ready: "listo",
  error: "error",
  disabled: "desactivado",
};

export function bytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function when(epoch) {
  if (!epoch) return "—";
  const date = new Date(epoch * 1000);
  return date.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function unitLabel(unit) {
  if (!unit) return "";
  if (unit.kind === "page") return `p. ${unit.number}`;
  if (unit.kind === "chapter") return unit.title ? `cap. ${unit.number} · ${unit.title}` : `cap. ${unit.number}`;
  return unit.title || `§ ${unit.number}`;
}

/** Split plain text into React-safe pieces, wrapping `terms` (folded stems) in <mark>. */
export function fold(text) {
  return text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

export function markTerms(text, terms) {
  if (!terms || !terms.length || !text) return [text];
  const folded = fold(text);
  if (folded.length !== text.length) return [text]; // exotic scripts: skip highlighting rather than misalign
  const ranges = [];
  for (const term of terms) {
    if (!term) continue;
    let index = folded.indexOf(term);
    while (index !== -1) {
      const wordStart = index === 0 || !/[\p{L}\p{N}]/u.test(folded[index - 1]);
      if (wordStart) {
        let end = index + term.length;
        while (end < folded.length && /[\p{L}\p{N}]/u.test(folded[end])) end += 1;
        ranges.push([index, end]);
      }
      index = folded.indexOf(term, index + 1);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const out = [];
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start < cursor) continue;
    out.push(text.slice(cursor, start));
    out.push({ mark: text.slice(start, end) });
    cursor = end;
  }
  out.push(text.slice(cursor));
  return out;
}

const STOP = new Set("de la el los las un una unos unas y o u e en del al a que con por para se su sus lo le les es son no ni como más muy ya mi mis tu tus me te sobre acerca dónde donde qué cuál cuáles cómo cuándo quién hay he ha leí escribí dice dijo este esta esto the of an and or in on to is are was were for with that this about where what which how when who did i my".split(" "));

/** Same light stemming as the server so the passage view highlights what the snippet highlighted. */
export function queryTerms(q) {
  const words = (q.match(/[\p{L}\p{N}_]+/gu) || []).filter((w) => w.length > 1);
  const kept = words.filter((w) => !STOP.has(fold(w)));
  return (kept.length ? kept : words).map((w) => {
    const lower = fold(w);
    if (lower.length < 4) return lower;
    if (lower.length > 5 && lower.endsWith("es")) return lower.slice(0, -2);
    if (lower.length > 4 && lower.endsWith("s")) return lower.slice(0, -1);
    return lower;
  });
}
