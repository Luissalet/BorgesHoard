# Borges's Hoard

## Biblioteca, lecturas y trámites

Borges incorpora **Links y Kafka** con sus interfaces y todas sus funciones: biblioteca con citas, artículos y subrayados, medios, archivo documental, OCR, taller PDF, facturas, contratos, plazos y recordatorios. Tres módulos independientes, todos desactivados inicialmente. Los automatismos tienen controles separados y también empiezan apagados. MCP descubre 378 herramientas sin arrancar servidores ni motores.

Las interfaces nativas viven en `/library/`, `/links/` y `/paperwork/`. El servidor de Borges aloja los motores Python y un auxiliar Node por stdio, sin otro puerto. La búsqueda común consulta sólo módulos activos y conserva las citas originales. No hay que ejecutar las aplicaciones antiguas.

Instalación: `python -m pip install -r requirements.txt` y `npm ci --prefix links_native`. El taller PDF se construye con `python scripts/build_pdf_engine.py` (Rust 1.95.0). Arranque explícito: `python -m borges --no-browser` o desde Hoard Hub. Las interfaces construidas se incluyen en el repositorio.

Antes de usar datos anteriores, con todo apagado: `python scripts/adopt_fusion_data.py --parent "C:\ruta\Hoards"`. Sólo guarda el mapa central; los datos permanecen en sus carpetas originales y deben conservarse. [Funcionamiento, instalación y límites](docs/FUSION.md) · [Inventario completo](provenance/CAPABILITIES.md) · [Procedencia y licencias](provenance/sources.json).

La documentación de biblioteca que sigue conserva sus funciones específicas; las rutas API de esa interfaz llevan ahora el prefijo `/library` y el arranque ya no precarga modelos ni índices.


Tu biblioteca personal, indexada en tu propio ordenador. Señala carpetas con documentos (PDF, DOCX, PPTX, Markdown, TXT, EPUB, HTML, CSV) y pregunta «¿dónde leí o escribí sobre X?»: cada respuesta llega con su cita exacta — `«Título», p. 12` o `apuntes.md § Sección` — y el pasaje completo para leerlo. Un asistente accede al mismo índice por MCP, así que puede citar tus documentos en vez de inventárselos.

Todo se queda en tu máquina: SQLite para texto y vectores, un pequeño modelo multilingüe de embeddings que corre en la CPU, sin cuentas y sin red salvo la descarga única del modelo.

## Qué hace

