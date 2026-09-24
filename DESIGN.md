---
name: Borges's Hoard
description: Una biblioteca personal que responde dónde leíste o escribiste algo, con la cita exacta.
colors:
  accent: "#7a2e2e"
  accent-hover: "#5f2222"
  accent-soft: "#f3e4e2"
  ink: "#2b2523"
  muted: "#7a6f6b"
  paper: "#fbf9f6"
  white: "#ffffff"
  line: "#e9e2dc"
  soft: "#f3eeea"
  sidebar: "#f5f0eb"
  nav-active: "#ecdcd8"
  nav-active-ink: "#5f2222"
  nav-hover: "#eee7e1"
  field-line: "#d9cfc7"
  field-ink: "#2f2826"
  placeholder: "#9a8f89"
  supporting-ink: "#6e645f"
  focus: "#a04a4a"
  button-line: "#ddd3cb"
  panel: "#f6f2ee"
  ok-bg: "#e5efe4"
  ok-ink: "#2f5f3a"
  warn-bg: "#f8ecd2"
  warn-ink: "#7a5a17"
  danger-bg: "#f9e8e6"
  danger-ink: "#8a2f26"
  danger-line: "#eac6c1"
  bar-bg: "#e8dfd8"
  highlight: "#f7e7a7"
  quote: "#6b3a3a"
typography:
  headline:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    lineHeight: 1.65
  passage:
    fontFamily: "Georgia, Times New Roman, serif"
    fontSize: "15px"
    lineHeight: 1.7
  citation:
    fontFamily: "Georgia, serif"
    fontSize: "14px"
    fontWeight: 600
  button:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: "18px"
  label:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
  code:
    fontFamily: "Consolas, monospace"
    fontSize: "12px"
rounded:
  badge: "5px"
  field: "6px"
  control: "7px"
  panel: "8px"
  search: "9px"
spacing:
  control-gap: "8px"
  action-gap: "10px"
  page-gutter: "40px"
  page-gutter-mobile: "16px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.white}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 15px"
  button-secondary:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 15px"
  field:
    backgroundColor: "{colors.white}"
    textColor: "{colors.field-ink}"
    rounded: "{rounded.field}"
    padding: "8px 11px"
  search-field:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.search}"
    padding: "12px 16px"
    fontSize: "16px"
  hit:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.panel}"
    padding: "14px 16px"
  passage-panel:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.panel}"
    sticky: "top 16px on desktop; full-screen overlay under 768px"
  chip:
    typography: "10px / 600 / uppercase"
    rounded: "{rounded.badge}"
    padding: "2px 6px"
---

# Design System: Borges's Hoard

## Overview

**Creative North Star: "Sala de lectura"**

Una biblioteca privada, silenciosa, donde cada respuesta llega con su cita. Papel cálido, tinta oscura y un acento sangre de toro (oxblood) reservado a la acción principal y a las citas. Las citas y los pasajes se componen en serifa (Georgia) para distinguir lo que dice el documento de lo que dice la interfaz; todo lo demás va en Segoe UI. La interfaz está en español de España.

**Key Characteristics:**

- Un buscador grande y un panel de pasaje a su derecha: la pregunta y la prueba se ven a la vez.
- La cita (`«Título», p. 12` / `archivo.md § Sección`) es el elemento más visible de cada resultado.
- Resaltado de coincidencias en amarillo papel; el pasaje concreto del resultado lleva un fondo crema.
- Estados escritos (al día, extrayendo, calculando embeddings, error) en lugar de iconos animados.

## Colors

### Primary

- `accent` #7a2e2e (oxblood) para el botón primario, el modo de búsqueda activo, la barra de progreso y la marca «B».
- `accent-hover` #5f2222 y `accent-soft` #f3e4e2 (iconos de tipo de archivo, chip de fase).
- `quote` #6b3a3a para las citas.

### Neutral

- `paper` #fbf9f6 fondo; `white` paneles y filas; `sidebar` #f5f0eb; `panel` #f6f2ee formularios.
- `ink` #2b2523 texto; `supporting-ink` #6e645f ayudas; `line` #e9e2dc bordes.
- Semánticos: `ok` verde apagado (al día, modelo listo), `warn` ámbar (necesita OCR, modelo cargando), `danger` (errores de extracción, quitar).
- `highlight` #f7e7a7 para `<mark>`; el pasaje del resultado usa #fdf3dc.

## Typography

**Body Font:** Segoe UI (system-ui de respaldo). **Pasajes y citas:** Georgia. Consolas solo para nombres de modelo y rutas.

