# Borges's Hoard

Your personal library, indexed on your own PC. Point it at folders of documents (PDF, DOCX, Markdown, TXT, EPUB, HTML, CSV) and ask "where did I read/write about X": every answer comes with an exact citation — `«Title», p. 12` or `notes.md § Section` — and the full passage to read. An assistant reaches the same index through MCP, so it can quote your documents instead of guessing.

Everything stays on the machine: SQLite for text and vectors, a small multilingual embedding model that runs on the CPU, no accounts, no network beyond the one-time model download.

Part of the Hoard family (see `faustus-plugin.json`).

## What it does

- **Collections** = folders you choose, with include/exclude globs, enable/disable, optional folder watching (reindex on change) and optional code files.
- **Extraction** per format: PDF page by page (page numbers kept; scanned PDFs without a text layer are listed and flagged `needs_ocr`, not OCR'd in v1), DOCX headings → sections (tables appended as rows), Markdown headings → sections with line numbers, TXT, EPUB chapter by chapter, HTML by h1–h3 (scripts/styles dropped), CSV first 200 rows.
- **Chunking** with overlap (~900 chars, 150 overlap) that never crosses a page/section boundary; every chunk remembers page, section title and line.
- **Incremental indexing**: size+mtime check, then SHA-256; only changed files are re-extracted; deleted files are purged. Runs in a background worker with a queue and live progress (files done/total, current file, per-file errors).
- **Hybrid search**: SQLite FTS5 BM25 (diacritics-insensitive, stopwords dropped, light Spanish stemming as prefix) ∪ dense cosine over float32 vectors in SQLite → Reciprocal Rank Fusion. Modes `hybrid | bm25 | dense`. A reranker hook is left in `Search(reranker=...)`.
- **Embeddings**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX, quantized, 384 dims, ~240 MB on disk, ~50 languages, cross-lingual) through `fastembed`, downloaded on first use into `data/models`. Until it is ready search is keyword-only and the UI says so. Set `BORGES_EMBED=none` to disable dense search entirely.
- **UI (Spanish)**: Buscar (big search box, mode toggle, collection filter, results with citation + highlighted snippet, passage side panel with prev/next page and "similar passages"), Biblioteca (documents by collection, type, size, pages, indexed date, OCR badge; document view with outline and page text), Colecciones (add folder, globs, switches, reindex, progress, errors), Estado (model, counts, queue, disk).

## Requirements

- Windows 10/11 (also runs on Linux/macOS), Python 3.11+ (3.13 fine), Node 22 only to build the client.
- CPU is enough. For an NVIDIA GPU you can install `fastembed-gpu` and set `BORGES_EMBED_PROVIDERS=CUDAExecutionProvider` (optional extra `gpu`).
- Python's `sqlite3` must have FTS5 (the official Windows builds do). The app fails loudly at startup otherwise.

## Install and run (Windows)

```bat
git clone <this repo> borges-hoard
cd borges-hoard
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install
npm run build
venv\Scripts\python -m borges
```

Open http://127.0.0.1:5184, go to **Colecciones** and add a folder. The first start downloads the embedding model (~240 MB); **Estado** shows its progress.

- `python scripts/launch.py` starts the app on a free port and opens the browser.
- `python scripts/dev.py` runs uvicorn `--reload` + the Vite dev server (proxying `/api`).
- `python scripts/selftest.py <folder>` indexes a folder into a temporary data dir, runs three queries and prints citations and timings (`--fake` skips the model).

## Configuration (environment)