- **Colecciones** = carpetas que eliges, con globs de inclusión/exclusión, activar/desactivar, vigilancia opcional de cambios (reindexa sola) y archivos de código opcionales.
- **Extracción** por formato: PDF página a página (se conserva el número; las páginas escaneadas las lee el servicio de OCR de la familia (Kafka's Hoard a través del hub, `fam_docs.ocr_pdf` de Hoard Link; solo las páginas sin capa de texto, `BORGES_OCR=0` lo apaga) y, si ese servicio no está en marcha, el PDF se lista como «necesita OCR» y se lee en un escaneo posterior cuando el servicio esté), DOCX con títulos → secciones (las tablas se añaden como filas, las listas conservan el guion), Markdown con títulos → secciones y números de línea, TXT (codificación por BOM, UTF-8, Windows-1252, Latin-1), EPUB capítulo a capítulo (la página del índice no es un capítulo), HTML por h1–h3, CSV (200 primeras filas). Los lectores de DOCX, HTML y texto, la limpieza del texto (las palabras partidas con guion a final de línea se reúnen, las exportaciones de una palabra por línea se vuelven a juntar), el troceado, las citas, las palabras de búsqueda y el orden por vectores son el código común de Hoard Link (`borges/hoard_link/`), no de esta app.
- **Lectura de PDF página a página**: cada página se juzga por separado. Una página con capa de texto útil conserva el texto de PyMuPDF y nunca llega al OCR; solo las páginas sin ella van al servicio de OCR / visión de la familia (`fam_docs.ocr_pdf`, solo esas páginas). La capa de texto es útil cuando, descontados los números de página y las cabeceras o pies que se repiten en al menos la mitad de las páginas (PDF de 3 o más páginas), la página conserva al menos 25 letras o dígitos, al menos 3 palabras, al menos la mitad de sus tokens son palabras o números y no más del 15 % son caracteres de reemplazo, de control o de uso privado. Por página el índice guarda `method` = `text_layer` | `ocr` | `vision` | `empty`, un `reason` (`usable_text_layer`, `no_text_layer`, `only_headers_or_page_numbers`, `too_little_text`, `garbage_text`, `too_few_words`, `not_word_like`, `forced`) y, si no se leyó nada, un `ocr_note` (`service_unavailable`, `ocr_disabled`, `failed`, `page_limit`, `no_text_found`). Está en los metadatos del documento (`meta.pdf_page_methods`), en `GET /api/documents/{id}` (`page_methods`, `ocr_forced`), en la lista de documentos (`page_method_counts`), en `GET /api/documents/{id}/text?page=N` (`extraction`) y en las herramientas MCP `library_document`, `library_documents` y `library_read`. `vision` se indica cuando el servicio de OCR nombra un motor de visión. Para forzar OCR / visión en todas las páginas de una colección: `POST /api/collections/{id}/reindex?force_ocr=true` o `library_reindex` con `force_ocr=true` (reextrae también los PDF sin cambios; si el OCR forzado no lee nada en una página, esta conserva su capa de texto). Los demás formatos no cambian.
- **Troceado** con solapamiento (~900 caracteres, 150 de solape) que nunca cruza una página o sección; cada pasaje recuerda página, sección y línea. Las secciones o páginas de menos de ~200 caracteres (un «## Pendiente» de dos líneas, una portada) se funden con la siguiente conservando los datos de la parte grande, y nunca se emite un pasaje de menos de 120 caracteres salvo que sea el documento entero. Cada documento guarda la versión de las reglas de troceado: si cambian, la siguiente reindexación (que el indexador encola sola al arrancar) vuelve a trocear solo los documentos antiguos y la interfaz avisa «reindexación necesaria: N documentos» hasta terminar.
- **Indexación incremental**: tamaño+fecha y después SHA-256; solo se vuelve a leer lo que cambia; lo borrado se purga. Corre en segundo plano con cola y progreso (archivos hechos/total, archivo actual, errores por archivo).
- **Búsqueda híbrida**: BM25 de SQLite FTS5 (sin distinguir tildes, sin palabras vacías; los términos de 3 o más letras se expanden como prefijo, los más cortos solo palabra entera) ∪ coseno sobre vectores float32 guardados en SQLite → fusión RRF. Modos «Híbrida», «Palabras» y «Significado». Se necesitan al menos 3 caracteres; un resultado por página o sección, con «ver más» para los demás; cada respuesta indica los milisegundos empleados.
- **Embeddings**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX cuantizado, 384 dimensiones, ~240 MB, ~50 idiomas, funciona entre idiomas) mediante `fastembed`, descargado la primera vez en `data/models`. Hasta que está listo la búsqueda es solo por palabras y la interfaz lo dice.
- **Interfaz**: Buscar (con filtro de fuente: documentos / chats de Faustus / enlaces / todos, y una etiqueta de fecha en los resultados de chat), Biblioteca, Colecciones, Fuentes (conectar un Faustus: URL, token o usuario/contraseña, proyectos, intervalo de sincronización, sincronizar ahora, estado) y Estado. Funciona en el móvil (a través de un túnel) con el pasaje a pantalla completa.
- **Fuente de enlaces**: los enlaces guardados en Links Hoard, a través del Hoard Hub (sin URL ni token), cada uno un documento citable `[enlace «Título» · sitio]`. Ver «La fuente de enlaces» más abajo.
- **Fuente Faustus**: señala tu propio Faustus (`http://127.0.0.1:7000` por defecto) y Borges indexa tus conversaciones pasadas, así el asistente puede citar lo que decidisteis en un chat anterior. Ver «La fuente Faustus» más abajo.

## Claves de pregunta (opcional, desactivadas por defecto)

Al indexar, un modelo local pequeño puede escribir 2-3 preguntas que responde cada fragmento («¿cuánto cuesta reparar los espejos del telescopio?»). Se guardan como claves de búsqueda extra que apuntan al fragmento: BM25 sobre las preguntas (tabla FTS5 `questions_fts`) y, con el modelo de embeddings cargado, coseno sobre sus vectores (`question_embeddings`). En `library_search` y `GET /api/search` las dos patas entran en la fusión de rangos como dos rankings más (peso 1,25 frente a 1,0 de las patas del texto) y se deduplican al fragmento: un fragmento hallado por su texto y por sus preguntas es un solo resultado; el que salió por una clave lleva `matched_question`. `questions=false` busca solo en el texto. Las claves se usan siempre que existan, en los modos `hybrid`, `bm25` y `dense`.

- El modelo de preguntas es cualquier endpoint de chat compatible con OpenAI (`<base_url>/chat/completions`): por defecto un Ollama local (`http://127.0.0.1:11434/v1`); hay que indicar el nombre del modelo. Ajuste: `PUT /api/question-keys/settings` o `library_question_keys_settings` (`enabled`, `base_url`, `model`, `api_key`; la clave vuelve enmascarada). Con `enabled`, cada indexación genera claves para los fragmentos que no las tienen, tras los embeddings; desactivado (por defecto) no se genera nada y nada cambia.
- Si el modelo no responde, la indexación no falla: los fragmentos quedan marcados `unavailable` (`chunk_question_state`) y se reintentan en la siguiente indexación o a demanda; tres fallos seguidos cortan la pasada. Los fragmentos de menos de 60 caracteres o con respuesta inservible quedan `empty`. Un fragmento que se vuelve a extraer pierde sus claves y vuelve a `pending`.
- Estado: `GET /api/question-keys[?document=&collection=&probe=]` o `library_question_keys` (fragmentos con claves, vacíos, sin modelo, pendientes; preguntas; preguntas sin vector; si el modelo responde). `GET /api/status` trae el mismo informe en `question_keys`, sin sondear el modelo.
- (Re)generar: `POST /api/question-keys/generate {document_id | collection_id, force?, max_chunks?}` o `library_generate_questions` (funciona con el ajuste desactivado; `force` sustituye las claves existentes; `max_chunks` limita las llamadas al modelo por petición y `remaining` dice cuántas quedan). `GET /api/question-keys/chunk/{id}` lista las preguntas de un fragmento.
- Evaluación sin conexión: `python scripts/eval_question_keys.py` indexa un corpus pequeño con un generador guionizado e imprime el recall@1 de consultas en forma de pregunta con y sin claves (hoy 0,60 sin claves y 1,00 con claves en ese corpus; lo comprueba `tests/test_question_keys.py`). Mide el mecanismo, no un modelo real: el recall con un modelo real sobre una biblioteca real aún no está medido, y por eso no se activa por defecto.

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
| `BORGES_OCR` | `1` | `0` no envía nunca los PDF escaneados al servicio de OCR de la familia. |
| `BORGES_OCR_LANG` | `es` | Idioma que se pide al motor de OCR. |
| `BORGES_WATCH` | `1` | `0` desactiva la vigilancia de carpetas. |
| `BORGES_AUTOSTART` | `1` | `0` no precarga el modelo ni reindexa al arrancar. |
| `BORGES_QUESTIONS_URL` / `BORGES_QUESTIONS_MODEL` / `BORGES_QUESTIONS_KEY` | Ollama local `http://127.0.0.1:11434/v1` / ninguno / ninguna | Valores por defecto del modelo de las claves de pregunta cuando el ajuste guardado en la base está vacío. |

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

## La fuente de enlaces (Links Hoard)

Los enlaces guardados en Links Hoard se indexan igual. Se añade una fuente de tipo `links` desde **Fuentes**
(o `POST /api/sources/links`): qué enlaces (`state`: `all`, `unread`, `read`, `archived`), una `tag` opcional
y el intervalo de sincronización (30 minutos por defecto). Sin URL y sin token: la sincronización pasa por el
proxy del Hoard Hub (`POST <hub>/api/apps/links/call` con el token propio de Borges), así Borges nunca lee el
archivo de token de otra aplicación. Sin hub, `base_url` + `token` llegan directamente al `/api/agent/call`
de Links Hoard.

Cada enlace es un documento (`kind: "link"`) con una ficha (título, sitio, autor, URL, etiquetas, tu nota, el
extracto) y el texto de la página tal como lo extrajo Links Hoard (`read_link`, paginado). Un enlace cuya
página nunca se descargó se indexa igualmente por su título, extracto y nota. Su cita es
`[enlace «Título» · sitio]` y cada resultado lleva la `url`. Un enlace solo se vuelve a leer cuando cambia su
huella (título, número de palabras, estado de descarga, nota, etiquetas, URL, extracto); uno borrado en Links
Hoard sale del índice en la siguiente sincronización. Cada sincronización con cambios emite
`borges.source.synced` en el bus de la familia.

## Conectar el asistente (MCP)

`mcp_server.py` es un puente stdio: pide la lista de herramientas a la aplicación en marcha y reenvía cada llamada a `POST /api/agent/call` con el token de `data/mcp-token`. Nunca abre la base de datos. Faustus detecta la aplicación por `/api/health` y rellena la conexión con `faustus-plugin.json`.

Herramientas: `library_search` (con `source='folder'|'faustus'`, `since`, `until`), `library_read`, `library_document`, `library_documents`, `library_collections`, `library_status`, `library_similar`, `library_reindex`, `library_add_collection`, `chats_recent` (últimos chats de Faustus indexados), y los dos del servicio de embeddings de la familia: `embed_texts` (de 1 a 512 textos → vectores del modelo multilingüe, sin recorte de tamaño) y `embed_status` (estado del modelo). Borges es el dueño de los embeddings de la familia: el resto de apps llaman a esas dos herramientas a través del hub (`fam_embed` en Hoard Link), de modo que toda la familia usa un solo modelo y un solo espacio de vectores. Claves de pregunta: `library_question_keys` (informe por documento o colección), `library_generate_questions` (genera o regenera; escritura) y `library_question_keys_settings` (activar y elegir modelo; escritura); `library_search` acepta `questions` y devuelve `matched_question` en los resultados hallados por una clave.

La protección de peticiones, el formato de errores (con `code`), los ficheros PWA, el servidor de la interfaz (`index.html` nunca se guarda en caché), el puente MCP, las rutas del agente y la clase de base de datos son el código común de Hoard Link. Una segunda ejecución de `python -m borges` avisa de que ya hay una en marcha y termina sin tocar el token ni la base de datos; el token de `data/mcp-token` se crea una vez y se conserva. Al añadir una carpeta se acepta la ruta pegada con sus comillas y se rechaza la raíz de una unidad, la carpeta de usuario, las del sistema, las de configuración o secretos y la propia carpeta de datos. Las instrucciones que acompañan a las herramientas obligan al asistente a responder solo con texto recuperado, a leer el pasaje completo antes de citar, a citar siempre (documento y página/sección, o el chat y la fecha) y a decir claramente cuando no encuentra nada; para preguntas sobre conversaciones o decisiones pasadas («¿qué decidimos sobre X?») le dicen que busque con `source="faustus"` o llame a `chats_recent`.

## Asistente de Faustus

Con el lanzador compartido de Hoard Link instalado, la app muestra el panel de Faustus de la familia (un botón flotante). La conversación, el modelo y las herramientas viven en Faustus; esta app solo le dice al panel qué hay abierto.

- **Contexto por vista.** El panel recibe la página (`search`, `library`, `collections`, `sources`, `status`) y los objetos abiertos en ella, con ids reales: un `document` (título, tipo, colección, ruta relativa, páginas, unidades, fragmentos, estado, fecha de indexado; en Biblioteca también la unidad mostrada) y un `passage` (cita, id del documento, página, sección, línea y una vista previa del texto de 240 caracteres). También cuenta la colección elegida como filtro de búsqueda. Del texto de los documentos no se envía nada más allá de esa vista previa. Las vistas de creación no dan objeto.
- **Campos sin guardar.** El panel los pide con el evento de ventana `borges:context-request`. Los formularios `search-form`, `library-filter`, `collection-form`, `faustus-source-form` y `links-source-form` responden con los campos que difieren de los iniciales. Un token o contraseña escrito en un formulario de fuente lo hace contar como sin guardar, pero el secreto en sí nunca sale de la página.
- **Refresco.** Cuando el asistente termina un turno, la vista abierta se recarga en el sitio (`hoard:copilot-turn-end` / `window.hoardCopilot.onTurnEnd`).
- **Guardado.** Los formularios envían solo los campos que han cambiado. `PATCH /api/collections/{id}` y `PATCH /api/sources/{id}` fusionan solo los campos enviados (los ajustes de una fuente se fusionan por clave y los secretos se conservan; `[]` o un valor vacío borra uno). El parche de colección lee, fusiona y escribe ahora bajo un único bloqueo de la conexión, de modo que sobreviven tanto el formulario del usuario como la llamada de una herramienta de agente.
- **Pruebas.** `node --test client/src/copilot-context.test.mjs` y `python -m pytest tests/test_partial_updates.py`.

## Pruebas

```bat
venv\Scripts\python -m pytest -q          # embedder falso, sin red
venv\Scripts\python -m pytest -m model    # descarga y usa el modelo real; comprueba una consulta en español
```

Incluye la fuente Faustus contra una aplicación ASGI de Faustus simulada (login, token, bypass de loopback, detección de cambios, borrados, citas, filtros por fuente/fecha, CRUD de `/api/sources`).

## Límites (v1)

- Las claves de pregunta están desactivadas por defecto, su mejora con un modelo real sobre bibliotecas reales no está medida y aún no tienen interfaz (solo API y MCP); el nombre del modelo lo pone el usuario.
- El OCR es el de Kafka: sin el hub y Kafka's Hoard en marcha, los PDF escaneados se listan con «necesita OCR» y no se pueden buscar hasta que un escaneo posterior encuentre el servicio.
- Las tablas de DOCX se aplanan como filas `celda | celda`; imágenes y notas al pie se ignoran.
- Los vectores se mantienen en memoria (~150 MB por cada 100 000 pasajes).
- La fase de reordenación (reranker) es un gancho, no una implementación.
- Los turnos de chat de Faustus pasan por la misma regla de fusión de secciones cortas que todo lo demás: una tanda de turnos muy cortos (< ~200 caracteres cada uno) se funde en una sola sección, así que «turno N» en una cita es la sección que terminó conteniendo ese pasaje, no necesariamente el número de cada mensaje individual.
- El sondeo de Faustus resincroniza por temporizador y a demanda; no reacciona a ningún aviso push de Faustus (no existe), así que una conversación editada hace segundos puede tardar hasta `poll_minutes` en reflejarse, o hasta pulsar «Sincronizar ahora».

## Licencia

MIT — Luissalet.

## Diapositivas de PowerPoint

Los archivos PPTX se leen con el lector común de Hoard Link, sin dependencias nuevas: cuadros de texto, tablas y notas de orador, separados por diapositiva y en el orden de la presentación. Las diapositivas vacías no desplazan la numeración y las cortas no se mezclan. Las citas indican archivo y diapositiva (`«presentacion.pptx», diap. 3`). `library_read` admite ese número en `page`, o el `chunk_id` de la búsqueda. No aplica OCR a imágenes ni gráficos, ni admite el formato binario `.ppt`. La versión de troceado pasa a 4; el próximo escaneo reconstruye índices con límites anteriores.