- **Headline:** título de página 30px (26px en móvil).
- **Title:** títulos de sección 16px seminegrita.
- **Body:** 14px; ayudas y metadatos 12px; chips 10px mayúsculas.
- **Passage:** 15px/1.7 con `white-space: pre-wrap` para conservar párrafos y saltos de línea originales.
- **Citation:** 14px seminegrita en `quote`.

## Layout

Escritorio: índice fijo de 224px + contenido flexible (`min-width: 0`), márgenes de 40px. Buscar usa una cuadrícula de dos columnas cuando hay un pasaje abierto: resultados (flexible) y panel de pasaje (320px–44%), pegajoso a 16px de arriba y con altura máxima `100dvh - 2rem` y desplazamiento propio.

Biblioteca lista filas agrupadas por colección; Documento usa índice de 260px + artículo. Colecciones apila formulario y tarjetas. Estado usa cuatro cifras en una fila.

- Hasta 768px: el índice pasa a barra superior con navegación horizontal desplazable; márgenes de 16px; el panel de pasaje ocupa toda la pantalla (`position: fixed`) con botón «Cerrar»; las columnas de metadatos de la biblioteca se ocultan salvo los avisos (OCR, error).
- No hay desplazamiento horizontal de página a 390px: todo contenedor de cuadrícula lleva `min-width: 0`.

## Elevation & Depth

Plano por defecto. Sombras solo en el aviso flotante (toast). El resultado seleccionado se marca con borde `accent` y un anillo de 1px, no con sombra.

## Shapes

Campos 6px, controles 7px, paneles 8px, el buscador principal 9px, chips 5px. Bordes de 1px. Iconos SVG de línea; los tipos de archivo son monogramas de texto (PDF, DOC, MD, EPUB, CHAT…) sobre `accent-soft`. Sin imágenes raster. Un resultado de chat de Faustus añade además un chip con la fecha de la conversación junto a la cita.

## Components

### Buttons

Primario (oxblood), secundario (blanco con borde), peligro (rojo suave: «Quitar»). Altura mínima 38px; variante `btn-sm` de 30px para acciones de fila. Enlaces de acción (`btn-link`) en `accent`.

### Inputs / Fields

Etiqueta encima, ayuda debajo. El buscador principal es más grande (16px, 12px de relleno) y recibe el foco al abrir la página. El selector de modo es un control segmentado (Híbrida / Palabras / Significado); «Significado» se deshabilita hasta que el modelo está listo.

### Navigation

Cinco secciones: Buscar, Biblioteca, Colecciones, Fuentes, Estado. Rutas por hash (`#/buscar`, `#/biblioteca/12`). La activa usa `aria-current` con fondo `nav-active`. Fuentes reutiliza el patrón de tarjeta de Colecciones (formulario arriba, tarjetas con progreso abajo) para conectar un Faustus: URL, token o usuario/contraseña, proyectos a incluir, intervalo de sondeo, activar/desactivar, «Sincronizar ahora».

### Chips

Tipo de archivo, fase de indexación, «sin texto · necesita OCR» (warn), «error» (danger). Texto en mayúsculas de 10px.

### Hits and Passage

Cada resultado es un botón con: monograma de tipo, cita (serifa), título/ruta/colección en ayuda, y fragmento con `<mark>`. Al pulsar se abre el panel de pasaje con la página o sección completa, el fragmento del resultado sobre fondo crema, los términos resaltados, navegación Anterior/Siguiente y «¿Dónde más hablo de esto?» (pasajes parecidos).

### Progress and errors

La colección muestra fase escrita, contador (archivos o pasajes) y barra en `accent` mientras trabaja; al terminar, resumen (cambiados, eliminados, embeddings, segundos). Los errores por archivo se despliegan en una lista sobre fondo `danger`.

### Empty states

Un título, una frase y una única acción («Añadir una carpeta») que lleva a Colecciones.

## Do's and Don'ts

### Do:

- **Do** mostrar siempre la cita antes que el fragmento; la cita es la respuesta.
- **Do** mantener el pasaje en serifa y con saltos de línea originales.
- **Do** escribir los estados del modelo y del indexador; nunca ocultar que la búsqueda es solo por palabras mientras se descarga el modelo.
- **Do** conservar «sin texto · necesita OCR» visible: un documento listado no es un documento buscable.

### Don't:

- **Don't** convertir los resultados en tarjetas elevadas ni añadir sombras al panel de pasaje.
- **Don't** usar el acento para texto largo; reservarlo a acciones, citas y estado activo.
- **Don't** truncar la cita: si no cabe, cortar el título con puntos suspensivos pero conservar «p. N» o «§ Sección».
