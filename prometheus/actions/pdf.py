"""Markdown → PDF: headings, paragraphs, bullet and numbered lists, **bold**, and links that can be clicked.

Uses a font installed on the laptop (Segoe UI or Arial on Windows, DejaVu on Linux) so ₹, é and other characters
print correctly; without one it falls back to the built-in font and plain characters.
"""
from __future__ import annotations

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


def markdown_pdf(text: str, title: str = "") -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(18, 18, 18)
    font = _font()
    if font:
        pdf.add_font(font[0], "", str(font[1]))
        pdf.add_font(font[0], "B", str(font[2]))
        family, uni = font[0], True
    else:
        family, uni = "Helvetica", False
    pdf.set_title(_plain(title or "Prometheus", uni))
    pdf.set_creator("Prometheus")
    pdf.add_page()

    def write_line(line: str, size: float, indent: float = 0, lead: str = "") -> None:
        pdf.set_x(pdf.l_margin + indent)
        height = size * 0.5
        if lead:
            pdf.set_font(family, "", size)
            pdf.write(height, _plain(lead, uni))
        for piece, bold, url in _segments(line):
            pdf.set_font(family, "B" if bold else "", size)
            if url:
                pdf.set_text_color(*LINK_BLUE)
                pdf.write(height, _plain(piece, uni), link=url)
                pdf.set_text_color(0, 0, 0)
            else:
                pdf.write(height, _plain(piece, uni))
        pdf.ln(height + 1.5)

    in_code = False
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            pdf.set_font("Courier", "", 9)
            pdf.set_x(pdf.l_margin + 4)
            pdf.multi_cell(0, 4.5, _plain(line, False) or " ")
            continue
        if not line.strip():
            pdf.ln(2.5)
            continue
        h = re.match(r"^\s*(#{1,6})\s+(.*)$", line)
        if h:
            level = len(h.group(1))
            pdf.ln(2 if level > 1 else 0)
            write_line(h.group(2).replace("**", ""), {1: 20, 2: 15, 3: 13}.get(level, 12))
            continue
        b = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
        if b:
            write_line(b.group(2), 11, indent=4 + min(len(b.group(1)), 8) * 2, lead="•  ")
            continue
        n = re.match(r"^(\s*)(\d{1,3})[.)]\s+(.*)$", line)
        if n:
            write_line(n.group(3), 11, indent=4 + min(len(n.group(1)), 8) * 2, lead=f"{n.group(2)}.  ")
            continue
        write_line(line.strip(), 11)
    return bytes(pdf.output())