| Variable | Default | Meaning |
| --- | --- | --- |
| `BORGES_PORT` / `PORT` | `5184` | Preferred port; `PORT_STRICT=1` pins it, otherwise the first free port from there. |
| `BORGES_DATA_DIR` | `<repo>/data` | Database (`borges-hoard.db`), `mcp-token`, `models/`. |
| `BORGES_MODELS_DIR` | `<data>/models` | Where the embedding model is cached. |
| `BORGES_EMBED` | `auto` | `auto` (fastembed), `fake` (tests), `none` (BM25 only). |
| `BORGES_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Any fastembed text model. |
| `BORGES_EMBED_PROVIDERS` | | e.g. `CUDAExecutionProvider` with `fastembed-gpu`. |
| `BORGES_WATCH` | `1` | `0` disables folder watching. |
| `BORGES_AUTOSTART` | `1` | `0` skips model preload and the initial reindex at startup. |

The server binds 127.0.0.1 only and rejects non-local `Host`/`Origin` headers.

## API

All JSON; errors are `{ "error": "..." }`.

- `GET /api/health` → `{ service: "borges-hoard", version, dataDirConfigured }`
- `GET /api/status` → model state, counts (documents, chunks, errors, needs_ocr), per-collection counts, worker queue and progress, disk
- `GET/POST /api/collections`, `GET/PATCH/DELETE /api/collections/{id}`, `POST /api/collections/{id}/reindex`, `GET /api/collections/{id}/progress`
- `GET /api/documents?collection&q&status&limit&cursor` (cursor = last id), `GET /api/documents/{id}` (metadata + outline), `GET /api/documents/{id}/text?page=|section=|unit=` (text with prev/next)
- `GET /api/search?q&collection&mode=hybrid|bm25|dense&limit` → hits with `citation`, `snippet` (with `<mark>`), `chunk_id`, `document_id`, `page`, `section`, `line`, `score`
- `GET /api/similar/{chunk_id}`, `GET /api/chunks/{chunk_id}`
- `GET /api/agent/tools` (catalog + instructions), `POST /api/agent/call` (Bearer token from `data/mcp-token`)

## MCP tools

`mcp_server.py` is a stdio bridge: it fetches the tool list from the running app and proxies every call to `POST /api/agent/call` with the token from `<DATA_DIR>/mcp-token`. It never opens the database. Env: `BORGES_URL`, `BORGES_TOKEN_FILE` (or `BORGES_TOKEN`).

| Tool | What it does |
| --- | --- |
| `library_search` | Hybrid search → hits with citation, snippet, chunk_id, document_id (q, collection?, mode?, limit). |
| `library_read` | Exact text of a page / section / the unit containing a chunk, with prev/next (document_id, page? \| section? \| chunk_id?, max_chars). |
| `library_document` | Metadata and outline of one document. |
| `library_documents` | List documents (collection?, q?, cursor). |
| `library_collections` | Collections with counts. |
| `library_status` | Model state, counts, queue. |
| `library_similar` | Passages similar in meaning to a chunk. |
| `library_reindex` | Queue a non-destructive reindex of a collection (write). |
| `library_add_collection` | Add a folder that must exist; idempotent by path (write). |

The instructions shipped with the tools tell the assistant to answer only from retrieved text, to read the full passage with `library_read` before quoting, to always cite, and to say so when nothing relevant is found.

## Tests

```bat
venv\Scripts\python -m pytest -q          # 44 tests, fake embedder, no network
venv\Scripts\python -m pytest -m model    # downloads/loads the real model, checks a Spanish query
```

Covers extraction per format (fixtures generated with PyMuPDF, python-docx, ebooklib), chunking, incremental reindex, FTS + snippets, hybrid fusion, API via TestClient, agent auth, the folder watcher, and a subprocess end-to-end test that boots the app and talks to it through the MCP stdio bridge.

## Limits (v1)

- No OCR: scanned PDFs are listed with `needs_ocr` and cannot be searched.
- DOCX tables are flattened to `cell | cell` rows; images and footnotes are ignored.
- Dense search keeps all vectors in memory (384 floats per chunk: ~150 MB per 100k chunks) and rebuilds the matrix after indexing changes.
- The reranker stage is a hook, not an implementation.

## License

MIT — Luissalet.
