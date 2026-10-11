"""Tools exposed to the assistant. One list drives /api/agent/* and mcp_server.py."""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from .hoard_link.agentkit import AppError, Empty, Tool, ann, call_tool as run_tool, tool_catalog as catalog_of
from .queries import citation
from .services import Services

AGENT_INSTRUCTIONS = """Borges's Hoard is the user's own library: folders of their documents (PDF, DOCX, Markdown, text, EPUB, HTML) indexed locally with exact page and section references.
Answer only from retrieved text. Start with library_search; then call library_read on the best hits to read the full passage before quoting or summarising it.
Always cite: give the document title or file name plus the page or section (the `citation` field is ready to paste). A snippet is an excerpt, not the whole argument.
Never claim a document says something that is not in the text you retrieved. If nothing relevant comes back, say that the library has no passage about it, and suggest which collection or words to try.
Prefer mode hybrid; use mode bm25 for exact words, names or codes, and dense for paraphrased ideas. Filter by collection when the user names one.
A document with needs_ocr is a scanned PDF without text: it is listed but cannot be searched; say so if it looks relevant.
library_add_collection and library_reindex change the index; call them only when the user asks. Indexing runs in the background: library_status shows the queue.
The library can also hold the user's own past Faustus conversations (a `faustus` source), indexed as one document per chat. For questions about past conversations or decisions ("¿qué dijimos?", "¿qué decidimos?", "¿en qué quedamos?", "what did we decide", "what did we say about"), call library_search with source="faustus" (optionally since/until for a date range), or chats_recent to browse recent chats, then quote the `citation` field verbatim — it already reads like `[chat «Title» · date · turno N]`.
Other apps of the family use `embed_texts` / `embed_status` to embed text with this library's multilingual model (not for answering the user).
Some passages carry generated question keys (2-3 questions the passage answers); a hit found through one has `matched_question`. library_question_keys says which documents have them; library_generate_questions and library_question_keys_settings change them, only when the user asks.
A `links` source indexes the user's saved links from Links Hoard (one document per link, the page text as Links Hoard extracted it). For "what did I save about", "aquel artículo que guardé", "dónde leí", call library_search with source="links"; the citation reads `[enlace «Title» · site]` and the hit carries the `url`."""


class SearchArgs(BaseModel):
    q: str = Field(..., min_length=3, max_length=500, description="What to look for, in the user's words (Spanish or English); at least 3 characters.")
    collection: int | None = Field(None, ge=1, description="Restrict to one collection id (see library_collections).")
    mode: str = Field("hybrid", pattern="^(hybrid|bm25|dense)$", description="hybrid (default), bm25 (exact words) or dense (meaning).")
    limit: int = Field(8, ge=1, le=30)
    source: str | None = Field(None, pattern="^(folder|faustus|links)$", description="Restrict to one source kind: 'faustus' for the user's past conversations, 'links' for saved links (Links Hoard), 'folder' for documents.")
    since: str | None = Field(None, pattern="^\\d{4}-\\d{2}-\\d{2}$", description="ISO date (YYYY-MM-DD): only chats/documents dated on or after this.")
    until: str | None = Field(None, pattern="^\\d{4}-\\d{2}-\\d{2}$", description="ISO date (YYYY-MM-DD): only chats/documents dated on or before this.")
    questions: bool = Field(True, description="Use the generated question keys (when the library has them) as extra rankings; false searches the passage text only.")


class ReadArgs(BaseModel):
    document_id: int = Field(..., ge=1, description="Document id from library_search or library_documents.")
    page: int | None = Field(None, ge=1, description="Physical page number (PDF) or slide number (PPTX).")
    section: int | None = Field(None, ge=1, description="Section/chapter ordinal from the document outline.")
    chunk_id: int | None = Field(None, ge=1, description="A chunk id from library_search: reads the page/section that contains it.")
    max_chars: int = Field(6000, ge=200, le=40000, description="Truncate the returned text to this many characters.")


class DocumentArgs(BaseModel):
    document_id: int = Field(..., ge=1)


class DocumentsArgs(BaseModel):
    collection: int | None = Field(None, ge=1)
    q: str = Field("", max_length=200, description="Filter by title or file name (substring).")
    limit: int = Field(50, ge=1, le=200)
    cursor: int | None = Field(None, ge=1, description="next_cursor from the previous page.")


class SimilarArgs(BaseModel):
    chunk_id: int = Field(..., ge=1, description="Chunk id from library_search.")
    limit: int = Field(8, ge=1, le=30)


