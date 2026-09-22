"""Dispatch a file to the extractor for its type."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .base import Extracted, Unit
from .docx import extract_docx
from .html import extract_epub, extract_html
from .pdf import extract_pdf
from .text import extract_csv, extract_markdown, extract_plain

__all__ = ["Extracted", "Unit", "extract", "kind_for", "SUPPORTED", "CODE_EXTENSIONS"]

DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".text": "txt",
    ".epub": "epub",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".csv": "csv",
}

CODE_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".ps1", ".bat",
     ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".sql", ".r", ".jl", ".lua", ".css", ".scss", ".tex", ".bib"}
)

SUPPORTED = frozenset(DOCUMENT_EXTENSIONS)

EXTRACTORS: dict[str, Callable[[Path], Extracted]] = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "md": extract_markdown,
    "txt": extract_plain,
    "epub": extract_epub,
    "html": extract_html,
    "csv": extract_csv,
}


def kind_for(path: Path, code: bool = False) -> str | None:
    """Document kind for a path, or None when the file type is not indexed."""
    suffix = path.suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        return DOCUMENT_EXTENSIONS[suffix]
    if code and suffix in CODE_EXTENSIONS:
        return "code"
    return None


def extract(path: Path, kind: str | None = None) -> Extracted:
    kind = kind or kind_for(path, code=True)
    if kind is None:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    if kind == "code":
        return extract_plain(path, kind="code")
    return EXTRACTORS[kind](path)
