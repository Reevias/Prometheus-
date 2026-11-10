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

            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)[:limit]
        if suffix in TEXT_SUFFIXES:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read(limit)
    except Exception:  # unreadable or damaged file: treat as empty
        return ""
    return ""