class ReindexArgs(BaseModel):
    collection_id: int = Field(..., ge=1)
    force_ocr: bool = Field(False, description="PDFs only: send every page to OCR / vision instead of only the pages without a usable text layer. Slow; only when the text layer is known to be wrong.")


class ChatsRecentArgs(BaseModel):
    limit: int = Field(10, ge=1, le=100)
    project: str | None = Field(None, max_length=200, description="Restrict to one Faustus project/folder name.")


class AddCollectionArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000, description="Absolute folder path on the user's PC; it must exist.")
    name: str = Field("", max_length=200, description="Display name (defaults to the folder name).")
    include: list[str] = Field(default_factory=list, max_length=50, description="Include globs, e.g. ['**/*.pdf']; empty means every supported file.")
    exclude: list[str] | None = Field(None, max_length=50, description="Exclude globs; omit for sensible defaults.")
    watch: bool = Field(False, description="Reindex automatically when files change.")


def _hit(h: dict) -> dict:
    keys = ("chunk_id", "document_id", "collection_id", "collection", "source_kind", "title", "kind", "rel_path",
            "page", "section", "line", "date", "project", "url", "score", "citation", "snippet", "matched_question")
    return {k: h[k] for k in keys if k in h}


def run_search(services: Services, args: SearchArgs) -> dict:
    if args.collection is not None and services.collections.get(args.collection) is None:
        raise LookupError(f"Collection {args.collection} does not exist.")
    result = services.search.search(args.q, args.mode, args.limit, args.collection, source=args.source, since=args.since, until=args.until, questions=args.questions)
    hits = [_hit(h) for h in result["hits"]]
    note = None
    if not hits:
        note = "No passage matches. Try other words, mode bm25 for exact terms, or check library_status: the index may still be building."
    elif not result["dense_available"] and args.mode != "bm25":
        note = "The embedding model is not ready yet; results are keyword-only for now."
    return {"query": args.q, "mode": result["mode"], "hits": hits, "count": len(hits), "took_ms": result["took_ms"], "note": note}


def run_read(services: Services, args: ReadArgs) -> dict:
    doc = services.queries.document_brief(args.document_id)
    if doc is None:
        raise LookupError(f"Document {args.document_id} does not exist.")
    unit_id = None
    if args.chunk_id is not None:
        chunk = services.queries.chunk(args.chunk_id)
        if chunk is None or chunk["document_id"] != args.document_id:
            raise LookupError(f"Chunk {args.chunk_id} does not belong to document {args.document_id}.")
        unit_id = chunk["unit_id"]
    unit = services.queries.unit(args.document_id, page=args.page, section=args.section, unit_id=unit_id)
    if unit is None:
        raise LookupError("That page or section does not exist in this document.")
    text = unit["text"]
    truncated = len(text) > args.max_chars
    page = unit["number"] if unit["kind"] in ("page", "slide") else None
    section_label = unit["title"] or (f"{unit['number']}" if unit["kind"] not in ("page", "slide") else "")
    return {
        "document_id": args.document_id, "title": doc["title"], "kind": doc["kind"], "rel_path": doc["rel_path"],
        "unit": {"id": unit["id"], "kind": unit["kind"], "number": unit["number"], "title": unit["title"], "line_start": unit["line_start"], "total": unit["total"]},
        "citation": citation(doc, page, section_label, unit["line_start"]),
        "text": text[: args.max_chars] + ("…" if truncated else ""),
        "truncated": truncated,
        "prev": unit["prev"], "next": unit["next"],
        "extraction": unit.get("extraction"),
    }


def run_document(services: Services, args: DocumentArgs) -> dict:
    doc = services.queries.document(args.document_id)
    if doc is None:
        raise LookupError(f"Document {args.document_id} does not exist.")
    return doc


def run_documents(services: Services, args: DocumentsArgs) -> dict:
    result = services.queries.list_documents(args.collection, args.q.strip(), args.limit, args.cursor)
    keys = ("id", "collection_id", "title", "kind", "rel_path", "pages", "units", "chunks", "needs_ocr", "page_method_counts", "status", "indexed_at")
    return {"documents": [{k: d[k] for k in keys} for d in result["documents"]], "next_cursor": result["next_cursor"]}


def run_collections(services: Services, _: Empty) -> dict:
    counts = {c["id"]: c for c in services.documents.counts()["collections"]}
    out = []
    for c in services.collections.list_by_kind("folder"):
        stats = counts.get(c.id, {})
        out.append({**c.to_dict(), "documents": stats.get("documents", 0), "chunks": stats.get("chunks", 0), "errors": stats.get("errors", 0)})
    return {"collections": out}


