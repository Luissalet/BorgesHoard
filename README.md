# Borges's Hoard

Your personal library, indexed on your own PC. Point it at folders of documents (PDF, DOCX, PPTX, Markdown, TXT, EPUB, HTML, CSV) and ask "where did I read/write about X": every answer comes with an exact citation — `«Title», p. 12` or `notes.md § Section` — and the full passage to read. An assistant reaches the same index through MCP, so it can quote your documents instead of guessing.

Everything stays on the machine: SQLite for text and vectors, a small multilingual embedding model that runs on the CPU, no accounts, no network beyond the one-time model download.

Part of the Hoard family (see `faustus-plugin.json`).

## What it does

- **Collections** = folders you choose, with include/exclude globs, enable/disable, optional folder watching (reindex on change) and optional code files.
- **Extraction** per format: PDF page by page (page numbers kept; scanned pages are read by the family's OCR service (Kafka's Hoard through the hub, `fam_docs.ocr_pdf` from Hoard Link; only the pages without a text layer, `BORGES_OCR=0` turns it off) and, when that service is not running, the PDF is listed and flagged `needs_ocr` and is read at a later scan once the service is up), DOCX headings → sections (tables appended as rows, list items keep their dash), Markdown headings → sections with line numbers, TXT (encoding by BOM, UTF-8, Windows-1252, Latin-1), EPUB chapter by chapter (the table-of-contents page is not a chapter), HTML by h1–h3 (scripts/styles/nav dropped), CSV first 200 rows. DOCX, HTML and text readers, text cleaning (words split by a hyphen at a line end are rejoined, one-word-per-line exports unstacked), chunking, citations, search words and vector ranking are the shared Hoard Link commons (`borges/hoard_link/`), not code of this app.
- **Per-page PDF reading**: each page is judged on its own. A page with a usable text layer keeps PyMuPDF's text and never reaches OCR; only the pages without one go to the family OCR / vision service (`fam_docs.ocr_pdf`, just those pages). A text layer is usable when, after dropping page numbers and headers/footers that repeat on at least half of the pages (3+ page PDFs), the page keeps at least 25 letters/digits, at least 3 word-like tokens, at least half of its tokens are words or numbers and no more than 15% are replacement/control/private-use characters. Per page the index records `method` = `text_layer` | `ocr` | `vision` | `empty`, a `reason` (`usable_text_layer`, `no_text_layer`, `only_headers_or_page_numbers`, `too_little_text`, `garbage_text`, `too_few_words`, `not_word_like`, `forced`) and, when nothing was read, an `ocr_note` (`service_unavailable`, `ocr_disabled`, `failed`, `page_limit`, `no_text_found`). It is in the document metadata (`meta.pdf_page_methods`), in `GET /api/documents/{id}` (`page_methods`, `ocr_forced`), in the document list (`page_method_counts`), in `GET /api/documents/{id}/text?page=N` (`extraction`) and in the MCP tools `library_document`, `library_documents` and `library_read`. `vision` is reported when the OCR service names a vision backend. To force OCR / vision on every page of a collection: `POST /api/collections/{id}/reindex?force_ocr=true` or `library_reindex` with `force_ocr=true` (re-extracts unchanged PDFs too; a page where forced OCR reads nothing keeps its text layer). Non-PDF formats are unchanged.
- **Chunking** with overlap (~900 chars, 150 overlap) that never crosses a page/section boundary; every chunk remembers page, section title and line. A page/section shorter than ~200 characters (a "## Pendiente" stub, a title page) is merged into the following one (or the previous one at the end) keeping the larger part's metadata, and no chunk under 120 characters is emitted unless it is the whole document. Chunking rules carry an `index_version` per document: when the rules change, the next reindex re-chunks only the stale documents, the worker queues it at startup, and status/UI say "reindexación necesaria: N documentos" until done.
- **Incremental indexing**: size+mtime check, then SHA-256; only changed files are re-extracted; deleted files are purged. Runs in a background worker with a queue and live progress (files done/total, current file, per-file errors).
- **Hybrid search**: SQLite FTS5 BM25 (diacritics-insensitive, stopwords dropped, terms of 3+ letters match as a prefix of their light Spanish stem, shorter terms whole-word only) ∪ dense cosine over float32 vectors in SQLite → Reciprocal Rank Fusion. Modes `hybrid | bm25 | dense`. Queries under 3 characters are refused (400). One hit per page/section; further hits from the same section come back in `also` (UI: "ver más"). Responses carry `took_ms`. A reranker hook is left in `Search(reranker=...)`.
- **Embeddings**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX, quantized, 384 dims, ~240 MB on disk, ~50 languages, cross-lingual) through `fastembed`, downloaded on first use into `data/models`. Until it is ready search is keyword-only and the UI says so. Set `BORGES_EMBED=none` to disable dense search entirely.
- **UI (Spanish)**: Buscar (big search box, mode toggle, collection filter, source filter — documents / Faustus chats / both —, results with citation + highlighted snippet + a date chip on chat hits, passage side panel with prev/next page and "similar passages"), Biblioteca (documents by collection, type, size, pages, indexed date, OCR badge; document view with outline and page text), Colecciones (add folder, globs, switches, reindex, progress, errors), Fuentes (connect a Faustus workspace: URL, token or user/password, project filter, poll interval, sync now, status), Estado (model, counts, queue, disk).
- **Links source**: the links saved in Links Hoard, through the Hoard Hub (no URL, no token), each one a citable document `[enlace «Title» · site]`. See "The links source" below.
- **Faustus source**: point Borges at your own Faustus workspace (`http://127.0.0.1:7000` by default) and it indexes your past conversations so the assistant can cite what was decided in an earlier chat. See "The Faustus source" below.

