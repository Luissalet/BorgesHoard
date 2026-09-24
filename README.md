# Borges's Hoard

Your personal library, indexed on your own PC. Point it at folders of documents (PDF, DOCX, Markdown, TXT, EPUB, HTML, CSV) and ask "where did I read/write about X": every answer comes with an exact citation — `«Title», p. 12` or `notes.md § Section` — and the full passage to read. An assistant reaches the same index through MCP, so it can quote your documents instead of guessing.

Everything stays on the machine: SQLite for text and vectors, a small multilingual embedding model that runs on the CPU, no accounts, no network beyond the one-time model download.

Part of the Hoard family (see `faustus-plugin.json`).

## What it does

- **Collections** = folders you choose, with include/exclude globs, enable/disable, optional folder watching (reindex on change) and optional code files.
- **Extraction** per format: PDF page by page (page numbers kept; scanned PDFs without a text layer are listed and flagged `needs_ocr`, not OCR'd in v1), DOCX headings → sections (tables appended as rows), Markdown headings → sections with line numbers, TXT, EPUB chapter by chapter, HTML by h1–h3 (scripts/styles dropped), CSV first 200 rows.
- **Chunking** with overlap (~900 chars, 150 overlap) that never crosses a page/section boundary; every chunk remembers page, section title and line. A page/section shorter than ~200 characters (a "## Pendiente" stub, a title page) is merged into the following one (or the previous one at the end) keeping the larger part's metadata, and no chunk under 120 characters is emitted unless it is the whole document. Chunking rules carry an `index_version` per document: when the rules change, the next reindex re-chunks only the stale documents, the worker queues it at startup, and status/UI say "reindexación necesaria: N documentos" until done.
- **Incremental indexing**: size+mtime check, then SHA-256; only changed files are re-extracted; deleted files are purged. Runs in a background worker with a queue and live progress (files done/total, current file, per-file errors).
- **Hybrid search**: SQLite FTS5 BM25 (diacritics-insensitive, stopwords dropped, terms of 3+ letters match as a prefix of their light Spanish stem, shorter terms whole-word only) ∪ dense cosine over float32 vectors in SQLite → Reciprocal Rank Fusion. Modes `hybrid | bm25 | dense`. Queries under 3 characters are refused (400). One hit per page/section; further hits from the same section come back in `also` (UI: "ver más"). Responses carry `took_ms`. A reranker hook is left in `Search(reranker=...)`.
- **Embeddings**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX, quantized, 384 dims, ~240 MB on disk, ~50 languages, cross-lingual) through `fastembed`, downloaded on first use into `data/models`. Until it is ready search is keyword-only and the UI says so. Set `BORGES_EMBED=none` to disable dense search entirely.
- **UI (Spanish)**: Buscar (big search box, mode toggle, collection filter, source filter — documents / Faustus chats / both —, results with citation + highlighted snippet + a date chip on chat hits, passage side panel with prev/next page and "similar passages"), Biblioteca (documents by collection, type, size, pages, indexed date, OCR badge; document view with outline and page text), Colecciones (add folder, globs, switches, reindex, progress, errors), Fuentes (connect a Faustus workspace: URL, token or user/password, project filter, poll interval, sync now, status), Estado (model, counts, queue, disk).
- **Faustus source**: point Borges at your own Faustus workspace (`http://127.0.0.1:7000` by default) and it indexes your past conversations so the assistant can cite what was decided in an earlier chat. See "The Faustus source" below.

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
| `BORGES_ALLOWED_HOSTS` | | Extra host names accepted behind a tunnel (see below). |
| `BORGES_MODELS_DIR` | `<data>/models` | Where the embedding model is cached. |
| `BORGES_EMBED` | `auto` | `auto` (fastembed), `fake` (tests), `none` (BM25 only). |
| `BORGES_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Any fastembed text model. |
| `BORGES_EMBED_PROVIDERS` | | e.g. `CUDAExecutionProvider` with `fastembed-gpu`. |
| `BORGES_WATCH` | `1` | `0` disables folder watching. |
| `BORGES_AUTOSTART` | `1` | `0` skips model preload and the initial reindex at startup. |

### Access from your phone (behind a tunnel)

The server binds 127.0.0.1 and only answers requests whose `Host` is `localhost`, `127.0.0.1` or `[::1]`. To reach it from your phone through a tunnel that fronts the app (a private mesh network, a reverse proxy), list the extra host names in `BORGES_ALLOWED_HOSTS`, comma-separated, exact names or `*.suffix`: `BORGES_ALLOWED_HOSTS=my-pc.example,*.ts.net`. Port and letter case are ignored, and the `Origin` of API calls must resolve to one of those hosts too (any scheme or port). Cross-site *fetches* are still refused; opening the app from another page (a link, a bookmarklet, the share sheet) is a normal navigation and works.

Once opened through the tunnel, the browser offers to install it (PWA).

## The Faustus source

Beyond folders, Borges can index the user's own conversations from a running Faustus workspace, so the
assistant can quote what was decided in an earlier chat. Add one from **Fuentes** (or `POST /api/sources`,
below): a `base_url` (Faustus commonly runs at `http://127.0.0.1:7000`, with test instances on
`7001`–`7003`) and either an **API token** (preferred: create one in Faustus with the `sessions` scope,
`POST /api/tokens`, and paste it here) or a **username/password**. A source with no credentials at all
still works if Faustus is running with `LOCALHOST_BYPASS=true` and both apps are on loopback — Borges
tries every call unauthenticated first and only logs in on a 401.

