# Borges's Hoard

Tu biblioteca personal, indexada en tu propio ordenador. Señala carpetas con documentos (PDF, DOCX, Markdown, TXT, EPUB, HTML, CSV) y pregunta «¿dónde leí o escribí sobre X?»: cada respuesta llega con su cita exacta — `«Título», p. 12` o `apuntes.md § Sección` — y el pasaje completo para leerlo. Un asistente accede al mismo índice por MCP, así que puede citar tus documentos en vez de inventárselos.

Todo se queda en tu máquina: SQLite para texto y vectores, un pequeño modelo multilingüe de embeddings que corre en la CPU, sin cuentas y sin red salvo la descarga única del modelo.

## Qué hace

- **Colecciones** = carpetas que eliges, con globs de inclusión/exclusión, activar/desactivar, vigilancia opcional de cambios (reindexa sola) y archivos de código opcionales.
- **Extracción** por formato: PDF página a página (se conserva el número; los PDF escaneados sin capa de texto se listan y se marcan «necesita OCR», sin OCR en esta versión), DOCX con títulos → secciones (las tablas se añaden como filas), Markdown con títulos → secciones y números de línea, TXT, EPUB capítulo a capítulo, HTML por h1–h3, CSV (200 primeras filas).
- **Troceado** con solapamiento (~900 caracteres, 150 de solape) que nunca cruza una página o sección; cada pasaje recuerda página, sección y línea. Las secciones o páginas de menos de ~200 caracteres (un «## Pendiente» de dos líneas, una portada) se funden con la siguiente conservando los datos de la parte grande, y nunca se emite un pasaje de menos de 120 caracteres salvo que sea el documento entero. Cada documento guarda la versión de las reglas de troceado: si cambian, la siguiente reindexación (que el indexador encola sola al arrancar) vuelve a trocear solo los documentos antiguos y la interfaz avisa «reindexación necesaria: N documentos» hasta terminar.
- **Indexación incremental**: tamaño+fecha y después SHA-256; solo se vuelve a leer lo que cambia; lo borrado se purga. Corre en segundo plano con cola y progreso (archivos hechos/total, archivo actual, errores por archivo).
- **Búsqueda híbrida**: BM25 de SQLite FTS5 (sin distinguir tildes, sin palabras vacías; los términos de 3 o más letras se expanden como prefijo, los más cortos solo palabra entera) ∪ coseno sobre vectores float32 guardados en SQLite → fusión RRF. Modos «Híbrida», «Palabras» y «Significado». Se necesitan al menos 3 caracteres; un resultado por página o sección, con «ver más» para los demás; cada respuesta indica los milisegundos empleados.
- **Embeddings**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX cuantizado, 384 dimensiones, ~240 MB, ~50 idiomas, funciona entre idiomas) mediante `fastembed`, descargado la primera vez en `data/models`. Hasta que está listo la búsqueda es solo por palabras y la interfaz lo dice.
- **Interfaz**: Buscar (con filtro de fuente: documentos / chats de Faustus / ambos, y una etiqueta de fecha en los resultados de chat), Biblioteca, Colecciones, Fuentes (conectar un Faustus: URL, token o usuario/contraseña, proyectos, intervalo de sincronización, sincronizar ahora, estado) y Estado. Funciona en el móvil (a través de un túnel) con el pasaje a pantalla completa.
- **Fuente Faustus**: señala tu propio Faustus (`http://127.0.0.1:7000` por defecto) y Borges indexa tus conversaciones pasadas, así el asistente puede citar lo que decidisteis en un chat anterior. Ver «La fuente Faustus» más abajo.

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

Una vez abierta a través del túnel, el navegador ofrece instalarla (PWA).

## La fuente Faustus

Además de carpetas, Borges puede indexar las conversaciones propias del usuario desde un Faustus en marcha, para que el asistente pueda citar lo que se decidió en un chat anterior. Se añade desde **Fuentes** (o `POST /api/sources`, ver abajo): una `base_url` (Faustus suele correr en `http://127.0.0.1:7000`, con instancias de prueba en `7001`–`7003`) y, o bien un **token de API** (preferido: créalo en Faustus con el alcance `sessions`, `POST /api/tokens`, y pégalo aquí), o bien **usuario/contraseña**. Una fuente sin ninguna credencial también funciona si Faustus corre con `LOCALHOST_BYPASS=true` y ambas aplicaciones están en loopback — Borges prueba primero sin autenticación y solo inicia sesión ante un 401.