## Question keys (optional, off by default)

At index time a small local model can write 2-3 questions each chunk answers ("how much does it cost to repair the telescope mirrors?"). They are stored as extra searchable keys that point to the chunk: BM25 over the questions (FTS5 table `questions_fts`) and, when the embedding model is loaded, cosine over their vectors (`question_embeddings`). In `library_search` / `GET /api/search` both legs join the rank fusion as two more rankings (weight 1.25 against 1.0 for the text legs), keyed on the chunk id, so a chunk found by its text and by its questions is one hit; a hit that surfaced through a key carries `matched_question`. `questions=false` searches the text only. Keys are used whenever they exist, in `hybrid`, `bm25` and `dense` modes.

- The question model is any OpenAI-compatible chat endpoint (`<base_url>/chat/completions`): the default endpoint is a local Ollama (`http://127.0.0.1:11434/v1`), the model name has to be set. Setting: `PUT /api/question-keys/settings` / `library_question_keys_settings` (`enabled`, `base_url`, `model`, `api_key`; the key comes back masked). With `enabled` on, every index run generates keys for the chunks that lack them, after embedding; with it off (the default) nothing is generated and nothing changes.
- The model being down never fails or slows an index run beyond one probe: the chunks are marked `unavailable` (`chunk_question_state`) and retried on the next run or on demand; three consecutive failures stop the pass. Chunks shorter than 60 characters or with an unusable answer are marked `empty`. A chunk that is re-extracted loses its keys and is `pending` again.
- Report: `GET /api/question-keys[?document=&collection=&probe=]` / `library_question_keys` (chunks with keys, empty, unavailable, pending; questions; questions waiting for a vector; whether the model answers). `GET /api/status` carries the same report under `question_keys` (without probing the model).
- (Re)generate: `POST /api/question-keys/generate {document_id | collection_id, force?, max_chunks?}` / `library_generate_questions` (works with the setting off; `force` replaces existing keys; `max_chunks` caps the model calls per request and `remaining` says how many are left). `GET /api/question-keys/chunk/{id}` lists a chunk's questions.
- Offline eval: `python scripts/eval_question_keys.py` indexes a small corpus with a scripted generator and prints recall@1 of question-shaped queries with and without the keys (currently 0.60 plain, 1.00 with keys on that corpus; `tests/test_question_keys.py` asserts it). It measures the mechanism, not a real model: the recall of a real model on a real library is not measured yet, which is why the setting is not on by default.

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
| `BORGES_OCR` | `1` | `0` never sends scanned PDFs to the family OCR service. |
| `BORGES_OCR_LANG` | `es` | Language asked of the OCR engine. |
| `BORGES_WATCH` | `1` | `0` disables folder watching. |
| `BORGES_AUTOSTART` | `1` | `0` skips model preload and the initial reindex at startup. |
| `BORGES_QUESTIONS_URL` / `BORGES_QUESTIONS_MODEL` / `BORGES_QUESTIONS_KEY` | local Ollama `http://127.0.0.1:11434/v1` / none / none | Defaults for the question keys model (below) when the setting stored in the database is empty. |

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