def run_status(services: Services, _: Empty) -> dict:
    s = services.status()
    worker = s["worker"]
    return {"model": s["model"], "counts": s["counts"], "collections": s["collections"], "indexing": worker["busy"], "queue": worker["queued"],
            "current": worker["current"], "chunks_pending_embedding": s["chunks_pending_embedding"], "reindex_needed": s["reindex_needed"],
            "note": f"reindexación necesaria: {s['reindex_needed']} documentos (índice antiguo; se actualizan en segundo plano)" if s["reindex_needed"] else None,
            "watching": s["watching"]}


def run_similar(services: Services, args: SimilarArgs) -> dict:
    hits = [_hit(h) for h in services.search.similar(args.chunk_id, args.limit)]
    return {"chunk_id": args.chunk_id, "hits": hits, "count": len(hits)}


def run_reindex(services: Services, args: ReindexArgs) -> dict:
    queued = services.reindex(args.collection_id, force_ocr=args.force_ocr)
    return {"ok": True, "queued": queued, "force_ocr": args.force_ocr, "note": "Queued." if queued else "Already indexing or queued."}


def run_chats_recent(services: Services, args: ChatsRecentArgs) -> dict:
    chats = services.queries.recent_chats(args.limit, args.project)
    return {"chats": chats, "count": len(chats)}


def run_add_collection(services: Services, args: AddCollectionArgs) -> dict:
    collection = services.add_collection(args.name, args.path, args.include, args.exclude, args.watch, False)
    return {"ok": True, "collection": collection.to_dict(), "note": "Indexing has started in the background; library_status shows progress."}


class QuestionKeysArgs(BaseModel):
    document_id: int | None = Field(None, ge=1, description="Restrict the report to one document.")
    collection_id: int | None = Field(None, ge=1, description="Restrict the report to one collection.")


class GenerateQuestionsArgs(BaseModel):
    document_id: int | None = Field(None, ge=1, description="Document to (re)generate the question keys for.")
    collection_id: int | None = Field(None, ge=1, description="Or a whole collection.")
    force: bool = Field(False, description="Regenerate chunks that already have keys; by default only chunks without keys (or whose earlier try found no model) are processed.")
    max_chunks: int = Field(50, ge=1, le=5000, description="At most this many chunks per call (each is one model call); `remaining` says how many are left.")


class QuestionKeysSettingsArgs(BaseModel):
    enabled: bool | None = Field(None, description="Generate question keys while indexing (default off). Omit every field to just read the setting.")
    base_url: str | None = Field(None, max_length=500, description="OpenAI-compatible endpoint of the local model (default the local Ollama, http://127.0.0.1:11434/v1).")
    model: str | None = Field(None, max_length=200, description="Model name to ask for the questions; a small local one is enough.")
    api_key: str | None = Field(None, max_length=500)


def run_question_keys(services: Services, args: QuestionKeysArgs) -> dict:
    if args.document_id is not None and services.queries.document_brief(args.document_id) is None:
        raise LookupError(f"Document {args.document_id} does not exist.")
    if args.collection_id is not None and services.collections.get(args.collection_id) is None:
        raise LookupError(f"Collection {args.collection_id} does not exist.")
    status = services.question_keys.status(args.document_id, args.collection_id)
    note = None
    if not status["keys_exist"]:
        note = "No question keys yet. library_generate_questions creates them for a document; library_question_keys_settings turns them on while indexing."
    elif status["chunks_unavailable"]:
        note = "Some chunks were processed while the question model was unreachable; library_generate_questions retries them."
    return {**status, "note": note}


def run_generate_questions(services: Services, args: GenerateQuestionsArgs) -> dict:
    if args.document_id is None and args.collection_id is None:
        raise ValueError("Give a document_id or a collection_id.")
    if args.document_id is not None and services.queries.document_brief(args.document_id) is None:
        raise LookupError(f"Document {args.document_id} does not exist.")
    if args.collection_id is not None and services.collections.get(args.collection_id) is None:
        raise LookupError(f"Collection {args.collection_id} does not exist.")
    result = services.question_keys.generate(args.document_id, args.collection_id, args.force, args.max_chunks)
    result["note"] = (None if result["model_available"] else
                      "The question model could not be reached; the chunks are marked unavailable and nothing was lost. Check library_question_keys_settings and try again.")
    return result


def run_question_keys_settings(services: Services, args: QuestionKeysSettingsArgs) -> dict:
    patch = args.model_dump(exclude_none=True)
    return {"settings": services.question_keys.update_settings(patch) if patch else services.question_keys.settings(), "changed": sorted(patch)}


