"""Read text out of documents, locally."""
from __future__ import annotations

from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json"}


def extract_text(path: Path, limit: int = 8000) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            out = []
            for page in PdfReader(str(path)).pages:
                out.append(page.extract_text() or "")
                if sum(map(len, out)) >= limit:
                    break
            return "\n".join(out)[:limit]
        if suffix == ".docx":
            import docx

            d = docx.Document(str(path))
            parts = [p.text for p in d.paragraphs]
            for table in d.tables:
                parts += [" | ".join(c.text for c in row.cells) for row in table.rows]
            return "\n".join(parts)[:limit]
        if suffix == ".xlsx":
            from openpyxl import load_workbook

            wb = load_workbook(str(path), read_only=True, data_only=True)
            out, size = [], 0
            for ws in wb.worksheets:
                out.append(f"# {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    line = " | ".join("" if v is None else str(v) for v in row).strip(" |")
                    if line:
                        out.append(line)
                        size += len(line)
                    if size >= limit:
                        break
            wb.close()
            return "\n".join(out)[:limit]
        if suffix == ".pptx":
            from pptx import Presentation

            out = []
            for n, slide in enumerate(Presentation(str(path)).slides, 1):
                out.append(f"# Slide {n}")
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        out.append(shape.text_frame.text)
                if slide.has_notes_slide:
                    out.append(slide.notes_slide.notes_text_frame.text)
            return "\n".join(out)[:limit]
        if suffix in TEXT_SUFFIXES:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read(limit)
    except Exception:  # unreadable or damaged file: treat as empty
        return ""
    return ""