## The links source (Links Hoard)

The saved links of Links Hoard can be indexed the same way. Add a source of kind `links` from **Fuentes**
(or `POST /api/sources/links`): which links (`state`: `all`, `unread`, `read`, `archived`), an optional
`tag`, and the poll interval (30 minutes by default). No URL and no token: the sync goes through the Hoard
Hub's proxy (`POST <hub>/api/apps/links/call` with Borges's own token, i.e. `family.call("links", ...)`),
so Borges never reads another app's token file. Without a hub, `base_url` + `token` reach Links Hoard's
`/api/agent/call` directly.

One link becomes one document (`kind: "link"`) with a header unit (title, site, byline, URL, tags, the
user's note, the excerpt) and the page text as Links Hoard extracted it (`read_link`, paged). A link whose
page never fetched is still indexed from its title, excerpt and note. Its citation reads
`[enlace «Title» · site]` and every hit carries the `url`. A link is re-read only when its fingerprint
(title, word count, fetch status, note, tags, URL, excerpt) changed; a link deleted in Links Hoard leaves
the index at the next sync. Each sync with changes emits `borges.source.synced` on the family bus.

## API

All JSON; errors are `{ "error": "..." }` (plus a machine-readable `code`, and `issues` for bad input). Tool answers are capped at about 20 KB, except the family embedding tools. The request guard, the error envelope, the PWA files, the single-page-app server (`index.html` is never cached), the MCP bridge, the agent routes and the database class are the shared Hoard Link commons, not code of this app. A second `python -m borges` reports the running instance and exits without touching the token or the database; the token in `data/mcp-token` is created once and stays.

Adding a folder (`POST /api/collections`, `library_add_collection`) accepts a pasted path with its quotes and refuses a drive root, the user profile folder, system folders, configuration or secret folders (`.ssh`, `AppData`, ...) and Borges's own data folder.

- `GET /api/health` → `{ service: "borges-hoard", version, dataDirConfigured }`
- `GET /api/status` → model state, counts (documents, chunks, errors, needs_ocr), per-collection counts, worker queue and progress, disk
- `GET/POST /api/collections`, `GET/PATCH/DELETE /api/collections/{id}`, `POST /api/collections/{id}/reindex[?force_ocr=true]`, `GET /api/collections/{id}/progress`
- `GET /api/documents?collection&q&status&limit&cursor` (cursor = last id), `GET /api/documents/{id}` (metadata + outline), `GET /api/documents/{id}/text?page=|section=|unit=` (text with prev/next)
- `GET /api/search?q&collection&mode=hybrid|bm25|dense&limit&source=folder|faustus|links&since=&until=` → `took_ms` and hits with `citation`, `snippet` (with `<mark>`), `chunk_id`, `document_id`, `page`, `section`, `line`, `date`, `project`, `source_kind`, `score`, `also`/`more` (collapsed hits from the same section); `q` must have 3+ characters; `since`/`until` are `YYYY-MM-DD` and only constrain dated (chat) hits
  (`&questions=false` leaves the generated question keys out; hits found through one carry `matched_question`)
- `GET /api/similar/{chunk_id}`, `GET /api/chunks/{chunk_id}`
- `GET /api/question-keys`, `GET/PUT /api/question-keys/settings`, `POST /api/question-keys/generate`, `GET /api/question-keys/chunk/{chunk_id}` — the question keys (see above)
- `POST /api/sources/links` — a Links Hoard source (`name`, `state`, `tag`, `poll_minutes`, and only without a hub `base_url` + `token`); `GET /api/sources?kind=faustus|links`
- `GET/POST /api/sources`, `GET/PATCH/DELETE /api/sources/{id}`, `POST /api/sources/{id}/sync`, `GET /api/sources/{id}/status`, `GET /api/sources/faustus/recent-chats?limit&project` — the Faustus source (see above); config secrets come back masked
- `GET /api/agent/tools` (catalog + instructions), `POST /api/agent/call` (Bearer token from `data/mcp-token`)

## MCP tools

`mcp_server.py` is a stdio bridge: it fetches the tool list from the running app and proxies every call to `POST /api/agent/call` with the token from `<DATA_DIR>/mcp-token`. It never opens the database. Env: `BORGES_URL`, `BORGES_TOKEN_FILE` (or `BORGES_TOKEN`).

| Tool | What it does |
| --- | --- |
| `library_search` | Hybrid search → hits with citation, snippet, chunk_id, document_id (q, collection?, mode?, limit, source? = `folder`\|`faustus`\|`links`, since?, until?). |
| `library_read` | Exact text of a page / section / the unit containing a chunk, with prev/next (document_id, page? \| section? \| chunk_id?, max_chars). |
| `library_document` | Metadata and outline of one document (PDFs: per-page reading method). |
| `library_documents` | List documents (collection?, q?, cursor). |
| `library_collections` | Collections with counts. |
| `library_status` | Model state, counts, queue. |
| `library_similar` | Passages similar in meaning to a chunk. |
| `library_reindex` | Queue a non-destructive reindex of a collection (write); `force_ocr` re-reads every PDF page with OCR / vision. |
| `library_add_collection` | Add a folder that must exist; idempotent by path (write). |
| `chats_recent` | Most recently indexed Faustus conversations — title, date, project (limit?, project?). |
| `library_question_keys` | Whether chunks have generated question keys: counts by state, questions, model reachable (document_id?, collection_id?). |
| `library_generate_questions` | Generate or regenerate the question keys of a document or collection (write); skips and marks the chunks when the model is down (document_id \| collection_id, force?, max_chunks?). |
| `library_question_keys_settings` | Read or change the question-keys setting: enabled, base_url, model, api_key (write). |
| `embed_texts` | Family service: 1 to 512 texts (texts, kind? = `document`\|`query`, normalize?) → `{model, dim, vectors, normalized}`; waits up to 100 s for a model that is loading, then 503 `embedder_loading`; 400 `embeddings_disabled` / `too_many_texts`. Not capped. |
| `embed_status` | Family service: `{backend, model, dim, state: disabled\|loading\|downloading\|ready\|error, error, load_seconds, ready}`. |

Borges is the owner of the family's text embeddings: the other apps reach `embed_texts` / `embed_status` through the hub (`fam_embed` in Hoard Link), so the whole family uses one multilingual model and one vector space (`model` and `dim` say which).

The instructions shipped with the tools tell the assistant to answer only from retrieved text, to read the full passage with `library_read` before quoting, to always cite, and to say so when nothing relevant is found — and, for questions about past conversations or decisions ("what did we decide about X?"), to search with `source="faustus"` (or call `chats_recent`) and quote the `[chat «Title» · date · turno N]` citation.

## Faustus assistant

Once the shared Hoard Link launcher is installed, the app shows the family's Faustus panel (a floating button). The conversation, the model and the tools live in Faustus; this app only tells the panel what is open.

- **Context per view.** The panel receives the page (`search`, `library`, `collections`, `sources`, `status`) and the objects open in it, with real ids: a `document` (title, kind, collection, relative path, pages, units, chunks, status, indexing date; in Library also the unit shown) and a `passage` (citation, document id, page, section, line, and a text preview of 240 characters). The collection chosen as a search filter counts too. Document text is never sent beyond that preview. Creation views give no object.
- **Unsaved fields.** The panel asks for them with the `borges:context-request` window event. The forms `search-form`, `library-filter`, `collection-form`, `faustus-source-form` and `links-source-form` answer with the fields that differ from what they started with. A token or password typed in a source form makes it count as unsaved, but the secret itself never leaves the page.
- **Refresh.** When the assistant finishes a turn the open view reloads in place (`hoard:copilot-turn-end` / `window.hoardCopilot.onTurnEnd`).
- **Saving.** Forms send only the fields that changed. `PATCH /api/collections/{id}` and `PATCH /api/sources/{id}` merge only the fields sent (source settings merge by key and secrets are kept; `[]` or an empty value clears one). The collection patch now reads, merges and writes under one connection lock, so the user's form and an agent's tool call both survive.
- **Tests.** `node --test client/src/copilot-context.test.mjs` and `python -m pytest tests/test_partial_updates.py`.

## Tests

```bat
venv\Scripts\python -m pytest -q          # fake embedder, no network
venv\Scripts\python -m pytest -m model    # downloads/loads the real model, checks a Spanish query
```

Covers extraction per format (fixtures generated with PyMuPDF, python-docx, ebooklib), the scanned-PDF OCR routing against a fake family service, the folder policy, the family embedding tools, the plumbing, chunking, incremental reindex, FTS + snippets, hybrid fusion, API via TestClient, agent auth, the folder watcher, the Faustus source against a fake Faustus ASGI app (login, token auth, loopback bypass, change detection, deletions, citations, source/date filters, `/api/sources` CRUD), the links source against a fake Links Hoard (paging, fingerprints, unfetched pages, filters, REST), the question keys (scripted generator, model down and recovering, force, reindex, API, MCP tools, offline recall@1 eval), and a subprocess end-to-end test that boots the app and talks to it through the MCP stdio bridge.

## Limits (v1)

- OCR is Kafka's: without the hub and Kafka's Hoard running, scanned PDFs are listed with `needs_ocr` and cannot be searched until a later scan finds the service.
- DOCX tables are flattened to `cell | cell` rows; images and footnotes are ignored.
- Dense search keeps all vectors in memory (384 floats per chunk: ~150 MB per 100k chunks) and rebuilds the matrix after indexing changes.
- The reranker stage is a hook, not an implementation.
- Question keys are off by default and their gain on real libraries with a real model is not measured; there is no UI for them yet (API and MCP only), and the model has to be named by the user.
- Faustus chat turns go through the same short-unit merge rule as everything else: a run of very short
  turns (< ~200 characters each) is merged into one section, so `turno N` in a citation is the section that
  ended up holding that passage, not necessarily every individual message's own number.
- The Faustus poller re-syncs on a timer and on demand; it does not react to a Faustus webhook/push (there
  is none), so a conversation edited seconds ago may lag by up to `poll_minutes` until "Sincronizar ahora"
  or the next scheduled poll picks it up.

## License

MIT — Luissalet.

## PowerPoint slides

PPTX files use the existing Hoard Link reader, with no extra dependency. Text boxes, tables and speaker notes are indexed per slide, in presentation order. Empty slides keep their physical position; short slides are never merged. Citations name the file and slide (`«deck.pptx», diap. 3`). `library_read` accepts that slide number through `page`, or the returned `chunk_id`. Images and charts are not OCR'd; old binary `.ppt` files are not supported. The chunk version is now 4, so the next scan rebuilds indexes made with older boundaries.

The slide and relationship model follows the [PresentationML structure](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document).