# ---------- family service: text embeddings (the owner of `fam_embed`; see hoard_link docs/commons/services.md section 6) ----------
EMBED_MAX_TEXTS = 512
EMBED_TEXT_CHARS = 4000  # each text is cut here before the model sees it (the model reads far fewer tokens anyway)
EMBED_WAIT_S = 100.0  # how long a call waits for a model that is still loading or downloading
FAMILY_TOOLS = frozenset({"embed_texts", "embed_status"})  # answers for programs: never cut to the assistant's size budget


class EmbedTextsArgs(BaseModel):
    texts: list[str] = Field(..., min_length=1, description=f"1 to {EMBED_MAX_TEXTS} texts; each is cut to {EMBED_TEXT_CHARS} characters.")
    kind: str = Field("document", pattern="^(document|query)$", description="document (default) or query: how each text is embedded.")
    normalize: bool = Field(True, description="Unit-length vectors. This model's vectors always are, and `normalized` says so.")


def run_embed_texts(services: Services, args: EmbedTextsArgs) -> dict:
    if len(args.texts) > EMBED_MAX_TEXTS:
        raise AppError("too_many_texts", f"At most {EMBED_MAX_TEXTS} texts per call (got {len(args.texts)}).", status=400,
                       hint="Send the texts in batches.")
    embedder = services.embedder
    if embedder.name == "none":
        raise AppError("embeddings_disabled", "Embeddings are disabled in this app (BORGES_EMBED=none).", status=400)
    if not services.wait_for_embedder(EMBED_WAIT_S):
        state = embedder.status()
        if state.get("state") == "error":
            raise AppError("embedder_error", f"The embedding model could not be loaded: {state.get('error')}", status=503)
        raise AppError("embedder_loading", "The embedding model is still loading or downloading; try again in a minute.", status=503,
                       hint="library_status or embed_status show the state.")
    texts = [t[:EMBED_TEXT_CHARS] for t in args.texts]
    if args.kind == "query":
        matrix = np.stack([embedder.embed_query(t) for t in texts])
    else:
        matrix = embedder.embed_documents(texts)
    return {"model": embedder.model, "dim": int(matrix.shape[1]), "vectors": matrix.astype("float32").tolist(), "normalized": True}


def run_embed_status(services: Services, _: Empty) -> dict:
    status = services.embedder.status()
    state = "loading" if status.get("state") == "not_loaded" else status.get("state")
    return {**status, "state": state, "ready": services.embedder.ready()}


