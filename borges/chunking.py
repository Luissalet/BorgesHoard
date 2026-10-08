"""Chunking is the shared `hoard_link.docs.chunking` (Borges' rules, now kept once for the whole family). This module only
names what the app stores with every document: INDEX_VERSION, bumped when chunk boundaries or extraction change so the
library is re-indexed; it follows the library's CHUNK_VERSION (a boundary rule change there bumps it here)."""

from __future__ import annotations

from .hoard_link.docs.chunking import (CHUNK_CHARS, CHUNK_VERSION, MIN_CHUNK_CHARS, OVERLAP_CHARS, Chunk, chunk_units,
                                       merge_small_units)

INDEX_VERSION = CHUNK_VERSION  # 4: physical PDF page and PowerPoint slide boundaries, including short units

__all__ = ["CHUNK_CHARS", "OVERLAP_CHARS", "MIN_CHUNK_CHARS", "INDEX_VERSION", "Chunk", "chunk_units", "merge_small_units"]
