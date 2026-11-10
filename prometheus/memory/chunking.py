"""Split long text into chunks: small overlapping children to search, larger parents to read.

Recursive splitting: try the biggest natural break first (a heading, then a blank line, a line, a sentence,
a word) and only fall back to a smaller one when a piece is still too long. Search runs over the small
children, so a match points at one idea; the answer is written from the parent around it, so the model
still sees the full context.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CHILD_CHARS = 1000      # about 250 tokens
PARENT_CHARS = 4000     # about 1000 tokens
OVERLAP = 0.15          # children share about 15% with the one before
MAX_DOC_CHARS = 2_000_000
SEPARATORS = ("\n# ", "\n## ", "\n### ", "\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")


@dataclass
class Piece:
    text: str
    start: int
    end: int


@dataclass
class Parent(Piece):
    children: list[Piece] = field(default_factory=list)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _cut_points(text: str, sep: str) -> list[int]:
    """Where a piece may end. A heading starts the next piece; other separators end the current one."""
    keep_with_next = sep.startswith("\n#")
    points, i = [], text.find(sep)
    while i != -1:
        cut = i + 1 if keep_with_next else i + len(sep)
        if 0 < cut < len(text):
            points.append(cut)
        i = text.find(sep, i + 1)
    return points


def split_spans(text: str, size: int, offset: int = 0, seps: tuple[str, ...] = SEPARATORS) -> list[tuple[int, int]]:
    """Spans (start, end) covering the text, each at most `size` characters, cut at the biggest break that works."""
    if len(text) <= size:
        return [(offset, offset + len(text))] if text else []
    for k, sep in enumerate(seps):
        points = _cut_points(text, sep)
        if not points:
            continue
        bounds = [0, *points, len(text)]
        spans: list[tuple[int, int]] = []
        start = 0
        for a, b in zip(bounds, bounds[1:]):
            if b - a > size:                       # this piece alone is too long: close the current span, split it finer
                if a > start:
                    spans.append((offset + start, offset + a))
                spans.extend(split_spans(text[a:b], size, offset + a, seps[k + 1:]))
                start = b
            elif b - start > size:                 # adding it would overflow: close the span before it
                spans.append((offset + start, offset + a))
                start = a
        if start < len(text):
            spans.append((offset + start, offset + len(text)))
        return spans
    return [(offset + i, offset + min(i + size, len(text))) for i in range(0, len(text), size)]


def _piece(text: str, start: int, end: int) -> Piece | None:
    raw = text[start:end]
    body = raw.strip()
    if not body:
        return None
    lead = len(raw) - len(raw.lstrip())
    return Piece(body, start + lead, start + lead + len(body))


def _with_overlap(text: str, spans: list[tuple[int, int]], overlap_chars: int, floor: int) -> list[tuple[int, int]]:
    """Start each span (but the first) a little earlier, at a word boundary, so ideas on a boundary are kept whole."""
    out = []
    for n, (a, b) in enumerate(spans):
        if n and overlap_chars:
            s = max(floor, a - overlap_chars)
            space = text.find(" ", s, a)
            a = space + 1 if space != -1 else s
        out.append((a, b))
    return out


def chunk_text(text: str, child_chars: int = CHILD_CHARS, parent_chars: int = PARENT_CHARS,
               overlap: float = OVERLAP) -> list[Parent]:
    """Parents of about `parent_chars`, each split into overlapping children of about `child_chars`."""
    text = (text or "").replace("\r\n", "\n").replace("\x00", "")[:MAX_DOC_CHARS]
    parents: list[Parent] = []
    for pa, pb in split_spans(text, parent_chars):
        head = _piece(text, pa, pb)
        if head is None:
            continue
        parent = Parent(head.text, head.start, head.end)
        base = split_spans(text[head.start:head.end], child_chars, head.start)
        for ca, cb in _with_overlap(text, base, int(child_chars * overlap), head.start):
            child = _piece(text, ca, cb)
            if child is not None:
                parent.children.append(child)
        parents.append(parent)
    return parents