TOOLS: list[Tool] = [
    Tool("library_search", "Search your documents, past chats and saved links; exact citation, snippet. Busca documentos, chats, enlaces.\nHybrid BM25 + multilingual embeddings. Filter source='faustus' for chats, 'links' for saved links, since/until for a date range; citations read «Title», p. 12 / file.md § Section / [chat «Title» · date · turno N] / [enlace «Title» · site].\nSinónimos: buscar, dónde leí, dónde escribí, qué dijimos, qué decidimos, apuntes, mis documentos, mis chats, conversaciones, cita, página, biblioteca, libro, tesis, notas, artículo.", SearchArgs, ann(True), run_search),
    Tool("library_read", "Read the exact text of a page or section before quoting it. Keywords: leer página, cita textual, qué dice.\nRead the exact text of one page or section of a document (by page, section ordinal or a chunk_id from library_search), with prev/next pointers. Use it before quoting so the quote is verbatim.\nSinónimos: leer, página, sección, capítulo, pasaje, texto completo, cita textual, qué dice, manuscrito, libro, apuntes.", ReadArgs, ann(True), run_read),
    Tool("library_document", "Metadata and outline (pages or sections with titles) of one document.\nFor a PDF it also gives page_methods: per page how the text was read (text_layer, ocr, vision or empty) and why.\nSinónimos: documento, índice, esquema, capítulos, páginas, libro, tesis, ficha.", DocumentArgs, ann(True), run_document),
    Tool("library_documents", "List indexed documents, optionally filtered by collection or by title/file name. Paginated with next_cursor.\nSinónimos: mis documentos, lista, biblioteca, archivos, libros, PDF, qué tengo, apuntes.", DocumentsArgs, ann(True), run_documents),
    Tool("library_collections", "The folders (collections) that make up the library, with document and chunk counts.\nSinónimos: colecciones, carpetas, biblioteca, dónde están mis documentos.", Empty, ann(True), run_collections),
    Tool("library_status", "Index health: embedding model state, counts, what is being indexed and the queue.\nSinónimos: estado, está indexando, modelo, cuántos documentos, progreso, biblioteca.", Empty, ann(True), run_status),
    Tool("library_similar", "Passages similar in meaning to a chunk, across the library. Keywords: parecido, dónde más, relacionado.\nPassages similar in meaning to a given chunk (from library_search), across the whole library. Good for 'where else did I write about this'.\nSinónimos: parecido, similar, relacionado, dónde más, otros pasajes, misma idea.", SimilarArgs, ann(True), run_similar),
    Tool("library_reindex", "Reindex one collection (only new or changed files; only when asked). Keywords: reindexar, actualizar índice.\nQueue a non-destructive reindex of one collection: only new or changed files are re-extracted; deleted files are purged. force_ocr=true re-reads every PDF page with OCR / vision instead of only the pages without a usable text layer. Only when the user asks.\nSinónimos: reindexar, actualizar, volver a indexar, refrescar, carpeta, colección.", ReindexArgs, ann(False, False, True), run_reindex),
    Tool("library_add_collection", "Add a folder to the library and index it (only when asked). Keywords: añadir carpeta, indexar, biblioteca.\nAdd a folder to the library (write). The path must exist on the user's PC; adding the same folder twice returns the existing collection without reindexing. Indexing of a new folder starts in the background. Only when the user asks.\nSinónimos: añadir carpeta, nueva colección, indexar carpeta, agregar documentos, biblioteca.", AddCollectionArgs, ann(False, False, True), run_add_collection),
    Tool("chats_recent", "Recently indexed Faustus chats (title, date, project). Chats recientes indexados de Faustus.\nNewest first; use to browse before searching, or for 'what have we talked about lately'.\nSinónimos: chats recientes, conversaciones recientes, últimas charlas, qué hemos hablado, historial reciente.", ChatsRecentArgs, ann(True), run_chats_recent),
    Tool("library_question_keys", "Do passages have generated question keys? Per-document report. Claves de pregunta del índice.\nCounts of chunks with keys, without keys (empty, model unavailable, never processed) and questions, for the library, one collection or one document; whether the question model answers; whether question keys are on.\nSinónimos: preguntas generadas, claves de pregunta, índice de preguntas, qué preguntas responde, cobertura, estado del índice.", QuestionKeysArgs, ann(True), run_question_keys),
    Tool("library_generate_questions", "Generate or regenerate the question keys of a document or collection (write). Generar preguntas por fragmento.\nA small local model writes 2-3 questions per passage; they become extra search keys that point to the passage. Skips silently and marks the chunks when the model is unreachable. At most max_chunks per call. Only when the user asks.\nSinónimos: generar preguntas, regenerar claves, preguntas por fragmento, mejorar la búsqueda, indexar preguntas.", GenerateQuestionsArgs, ann(False, False, True), run_generate_questions),
    Tool("library_question_keys_settings", "Read or change the question-keys setting: on/off while indexing, local model and endpoint (write).\nOff by default. Omit all fields to read. Sinónimos: activar preguntas generadas, modelo de preguntas, configuración, claves de pregunta.", QuestionKeysSettingsArgs, ann(False, False, True), run_question_keys_settings),
    Tool("embed_texts", "Embed texts into vectors with the family's multilingual model. Keywords: embeddings, vectores.\nTurn 1 to 512 texts into unit-length vectors (document or query side) with Borges's multilingual MiniLM model, the one embedding space of the family; the answer says `model` and `dim`: store both next to the vectors and re-embed when they change. For programs, not for reading.\nSinónimos: embeddings, vectores, incrustaciones, representar texto, similitud semántica, búsqueda semántica, modelo de lenguaje.", EmbedTextsArgs, ann(True), run_embed_texts),
    Tool("embed_status", "State of the embedding model: ready, loading, downloading or disabled. Keywords: modelo de embeddings, listo.\nWhether `embed_texts` can answer now: backend, model, dimension, state (disabled, loading, downloading, ready, error), error and load time. Check before indexing a lot of text.\nSinónimos: estado del modelo, modelo de embeddings, está listo, cargando, descargando, dimensión, vectores.", Empty, ann(True), run_embed_status),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def tool_catalog() -> list[dict]:
    return catalog_of(TOOLS)


def call_tool(services: Services, name: str, arguments: dict | None, *, cap: bool = True) -> Any:
    """Run one tool through the shared agent kit (argument validation, result cap). An expected "not found"
    (`LookupError`) keeps the 404 it always had; a bare `ValueError` is a 400 `invalid`."""
    try:
        return run_tool(TOOLS_BY_NAME, services, name, arguments, cap=cap and name not in FAMILY_TOOLS)
    except LookupError as error:
        if isinstance(error, KeyError):  # an unknown tool: the router answers 404 unknown_tool itself
            raise
        raise AppError("not_found", str(error), status=404) from error