Las credenciales se guardan únicamente en la base de datos local de Borges (`data/borges-hoard.db`, ya fuera de git — ver `.gitignore`), nunca en un archivo en claro ni en un commit; la API y la interfaz siempre muestran el token/contraseña enmascarados. No hay un `data/sources.json` aparte: el almacén de colecciones que ya existe vive bajo `data/`, y es el sitio natural para esto, así que la configuración de una fuente Faustus viaja en la misma fila de `collections` (`kind='faustus'`) que una carpeta.

Endpoints de Faustus en los que se apoya (conviene verificarlos contra la instancia real):

- `POST /api/auth/login` — JSON `{username, password, remember}`; deja la cookie `odysseus_session`. Solo se usa cuando no hay token configurado y una llamada responde 401.
- `GET /api/sessions` — todas las conversaciones no archivadas del usuario: `id`, `name`, `folder` (se usa como «proyecto»), `model`, `created_at`, `updated_at`, `last_message_at`. Se sondea para detectar cambios sin volver a descargar cada conversación entera.
- `GET /api/session/{id}/export?fmt=json` — la transcripción completa de una conversación, ya construida por el propio `src/chat_export.py` de Faustus (que descarta los turnos de sistema y cualquier turno marcado `metadata.hidden`, es decir, las tarjetas de aprobación internas).
- Los tokens usan `Authorization: Bearer <token>` (creado con `POST /api/tokens`, alcance `sessions`, prefijo `ody_`).

Cada conversación se convierte en un documento (`kind: "chat"`), titulado `Chat: <título> (<aaaa-mm-dd>)`, con una sección por cada turno de usuario/asistente conservado («turno N»); el resultado de una herramienta solo se conserva si es corto (≤300 caracteres), si no solo se anota su nombre. El mismo troceado/embeddings/FTS de siempre indexa esto igual que un documento de carpeta, así que las citas, `library_similar` y el panel de pasaje funcionan sin cambios. La cita se lee `[chat «Título» · aaaa-mm-dd · turno N]`. Un sondeo en segundo plano resincroniza cada fuente cada `poll_minutes` (10 por defecto); **Fuentes** también tiene un botón «Sincronizar ahora».

## Conectar el asistente (MCP)

`mcp_server.py` es un puente stdio: pide la lista de herramientas a la aplicación en marcha y reenvía cada llamada a `POST /api/agent/call` con el token de `data/mcp-token`. Nunca abre la base de datos. Faustus detecta la aplicación por `/api/health` y rellena la conexión con `faustus-plugin.json`.

Herramientas: `library_search` (con `source='folder'|'faustus'`, `since`, `until`), `library_read`, `library_document`, `library_documents`, `library_collections`, `library_status`, `library_similar`, `library_reindex`, `library_add_collection` y `chats_recent` (últimos chats de Faustus indexados). Las instrucciones que acompañan a las herramientas obligan al asistente a responder solo con texto recuperado, a leer el pasaje completo antes de citar, a citar siempre (documento y página/sección, o el chat y la fecha) y a decir claramente cuando no encuentra nada; para preguntas sobre conversaciones o decisiones pasadas («¿qué decidimos sobre X?») le dicen que busque con `source="faustus"` o llame a `chats_recent`.

## Pruebas

```bat
venv\Scripts\python -m pytest -q          # embedder falso, sin red
venv\Scripts\python -m pytest -m model    # descarga y usa el modelo real; comprueba una consulta en español
```

Incluye la fuente Faustus contra una aplicación ASGI de Faustus simulada (login, token, bypass de loopback, detección de cambios, borrados, citas, filtros por fuente/fecha, CRUD de `/api/sources`).

## Límites (v1)

- Sin OCR: los PDF escaneados se listan con «necesita OCR» pero no se pueden buscar.
- Las tablas de DOCX se aplanan como filas `celda | celda`; imágenes y notas al pie se ignoran.
- Los vectores se mantienen en memoria (~150 MB por cada 100 000 pasajes).
- La fase de reordenación (reranker) es un gancho, no una implementación.
- Los turnos de chat de Faustus pasan por la misma regla de fusión de secciones cortas que todo lo demás: una tanda de turnos muy cortos (< ~200 caracteres cada uno) se funde en una sola sección, así que «turno N» en una cita es la sección que terminó conteniendo ese pasaje, no necesariamente el número de cada mensaje individual.
- El sondeo de Faustus resincroniza por temporizador y a demanda; no reacciona a ningún aviso push de Faustus (no existe), así que una conversación editada hace segundos puede tardar hasta `poll_minutes` en reflejarse, o hasta pulsar «Sincronizar ahora».

## Licencia

MIT — Luissalet.
