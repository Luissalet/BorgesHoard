# Borges's Hoard

Tu biblioteca personal, indexada en tu propio ordenador. Señala carpetas con documentos (PDF, DOCX, Markdown, TXT, EPUB, HTML, CSV) y pregunta «¿dónde leí o escribí sobre X?»: cada respuesta llega con su cita exacta — `«Título», p. 12` o `apuntes.md § Sección` — y el pasaje completo para leerlo. Un asistente accede al mismo índice por MCP, así que puede citar tus documentos en vez de inventárselos.

Todo se queda en tu máquina: SQLite para texto y vectores, un pequeño modelo multilingüe de embeddings que corre en la CPU, sin cuentas y sin red salvo la descarga única del modelo.

## Qué hace

- **Colecciones** = carpetas que eliges, con globs de inclusión/exclusión, activar/desactivar, vigilancia opcional de cambios (reindexa sola) y archivos de código opcionales.
- **Extracción** por formato: PDF página a página (se conserva el número; los PDF escaneados sin capa de texto se listan y se marcan «necesita OCR», sin OCR en esta versión), DOCX con títulos → secciones (las tablas se añaden como filas), Markdown con títulos → secciones y números de línea, TXT, EPUB capítulo a capítulo, HTML por h1–h3, CSV (200 primeras filas).
- **Troceado** con solapamiento (~900 caracteres, 150 de solape) que nunca cruza una página o sección; cada pasaje recuerda página, sección y línea.
- **Indexación incremental**: tamaño+fecha y después SHA-256; solo se vuelve a leer lo que cambia; lo borrado se purga. Corre en segundo plano con cola y progreso (archivos hechos/total, archivo actual, errores por archivo).
- **Búsqueda híbrida**: BM25 de SQLite FTS5 (sin distinguir tildes, sin palabras vacías, plurales como prefijo) ∪ coseno sobre vectores float32 guardados en SQLite → fusión RRF. Modos «Híbrida», «Palabras» y «Significado».
- **Embeddings**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX cuantizado, 384 dimensiones, ~240 MB, ~50 idiomas, funciona entre idiomas) mediante `fastembed`, descargado la primera vez en `data/models`. Hasta que está listo la búsqueda es solo por palabras y la interfaz lo dice.
- **Interfaz**: Buscar, Biblioteca, Colecciones y Estado. Funciona en el móvil (a través de un túnel) con el pasaje a pantalla completa.

## Requisitos

- Windows 10/11 (también Linux/macOS), Python 3.11 o superior (3.13 va bien), Node 22 solo para construir el cliente.
- Con la CPU basta. Si tenéis GPU NVIDIA podéis instalar `fastembed-gpu` y poner `BORGES_EMBED_PROVIDERS=CUDAExecutionProvider`.
- El `sqlite3` de Python debe tener FTS5 (las versiones oficiales de Windows lo traen). Si falta, la aplicación avisa al arrancar.

## Instalar y arrancar (Windows)

```bat
git clone <este repositorio> borges-hoard
cd borges-hoard
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install
npm run build
venv\Scripts\python -m borges
```

Abre http://127.0.0.1:5184, entra en **Colecciones** y añade una carpeta. La primera vez se descarga el modelo (~240 MB); **Estado** muestra cómo va.

- `python scripts/launch.py` arranca en un puerto libre y abre el navegador.
- `python scripts/dev.py` lanza uvicorn con recarga y el servidor de Vite.
- `python scripts/selftest.py <carpeta>` indexa una carpeta en un directorio temporal, lanza tres consultas e imprime citas y tiempos (`--fake` evita el modelo).

## Configuración (variables de entorno)

| Variable | Por defecto | Significado |
| --- | --- | --- |
| `BORGES_PORT` / `PORT` | `5184` | Puerto preferido; `PORT_STRICT=1` lo fija, si no se usa el primero libre. |
| `BORGES_DATA_DIR` | `<repo>/data` | Base de datos, `mcp-token`, `models/`. |
| `BORGES_ALLOWED_HOSTS` | | Nombres de host adicionales aceptados detrás de un túnel (ver más abajo). |
| `BORGES_MODELS_DIR` | `<data>/models` | Dónde se guarda el modelo. |
| `BORGES_EMBED` | `auto` | `auto`, `fake` (pruebas) o `none` (solo palabras). |
| `BORGES_MODEL` | MiniLM multilingüe | Cualquier modelo de texto de fastembed. |
| `BORGES_WATCH` | `1` | `0` desactiva la vigilancia de carpetas. |
| `BORGES_AUTOSTART` | `1` | `0` no precarga el modelo ni reindexa al arrancar. |

### Acceso desde el móvil (a través de un túnel)

El servidor escucha en 127.0.0.1 y solo responde a peticiones cuyo `Host` sea `localhost`, `127.0.0.1` o `[::1]`. Para entrar desde el móvil a través de un túnel que ponga la aplicación delante (una red privada, un proxy inverso), indicad los nombres de host adicionales en `BORGES_ALLOWED_HOSTS`, separados por comas, exactos o `*.sufijo`: `BORGES_ALLOWED_HOSTS=mi-pc.example,*.ts.net`. El puerto y las mayúsculas no importan, y el `Origin` de las llamadas a la API también tiene que corresponder a uno de esos hosts (con cualquier esquema o puerto). Las peticiones *fetch* desde otras webs se siguen rechazando; abrir la aplicación desde otra página (un enlace, un bookmarklet, el menú de compartir) es una navegación normal y funciona.

## Conectar el asistente (MCP)

`mcp_server.py` es un puente stdio: pide la lista de herramientas a la aplicación en marcha y reenvía cada llamada a `POST /api/agent/call` con el token de `data/mcp-token`. Nunca abre la base de datos. Faustus detecta la aplicación por `/api/health` y rellena la conexión con `faustus-plugin.json`.

Herramientas: `library_search`, `library_read`, `library_document`, `library_documents`, `library_collections`, `library_status`, `library_similar`, `library_reindex` y `library_add_collection`. Las instrucciones que acompañan a las herramientas obligan al asistente a responder solo con texto recuperado, a leer el pasaje completo antes de citar, a citar siempre (documento y página o sección) y a decir claramente cuando no encuentra nada.

## Pruebas

```bat
venv\Scripts\python -m pytest -q          # 44 pruebas, embedder falso, sin red
venv\Scripts\python -m pytest -m model    # descarga y usa el modelo real; comprueba una consulta en español
```

## Límites (v1)

- Sin OCR: los PDF escaneados se listan con «necesita OCR» pero no se pueden buscar.
- Las tablas de DOCX se aplanan como filas `celda | celda`; imágenes y notas al pie se ignoran.
- Los vectores se mantienen en memoria (~150 MB por cada 100 000 pasajes).
- La fase de reordenación (reranker) es un gancho, no una implementación.

## Licencia

MIT — Luissalet.
