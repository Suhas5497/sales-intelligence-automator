"""Mark verified evidence quotes inside page text for the evidence reader (HTML-escaped output)."""
from __future__ import annotations

import html
import re

QUOTE_CHARS = {"'": "['‘’]", '"': "[\"“”]", "-": "[-–—]"}


def _pattern(quote: str) -> re.Pattern | None:
    words = html.escape(quote.strip(" .\"'"), quote=False).split()
    if not words:
        return None
    parts = ["".join(QUOTE_CHARS.get(ch, re.escape(ch)) for ch in word) for word in words]
    return re.compile(r"\s+".join(parts), re.I)


def highlight(text: str, quotes: list[str], context: int | None = None) -> tuple[str, int]:
    """Return (escaped html with <mark> around each quote, number of quotes found).
    With context, only the passages around matches are kept (… between them)."""
    escaped = html.escape(text or "", quote=False)
    spans = []
    for quote in quotes:
        pattern = _pattern(quote)
        match = pattern.search(escaped) if pattern else None
        if match:
            spans.append((match.start(), match.end()))
    spans = sorted(set(spans))
    if not spans:
        return (escaped[: context * 2] + ("…" if len(escaped) > context * 2 else "")) if context else escaped, 0
    out, cursor, shown = [], 0, 0
    for start, end in spans:
        if start < cursor:
            continue                                  # overlaps a passage already marked
        shown += 1
        lead_in = escaped[cursor:start]
        if context and len(lead_in) > context * 2:
            lead_in = (lead_in[:context] + " … " if cursor else "… ") + lead_in[-context:]
        out.append(lead_in + f"<mark>{escaped[start:end]}</mark>")
        cursor = end
    tail = escaped[cursor:]
    if context and len(tail) > context:
        tail = tail[:context] + " …"
    out.append(tail)
    return "".join(out), shown
