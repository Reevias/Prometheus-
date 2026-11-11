"""Markdown → Excel (.xlsx) and PowerPoint (.pptx).

Excel: each Markdown table (or CSV block) becomes a sheet (named by the heading above it), with a bold frozen
header, sensible column widths, real numbers, a Total row with SUM formulas, and a bar chart when there are
numbers to compare. Other text goes to a "Notes" sheet.

Slides: "# Title" makes the title slide (the next line is the subtitle), each "## Heading" a slide with its
bullets (indented bullets nest), "Notes: ..." lines become speaker notes, and long slides continue on a second one.
"""
from __future__ import annotations

import io
import re

SAFE_SHEET = re.compile(r"[\[\]:*?/\\]")
NUMBER = re.compile(r"^[-+]?[₹$€£]?\s?\d[\d,]*(\.\d+)?%?$")
MAX_BULLETS = 7


def _num(cell: str):
    raw = cell.strip()
    if not NUMBER.fullmatch(raw):
        return raw
    plain = re.sub(r"[₹$€£,\s]", "", raw)
    if plain.endswith("%"):
        return float(plain[:-1]) / 100
    return float(plain) if "." in plain else int(plain)


def _tables(text: str):
    """(name, rows) for each table; plain lines are gathered as notes."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if len(lines) >= 2 and all(l.count(",") == lines[0].count(",") >= 1 and not l.startswith(("#", "|"))
                               for l in lines):
        return [("Sheet", [[c.strip() for c in l.split(",")] for l in lines])], []      # plain CSV
    tables, notes, name, rows = [], [], "", []

    def close():
        nonlocal rows
        if rows:
            tables.append((name or f"Table {len(tables) + 1}", rows))
        rows = []

    for line in (text or "").splitlines():
        s = line.strip()
        h = re.match(r"^#{1,6}\s+(.*)$", s)
        if h:
            close()
            name = h.group(1).strip()
            continue
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue                                  # the |---|---| line
            rows.append(cells)
            continue
        close()
        if s:
            notes.append(s.lstrip("-* "))
    close()
    return tables, notes


def markdown_xlsx(text: str, title: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    tables, notes = _tables(text)
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    for name, rows in tables:
        sheet_name = (SAFE_SHEET.sub(" ", name).strip() or "Sheet")[:31]
        while sheet_name in used:
            sheet_name = sheet_name[:28] + f" {len(used)}"
        used.add(sheet_name)
        ws = wb.create_sheet(sheet_name)
        width = max(len(r) for r in rows)
        header, body = rows[0] + [""] * (width - len(rows[0])), [r + [""] * (width - len(r)) for r in rows[1:]]
        ws.append(header)
        for r in body:
            ws.append([_num(c) for c in r])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F3640")
            cell.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"
        numeric = [i for i in range(width) if body and all(isinstance(_num(r[i]), (int, float)) or not r[i].strip()
                                                           for r in body) and any(r[i].strip() for r in body)]
        last = len(body) + 1
        if numeric and len(body) >= 2:
            total = ["Total"] + [""] * (width - 1)
            ws.append(total)
            for i in numeric:
                col = get_column_letter(i + 1)
                ws.cell(row=last + 1, column=i + 1, value=f"=SUM({col}2:{col}{last})")
            for cell in ws[last + 1]:
                cell.font = Font(bold=True)
        for i in numeric:
            sample = next((_num(r[i]) for r in body if r[i].strip()), 0)
            fmt = "0%" if any(r[i].strip().endswith("%") for r in body) else ("#,##0.00" if isinstance(sample, float) else "#,##0")
            for row in ws.iter_rows(min_row=2, max_row=last + 1, min_col=i + 1, max_col=i + 1):
                row[0].number_format = fmt
        for i in range(width):
            longest = max(len(str(ws.cell(row=r, column=i + 1).value or "")) for r in range(1, last + 2))
            ws.column_dimensions[get_column_letter(i + 1)].width = min(60, max(10, longest + 2))
        if numeric and numeric[0] > 0 and 2 <= len(body) <= 40:
            chart = BarChart()
            chart.title = name
            chart.height, chart.width = 8, 16
            data = Reference(ws, min_col=numeric[0] + 1, max_col=numeric[-1] + 1, min_row=1, max_row=last)
            cats = Reference(ws, min_col=1, min_row=2, max_row=last)
            chart.add_data(data, titles_from_data=True)
            chart.set_categories(cats)
            ws.add_chart(chart, f"{get_column_letter(width + 2)}2")
    if notes or not tables:
        ws = wb.create_sheet("Notes")
        ws.append([title or "Notes"])
        ws["A1"].font = Font(bold=True, size=14)
        for n in notes or ["(no tables found: write tables as | a | b | lines)"]:
            ws.append([n])
        ws.column_dimensions["A"].width = 100
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def markdown_pptx(text: str, title: str = "") -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = 12192000, 6858000          # 16:9
    slides: list[dict] = []
    cur: dict | None = None
    deck_title, subtitle = title, ""
    for line in (text or "").splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        if s.startswith("# "):
            deck_title = s[2:].strip()
            cur = {"kind": "title"}
            continue
        if re.match(r"^#{2,6}\s+", s):
            cur = {"kind": "slide", "title": re.sub(r"^#+\s+", "", s).strip(), "bullets": [], "notes": []}
            slides.append(cur)
            continue
        note = re.match(r"^\s*(?:>\s*)?notes?:\s*(.*)$", s, re.I)
        if note and cur and cur.get("kind") == "slide":
            cur["notes"].append(note.group(1))
            continue
        if cur is not None and cur.get("kind") == "title" and not subtitle and not s.lstrip().startswith(("-", "*")):
            subtitle = s.strip()
            continue
        b = re.match(r"^(\s*)(?:[-*•]|\d+[.)])\s+(.*)$", s)
        if cur is None or cur.get("kind") != "slide":
            cur = {"kind": "slide", "title": deck_title or "Overview", "bullets": [], "notes": []}
            slides.append(cur)
        level = min(len(b.group(1)) // 2, 3) if b else 0
        cur["bullets"].append((level, (b.group(2) if b else s.strip()).replace("**", "")))

    first = prs.slides.add_slide(prs.slide_layouts[0])
    first.shapes.title.text = deck_title or "Presentation"
    if len(first.placeholders) > 1:
        first.placeholders[1].text = subtitle
    for sl in slides:
        chunks = [sl["bullets"][i:i + MAX_BULLETS] for i in range(0, max(1, len(sl["bullets"])), MAX_BULLETS)]
        for n, chunk in enumerate(chunks):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = sl["title"] + (" (continued)" if n else "")
            body = slide.placeholders[1].text_frame
            body.clear()
            for k, (level, words) in enumerate(chunk):
                p = body.paragraphs[0] if k == 0 else body.add_paragraph()
                p.text, p.level = words, level
                for run in p.runs:
                    run.font.size = Pt(22 if level == 0 else 18)
            if sl["notes"] and n == 0:
                slide.notes_slide.notes_text_frame.text = "\n".join(sl["notes"])
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()
