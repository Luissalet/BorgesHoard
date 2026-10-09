"""Per-page decision for PDFs: does a page have a usable text layer, or does it need OCR / vision?

The decision looks at one page's extracted text (plus the lines that repeat across pages, which are running headers or
footers). A page is `usable` when, after dropping page numbers and running headers, it still has real text:

* at least MIN_PAGE_CHARS letters and digits (`too_little_text` otherwise; `only_headers_or_page_numbers` when what was
  left out was all there was; `no_text_layer` when there is nothing at all);
* at most MAX_BAD_CHAR_RATIO of replacement, control or private-use characters (`garbage_text`: a broken font encoding);
* at least MIN_WORDS word-like tokens (letters only, two or more) (`too_few_words`);
* at least MIN_VALID_RATIO of the tokens are words or numbers (`not_word_like`).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

MIN_PAGE_CHARS = 25
MIN_WORDS = 3
MIN_VALID_RATIO = 0.5
MAX_BAD_CHAR_RATIO = 0.15
MIN_PAGES_FOR_RUNNING = 3
MAX_RUNNING_LINE = 120

_PAGE_NUMBER = re.compile(r"^\W*(?:(?:page|pagina|pag|p|p\u00e1g|p\u00e1gina)\.?\s*)?(?:\d{1,5}|[ivxlcdm]{1,8})"
                          r"(?:\s*(?:/|of|de|-)\s*\d{1,5})?\W*$", re.IGNORECASE)
_BARE_PAGE_NUMBER = re.compile(r"^\W*(?:\d{1,5}|[IVXLCDM]{1,8})(?:\s*(?:/|of|de|-)\s*\d{1,5})?\W*$")
_PREFIXED = re.compile(r"^\W*(?:page|pagina|pag|p|p\u00e1g|p\u00e1gina)\.?\s*\w", re.IGNORECASE)
_WORD = re.compile(r"^\W*[^\W\d_]{2,}(?:['\u2019\-][^\W\d_]+)*\W*$")
_NUMBER = re.compile(r"^\W*[+-]?\d[\d.,:/%]*\W*$")


@dataclass(frozen=True)
class Assessment:
    usable: bool
    reason: str
    chars: int  # letters and digits of the whole page text


def _is_page_number(line: str) -> bool:
    return bool(_BARE_PAGE_NUMBER.match(line) or (_PREFIXED.match(line) and _PAGE_NUMBER.match(line)))


def line_key(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.split()).casefold())


def alnum_chars(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


def running_lines(page_texts: list[str]) -> frozenset[str]:
    """Line shapes (digits folded to #) that repeat on at least half of the pages: running headers and footers."""
    if len(page_texts) < MIN_PAGES_FOR_RUNNING:
        return frozenset()
    seen: dict[str, int] = {}
    for text in page_texts:
        keys = {line_key(line) for line in text.splitlines() if line.strip() and len(line.strip()) <= MAX_RUNNING_LINE}
        for key in keys:
            seen[key] = seen.get(key, 0) + 1
    need = max(MIN_PAGES_FOR_RUNNING, math.ceil(len(page_texts) / 2))
    return frozenset(key for key, n in seen.items() if n >= need and alnum_chars(key.replace("#", "")) > 0)


def assess_page(text: str, running: frozenset[str] = frozenset()) -> Assessment:
    chars = alnum_chars(text)
    if chars == 0:
        return Assessment(False, "no_text_layer", 0)
    kept: list[str] = []
    dropped = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if _is_page_number(line) or line_key(line) in running:
            dropped = True
            continue
        kept.append(line)
    body = "\n".join(kept)
    if alnum_chars(body) < MIN_PAGE_CHARS:
        return Assessment(False, "only_headers_or_page_numbers" if dropped else "too_little_text", chars)
    visible = [ch for ch in body if not ch.isspace()]
    bad = sum(1 for ch in visible if ch == "\ufffd" or unicodedata.category(ch) in ("Cc", "Co", "Cn"))
    if visible and bad / len(visible) > MAX_BAD_CHAR_RATIO:
        return Assessment(False, "garbage_text", chars)
    tokens = body.split()
    words = sum(1 for tok in tokens if _WORD.match(tok))
    numbers = sum(1 for tok in tokens if _NUMBER.match(tok))
    if words < MIN_WORDS:
        return Assessment(False, "too_few_words", chars)
    if (words + numbers) / len(tokens) < MIN_VALID_RATIO:
        return Assessment(False, "not_word_like", chars)
    return Assessment(True, "usable_text_layer", chars)


def page_ranges(pages: list[int]) -> str:
    """[1, 2, 3, 7] -> '1-3,7' (the form the OCR service takes)."""
    out: list[str] = []
    ordered = sorted(set(pages))
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        out.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ",".join(out)