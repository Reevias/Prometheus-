"""Markdown → PDF: headings, paragraphs, bullet and numbered lists, **bold**, and links that can be clicked.

Uses a font installed on the laptop (Segoe UI or Arial on Windows, DejaVu on Linux) so ₹, é and other characters
print correctly; without one it falls back to the built-in font and plain characters.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

FONTS = [("Segoe", "segoeui.ttf", "segoeuib.ttf"), ("Arial", "arial.ttf", "arialbd.ttf"),
         ("DejaVu", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf")]
FONT_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu"),
             Path("/usr/share/fonts/TTF"), Path("/Library/Fonts")]
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s)<>\]]+)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
LINK_BLUE = (35, 86, 190)


def _font():
    for name, regular, bold in FONTS:
        for d in FONT_DIRS:
            if (d / regular).exists():
                b = d / bold
                return name, d / regular, (b if b.exists() else d / regular)
    return None


def _plain(text: str, unicode_ok: bool) -> str:
    if unicode_ok:
        return text
    swaps = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "•": "-", "₹": "Rs "}
    for a, b in swaps.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def _segments(line: str):
    """(text, bold, link) pieces of one line."""
    pos = 0
    for m in LINK.finditer(line):
        if m.start() > pos:
            yield from _bold(line[pos:m.start()])
        label, url = (m.group(1), m.group(2)) if m.group(2) else (m.group(3), m.group(3))
        yield label, False, url.rstrip(".,")
        pos = m.end()
    if pos < len(line):
        yield from _bold(line[pos:])


def _bold(text: str):
    pos = 0
    for m in BOLD.finditer(text):
        if m.start() > pos:
            yield text[pos:m.start()], False, ""
        yield m.group(1), True, ""
        pos = m.end()
    if pos < len(text):
        yield text[pos:], False, ""


_FONT_NAMES: dict[str, str] = {}      # font file path -> the name it was registered under (once per run)


def _register(font) -> str:
    """Register the installed font (regular + bold) with ReportLab once; returns the family name."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    key = f"{font[1]}|{font[2]}"
    if key not in _FONT_NAMES:
        name = f"Prom{len(_FONT_NAMES)}{font[0]}"
        pdfmetrics.registerFont(TTFont(name, str(font[1])))
        pdfmetrics.registerFont(TTFont(name + "-B", str(font[2])))
        pdfmetrics.registerFontFamily(name, normal=name, bold=name + "-B", italic=name, boldItalic=name + "-B")
        _FONT_NAMES[key] = name
    return _FONT_NAMES[key]


def markdown_pdf(text: str, title: str = "") -> bytes:
    """Written with ReportLab (BSD licence)."""
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer

    font = _font()
    try:
        family, bold_family, uni = (_register(font), None, True) if font else ("Helvetica", None, False)
    except Exception:                       # a broken font file: the built-in font still works
        family, bold_family, uni = "Helvetica", None, False

    def style(size: float, indent: float = 0) -> ParagraphStyle:
        return ParagraphStyle(f"s{size}-{indent}", fontName=family, fontSize=size, leading=size * 1.35,
                              leftIndent=indent, spaceAfter=3)

    def markup(line: str) -> str:
        out = []
        for piece, bold, url in _segments(line):
            part = escape(_plain(piece, uni))
            if url:
                part = f'<a href="{escape(url, {chr(34): "&quot;"})}" color="#2356be">{part}</a>'
            out.append(f"<b>{part}</b>" if bold else part)
        return "".join(out)

    story, in_code = [], False
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            story.append(Preformatted(_plain(line, False) or " ", ParagraphStyle(
                "code", fontName="Courier", fontSize=9, leading=11.5, leftIndent=10)))
            continue
        if not line.strip():
            story.append(Spacer(1, 3 * mm))
            continue
        h = re.match(r"^\s*(#{1,6})\s+(.*)$", line)
        if h:
            level = len(h.group(1))
            size = {1: 20, 2: 15, 3: 13}.get(level, 12)
            story.append(Spacer(1, 2 * mm if level > 1 else 0))
            story.append(Paragraph(f"<b>{markup(h.group(2).replace('**', ''))}</b>", style(size)))
            continue
        b = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
        if b:
            story.append(Paragraph("•&nbsp;&nbsp;" + markup(b.group(2)), style(11, 12 + min(len(b.group(1)), 8) * 5)))
            continue
        n = re.match(r"^(\s*)(\d{1,3})[.)]\s+(.*)$", line)
        if n:
            story.append(Paragraph(f"{n.group(2)}.&nbsp;&nbsp;" + markup(n.group(3)),
                                   style(11, 12 + min(len(n.group(1)), 8) * 5)))
            continue
        story.append(Paragraph(markup(line.strip()), style(11)))
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=_plain(title or "Prometheus", uni), creator="Prometheus",
                            author="Prometheus")
    doc.build(story or [Spacer(1, 1)])
    return buffer.getvalue()