Credentials are kept only in Borges's own local database (`data/borges-hoard.db`, already outside git —
see `.gitignore`), never written to a plain file and never committed; the API and UI always show the
token/password masked. There is no separate `data/sources.json`: the existing per-collection store
already lives under `data/` and is the natural place for this, so a Faustus source's config rides in the
same `collections` row (`kind='faustus'`) as a folder's does.

Endpoints relied on in Faustus (verify these against the live instance — see the integrator notes if the
app's routes have moved since):

- `POST /api/auth/login` — JSON `{username, password, remember}`; sets the `odysseus_session` cookie. Used
  only when no token is configured and a call comes back 401 (i.e. never on an instance with
  `LOCALHOST_BYPASS=true` reachable from loopback).
- `GET /api/sessions` — every non-archived conversation for the authenticated user: `id`, `name`, `folder`
  (used as the "project"), `model`, `created_at`, `updated_at`, `last_message_at`. Polled to detect changes
  (`last_message_at`/`updated_at`) without re-fetching every conversation.
- `GET /api/session/{id}/export?fmt=json` — the full transcript for one conversation, already built by
  Faustus's own `src/chat_export.py` (`build_transcript`, which drops system turns and any turn flagged
  `metadata.hidden`, i.e. tool-approval bookkeeping): `{name, project, messages: [{role, content,
  timestamp, model, tool_calls, attachments}]}`.
- Bearer tokens use `Authorization: Bearer <token>` (a token minted with `POST /api/tokens`, scope
  `sessions`, prefixed `ody_`).

One conversation becomes one document (`kind: "chat"`), titled `Chat: <title> (<yyyy-mm-dd>)`, with one
section per kept user/assistant turn ("turno N"); a tool call's result is kept only when short (≤300
characters), otherwise just its name is noted. The existing chunking/embedding/FTS pipeline indexes it
exactly like a folder document, so citations, `library_similar` and the passage view all work unchanged.
Its citation reads `[chat «Title» · yyyy-mm-dd · turno N]`. A background poller re-syncs every source every
`poll_minutes` (default 10); **Fuentes** also has a "Sincronizar ahora" button, and unchanged conversations
are skipped cheaply by comparing `last_message_at`.

## API

All JSON; errors are `{ "error": "..." }`.

