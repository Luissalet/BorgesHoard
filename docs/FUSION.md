# Biblioteca, lecturas y trámites

Borges incorpora los motores nativos de Links y Kafka. Un servidor público sirve tres interfaces completas y un catálogo MCP de 378 herramientas: 15 de biblioteca, 46 de enlaces, 314 de documentos y tres controles comunes (estado, activación y búsqueda conjunta). El inventario exacto y las colisiones están en `provenance/CAPABILITIES.md`.

## Funciones y automatismos

Los tres módulos están apagados inicialmente. Consultar el catálogo, iniciar Faustus o conectar MCP no abre sus bases, descarga modelos ni inicia trabajos. La apertura explícita de Borges muestra Funciones. Allí se activa cada módulo por separado.

Cada módulo tiene además un control de automatismos, inicialmente apagado. Biblioteca permite vigilancia y precarga; Lecturas permite feeds, análisis automático y recuperación de colas; Trámites permite escaneo de carpetas/correo y recordatorios. Con automatismos apagados siguen disponibles las acciones manuales. Los ajustes históricos permanecen intactos; la autorización de automatismos se guarda en el mapa central. Cambiarla reinicia sólo ese motor después de cerrar sus trabajos.

Los motores de biblioteca y trámites comparten el proceso ASGI. El motor Express de enlaces trabaja como auxiliar Node por stdio, sin puerto ni servidor antiguo. HTTP se analiza sobre sockets de memoria; las respuestas mantienen streaming, cabeceras, descargas y rangos. La desactivación espera los trabajos nativos antes de cerrar SQLite y el auxiliar. Las llamadas que lleguen después reciben `module_disabled`.

## Trabajo común

La búsqueda de Funciones y `borges_search_all` consultan únicamente los módulos activos y conservan los resultados y citas originales. No crean otro índice ni activan módulos. La biblioteca puede seguir indexando enlaces mediante su fuente Links; en el producto fusionado utiliza directamente el motor incorporado. El OCR de la biblioteca y el OCR de vídeo llegan al módulo de Trámites incorporado, sin arrancar el repositorio anterior. Las funciones que necesitan un modelo utilizan el servicio global de la familia; no se incluye un modelo de texto alternativo.

## Datos anteriores

Con todos los módulos apagados, ejecutar `python scripts/adopt_fusion_data.py --parent "C:\ruta\Hoards"`. Sólo se escribe `data/fusion-modules.json` con las rutas de datos, `enabled: []` y `automatic: []`. Los originales de Kafka, bases, historiales, índices, subrayados, configuraciones, tokens y descargas no se copian, convierten ni trasladan. Es necesario conservar sus carpetas. La biblioteca sigue usando su carpeta `data` anterior; los módulos nuevos sin adopción utilizan `data/links` y `data/paperwork`.

Los antiguos IDs Links y Kafka pueden permanecer en el Hub como identidades no arrancables y apuntar a las rutas `/links` y `/paperwork`. Conservan sus tokens originales, referencias, contratos de servicios y agenda; nunca son dueños del proceso Borges. Se retiran de los grupos de inicio. El catálogo directo de Borges es estático y nunca inicia el servidor.

## Instalación y uso

Python 3.11+, Node 22.13+ y las dependencias de `requirements.txt`. Instalar también `npm ci --prefix links_native`. Las interfaces construidas se distribuyen con el código; para reconstruir: `npm ci && npm run build` en la raíz, `links_native` y `paperwork_ui`. Cada configuración Vite conoce su prefijo; los paquetes JavaScript no se reescriben durante la entrega.

El taller PDF incluye su motor Rust propio bajo `native/pdf` y conserva su procedencia y licencias. Construirlo con `python scripts/build_pdf_engine.py` (toolchain Rust 1.95.0). OCR opcional: `pip install rapidocr`; los motores externos de Office, Ghostscript, medios, OCR y modelos conservan sus requisitos nativos y comunican cuándo no están disponibles.

Arrancar explícitamente desde el Hub o con `python -m borges --no-browser`. El servidor preferido es `http://127.0.0.1:5184`. `BORGES_DATA_DIR` cambia el mapa y token centrales; `BORGES_PORT` cambia el puerto. No es necesario ejecutar Links ni Kafka.

## Verificación

`python -m pytest tests` incluye la biblioteca y la integración real de los tres motores con datos sintéticos. `links_native/tests` conserva las regresiones Node y `legacy_tests/kafka` las de trámites. Los tests históricos de arranque automático se han sustituido por pruebas de catálogo inerte; el E2E activa explícitamente Biblioteca y mantiene sus comprobaciones de extracción, búsqueda y citas. Ninguna prueba de migración abre los datos reales.

Procedencia exacta en `provenance/sources.json`; las licencias MIT y las licencias del motor PDF se conservan. Las capturas y resultados privados de aceptación no forman parte de la publicación.

## Conversaciones de Faustus

Cada interfaz nativa conserva su identidad y copilot-space-id originales. El lanzador común se sirve sin cambios; las peticiones relativas a la raíz se envían al módulo de la página de origen. Biblioteca conserva Borges, Lecturas conserva Links y Trámites conserva Kafka, con el contexto y las herramientas de esa interfaz. Consultar el panel de un módulo apagado no lo activa.
