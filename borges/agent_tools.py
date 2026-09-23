"""Tools exposed to the assistant. One list drives /api/agent/* and mcp_server.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field

from .queries import citation
from .services import Services

AGENT_INSTRUCTIONS = """Borges's Hoard is the user's own library: folders of their documents (PDF, DOCX, Markdown, text, EPUB, HTML) indexed locally with exact page and section references.
Answer only from retrieved text. Start with library_search; then call library_read on the best hits to read the full passage before quoting or summarising it.
Always cite: give the document title or file name plus the page or section (the `citation` field is ready to paste). A snippet is an excerpt, not the whole argument.
Never claim a document says something that is not in the text you retrieved. If nothing relevant comes back, say that the library has no passage about it, and suggest which collection or words to try.
Prefer mode hybrid; use mode bm25 for exact words, names or codes, and dense for paraphrased ideas. Filter by collection when the user names one.
A document with needs_ocr is a scanned PDF without text: it is listed but cannot be searched; say so if it looks relevant.
library_add_collection and library_reindex change the index; call them only when the user asks. Indexing runs in the background: library_status shows the queue."""


class Empty(BaseModel):
    pass


class SearchArgs(BaseModel):
    q: str = Field(..., min_length=3, max_length=500, description="What to look for, in the user's words (Spanish or English); at least 3 characters.")
    collection: int | None = Field(None, ge=1, description="Restrict to one collection id (see library_collections).")
    mode: str = Field("hybrid", pattern="^(hybrid|bm25|dense)$", description="hybrid (default), bm25 (exact words) or dense (meaning).")
    limit: int = Field(8, ge=1, le=30)


class ReadArgs(BaseModel):
    document_id: int = Field(..., ge=1, description="Document id from library_search or library_documents.")
    page: int | None = Field(None, ge=1, description="Page number (PDF).")
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


class AddCollectionArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000, description="Absolute folder path on the user's PC; it must exist.")
    name: str = Field("", max_length=200, description="Display name (defaults to the folder name).")
    include: list[str] = Field(default_factory=list, max_length=50, description="Include globs, e.g. ['**/*.pdf']; empty means every supported file.")
    exclude: list[str] | None = Field(None, max_length=50, description="Exclude globs; omit for sensible defaults.")
    watch: bool = Field(False, description="Reindex automatically when files change.")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    annotations: dict[str, bool]
    run: Callable[[Services, Any], Any]


def _hit(h: dict) -> dict:
    keys = ("chunk_id", "document_id", "collection_id", "collection", "title", "kind", "rel_path", "page", "section", "line", "score", "citation", "snippet")
    return {k: h[k] for k in keys}


def run_search(services: Services, args: SearchArgs) -> dict:
    if args.collection is not None and services.collections.get(args.collection) is None:
        raise LookupError(f"Collection {args.collection} does not exist.")
    result = services.search.search(args.q, args.mode, args.limit, args.collection)
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
    page = unit["number"] if unit["kind"] == "page" else None
    section_label = unit["title"] or (f"{unit['number']}" if unit["kind"] != "page" else "")
    return {
        "document_id": args.document_id, "title": doc["title"], "kind": doc["kind"], "rel_path": doc["rel_path"],
        "unit": {"id": unit["id"], "kind": unit["kind"], "number": unit["number"], "title": unit["title"], "line_start": unit["line_start"], "total": unit["total"]},
        "citation": citation(doc, page, section_label, unit["line_start"]),
        "text": text[: args.max_chars] + ("…" if truncated else ""),
        "truncated": truncated,
        "prev": unit["prev"], "next": unit["next"],
    }


def run_document(services: Services, args: DocumentArgs) -> dict:
    doc = services.queries.document(args.document_id)
    if doc is None:
        raise LookupError(f"Document {args.document_id} does not exist.")
    return doc


def run_documents(services: Services, args: DocumentsArgs) -> dict:
    result = services.queries.list_documents(args.collection, args.q.strip(), args.limit, args.cursor)
    keys = ("id", "collection_id", "title", "kind", "rel_path", "pages", "units", "chunks", "needs_ocr", "status", "indexed_at")
    return {"documents": [{k: d[k] for k in keys} for d in result["documents"]], "next_cursor": result["next_cursor"]}


def run_collections(services: Services, _: Empty) -> dict:
    counts = {c["id"]: c for c in services.documents.counts()["collections"]}
    out = []
    for c in services.collections.list():
        stats = counts.get(c.id, {})
        out.append({**c.to_dict(), "documents": stats.get("documents", 0), "chunks": stats.get("chunks", 0), "errors": stats.get("errors", 0)})
    return {"collections": out}


def run_status(services: Services, _: Empty) -> dict:
    s = services.status()
    worker = s["worker"]
    return {"model": s["model"], "counts": s["counts"], "collections": s["collections"], "indexing": worker["busy"], "queue": worker["queued"],
            "current": worker["current"], "chunks_pending_embedding": s["chunks_pending_embedding"], "reindex_needed": s["reindex_needed"],
            "note": f"reindexación necesaria: {s['reindex_needed']} documentos (chunking rules changed; they are re-chunked in the background)" if s["reindex_needed"] else None,
            "watching": s["watching"]}


def run_similar(services: Services, args: SimilarArgs) -> dict:
    hits = [_hit(h) for h in services.search.similar(args.chunk_id, args.limit)]
    return {"chunk_id": args.chunk_id, "hits": hits, "count": len(hits)}


def run_reindex(services: Services, args: ReindexArgs) -> dict:
    queued = services.reindex(args.collection_id)
    return {"ok": True, "queued": queued, "note": "Queued." if queued else "Already indexing or queued."}


def run_add_collection(services: Services, args: AddCollectionArgs) -> dict:
    collection = services.add_collection(args.name, args.path, args.include, args.exclude, args.watch, False)
    return {"ok": True, "collection": collection.to_dict(), "note": "Indexing has started in the background; library_status shows progress."}


def _ann(read_only: bool, destructive: bool = False, idempotent: bool | None = None) -> dict[str, bool]:
    return {"readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": read_only if idempotent is None else idempotent, "openWorldHint": False}


TOOLS: list[Tool] = [
    Tool("library_search", "Search the user's own documents (PDF, DOCX, Markdown, text, EPUB, HTML) and get passages with an exact citation («Title», p. 12 or file.md § Section), a highlighted snippet, chunk_id and document_id. Hybrid BM25 + multilingual embeddings by default. First step for 'where did I read/write about X'.\nSinónimos: buscar, dónde leí, dónde escribí, apuntes, mis documentos, buscar en mis PDF, cita, página, manuscrito, capítulo, biblioteca, libro, tesis, máster, notas, artículo.", SearchArgs, _ann(True), run_search),
    Tool("library_read", "Read the exact text of one page or section of a document (by page, section ordinal or a chunk_id from library_search), with prev/next pointers. Use it before quoting so the quote is verbatim.\nSinónimos: leer, página, sección, capítulo, pasaje, texto completo, cita textual, qué dice, manuscrito, libro, apuntes.", ReadArgs, _ann(True), run_read),
    Tool("library_document", "Metadata and outline (pages or sections with titles) of one document.\nSinónimos: documento, índice, esquema, capítulos, páginas, libro, tesis, ficha.", DocumentArgs, _ann(True), run_document),
    Tool("library_documents", "List indexed documents, optionally filtered by collection or by title/file name. Paginated with next_cursor.\nSinónimos: mis documentos, lista, biblioteca, archivos, libros, PDF, qué tengo, apuntes.", DocumentsArgs, _ann(True), run_documents),
    Tool("library_collections", "The folders (collections) that make up the library, with document and chunk counts.\nSinónimos: colecciones, carpetas, biblioteca, dónde están mis documentos.", Empty, _ann(True), run_collections),
    Tool("library_status", "Index health: embedding model state, counts, what is being indexed and the queue.\nSinónimos: estado, está indexando, modelo, cuántos documentos, progreso, biblioteca.", Empty, _ann(True), run_status),
    Tool("library_similar", "Passages similar in meaning to a given chunk (from library_search), across the whole library. Good for 'where else did I write about this'.\nSinónimos: parecido, similar, relacionado, dónde más, otros pasajes, misma idea.", SimilarArgs, _ann(True), run_similar),
    Tool("library_reindex", "Queue a non-destructive reindex of one collection: only new or changed files are re-extracted; deleted files are purged. Only when the user asks.\nSinónimos: reindexar, actualizar, volver a indexar, refrescar, carpeta, colección.", ReindexArgs, _ann(False, False, True), run_reindex),
    Tool("library_add_collection", "Add a folder to the library (write). The path must exist on the user's PC; adding the same folder twice returns the existing collection without reindexing. Indexing of a new folder starts in the background. Only when the user asks.\nSinónimos: añadir carpeta, nueva colección, indexar carpeta, agregar documentos, biblioteca.", AddCollectionArgs, _ann(False, False, True), run_add_collection),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def tool_catalog() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "annotations": t.annotations, "inputSchema": t.input_model.model_json_schema(by_alias=True)}
        for t in TOOLS
    ]


def call_tool(services: Services, name: str, arguments: dict | None) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool: {name}")
    args = tool.input_model.model_validate(arguments or {})
    return tool.run(services, args)