- `GET /api/health` → `{ service: "borges-hoard", version, dataDirConfigured }`
- `GET /api/status` → model state, counts (documents, chunks, errors, needs_ocr), per-collection counts, worker queue and progress, disk
- `GET/POST /api/collections`, `GET/PATCH/DELETE /api/collections/{id}`, `POST /api/collections/{id}/reindex`, `GET /api/collections/{id}/progress`
- `GET /api/documents?collection&q&status&limit&cursor` (cursor = last id), `GET /api/documents/{id}` (metadata + outline), `GET /api/documents/{id}/text?page=|section=|unit=` (text with prev/next)
- `GET /api/search?q&collection&mode=hybrid|bm25|dense&limit&source=folder|faustus&since=&until=` → `took_ms` and hits with `citation`, `snippet` (with `<mark>`), `chunk_id`, `document_id`, `page`, `section`, `line`, `date`, `project`, `source_kind`, `score`, `also`/`more` (collapsed hits from the same section); `q` must have 3+ characters; `since`/`until` are `YYYY-MM-DD` and only constrain dated (chat) hits
- `GET /api/similar/{chunk_id}`, `GET /api/chunks/{chunk_id}`
- `GET/POST /api/sources`, `GET/PATCH/DELETE /api/sources/{id}`, `POST /api/sources/{id}/sync`, `GET /api/sources/{id}/status`, `GET /api/sources/faustus/recent-chats?limit&project` — the Faustus source (see above); config secrets come back masked
- `GET /api/agent/tools` (catalog + instructions), `POST /api/agent/call` (Bearer token from `data/mcp-token`)

## MCP tools

`mcp_server.py` is a stdio bridge: it fetches the tool list from the running app and proxies every call to `POST /api/agent/call` with the token from `<DATA_DIR>/mcp-token`. It never opens the database. Env: `BORGES_URL`, `BORGES_TOKEN_FILE` (or `BORGES_TOKEN`).

| Tool | What it does |
| --- | --- |
| `library_search` | Hybrid search → hits with citation, snippet, chunk_id, document_id (q, collection?, mode?, limit, source? = `folder`\|`faustus`, since?, until?). |
| `library_read` | Exact text of a page / section / the unit containing a chunk, with prev/next (document_id, page? \| section? \| chunk_id?, max_chars). |
| `library_document` | Metadata and outline of one document. |
| `library_documents` | List documents (collection?, q?, cursor). |
| `library_collections` | Collections with counts. |
| `library_status` | Model state, counts, queue. |
| `library_similar` | Passages similar in meaning to a chunk. |
| `library_reindex` | Queue a non-destructive reindex of a collection (write). |
| `library_add_collection` | Add a folder that must exist; idempotent by path (write). |
| `chats_recent` | Most recently indexed Faustus conversations — title, date, project (limit?, project?). |

The instructions shipped with the tools tell the assistant to answer only from retrieved text, to read the full passage with `library_read` before quoting, to always cite, and to say so when nothing relevant is found — and, for questions about past conversations or decisions ("what did we decide about X?"), to search with `source="faustus"` (or call `chats_recent`) and quote the `[chat «Title» · date · turno N]` citation.

## Tests

```bat
venv\Scripts\python -m pytest -q          # fake embedder, no network
venv\Scripts\python -m pytest -m model    # downloads/loads the real model, checks a Spanish query
```

Covers extraction per format (fixtures generated with PyMuPDF, python-docx, ebooklib), chunking, incremental reindex, FTS + snippets, hybrid fusion, API via TestClient, agent auth, the folder watcher, the Faustus source against a fake Faustus ASGI app (login, token auth, loopback bypass, change detection, deletions, citations, source/date filters, `/api/sources` CRUD), and a subprocess end-to-end test that boots the app and talks to it through the MCP stdio bridge.

## Limits (v1)

- No OCR: scanned PDFs are listed with `needs_ocr` and cannot be searched.
- DOCX tables are flattened to `cell | cell` rows; images and footnotes are ignored.
- Dense search keeps all vectors in memory (384 floats per chunk: ~150 MB per 100k chunks) and rebuilds the matrix after indexing changes.
- The reranker stage is a hook, not an implementation.
- Faustus chat turns go through the same short-unit merge rule as everything else: a run of very short
  turns (< ~200 characters each) is merged into one section, so `turno N` in a citation is the section that
  ended up holding that passage, not necessarily every individual message's own number.
- The Faustus poller re-syncs on a timer and on demand; it does not react to a Faustus webhook/push (there
  is none), so a conversation edited seconds ago may lag by up to `poll_minutes` until "Sincronizar ahora"
  or the next scheduled poll picks it up.

## License

MIT — Luissalet.
