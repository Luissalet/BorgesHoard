# Piloto de estructura, recuperación y citas PDF

Inspiración: [PageIndex](https://github.com/VectifyAI/PageIndex), separación de
estructura declarada e inferida. Este piloto evalúa la implementación propia de
Borges; no ejecuta PageIndex ni copia su código.

## Fallo encontrado y corregido

El indexador fusionaba páginas de menos de 200 caracteres con una vecina y
conservaba el número de la página receptora. Una evidencia en la página 1 podía
recuperarse y citarse como página 2; un marcador a página 1 abría otra página.

Ahora las páginas físicas se conservan como unidades independientes. Una página
sin texto extraíble no devuelve silenciosamente la página vecina. La versión de
índice sube a 3: el siguiente arranque/reindexado reconstruye índices anteriores;
las siguientes pasadas vuelven a ser incrementales. Esto puede aumentar el
número de fragmentos pequeños de PDFs. La fusión de secciones de otros formatos
no cambia.

## Comprobación reproducible

`tests/test_pdf_citation_roundtrip.py` genera un PDF con páginas breves inicial y
final, una página densa, una separadora vacía y cuatro marcadores. Comprueba la
migración automática al reiniciar, persistencia de destinos, búsqueda BM25,
lectura por resultado y por página, cita física correcta, ausencia de sustitución
de la página vacía y que la segunda indexación no repite el trabajo.

Para repetir la evaluación sobre un PDF local:

```powershell
.\venv\Scripts\python.exe scripts/check_pdf_provenance.py 'ruta\documento.pdf'
```

El script copia el PDF a una carpeta temporal, crea un índice aislado, lo cierra
y lo reabre. Contrasta el texto persistido de cada página con la extracción del
original; verifica destinos estructurales y búsqueda/citas con un término literal
exclusivo de cada página cuando existe. Usa embeddings falsos y búsqueda BM25;
no descarga modelos ni modifica la biblioteca de producción.

## Resultado real, 29-09-2026

Muestra ya visitada: **TextMesh Pro User Guide 2016.pdf**, disponible en
`TFMMonsterCollecting/Assets/TextMesh Pro/Documentation` del equipo local.
SHA-256: `93d6d3ca8a7d8423b01faacd00fa3921fe055fb2187873653a925e4cc1af85c0`.

- 14/14 páginas con texto preservado tras reabrir el índice.
- 8/8 destinos inferidos persistidos y navegables.
- 14/14 páginas recuperadas con término literal exclusivo y cita física correcta.
- Suite completa: 97 pruebas correctas; 1 prueba del modelo real deseleccionada.

La segunda muestra anterior (`theme-showcase.pdf`) no se volvió a localizar en
las carpetas consultadas, por lo que no se afirma una nueva ejecución sobre ella.
Los 8 destinos válidos no miden cobertura/precisión de encabezados. La prueba
literal no mide recuperación semántica, calidad de respuestas del modelo ni OCR.
No se incluyen los PDFs originales en el repositorio.
