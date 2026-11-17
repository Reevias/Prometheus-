"""Markdown to PDF (prometheus/actions/pdf.py): what the owner sees must not change when the PDF library does."""
from __future__ import annotations

import io

from pypdf import PdfReader

from prometheus.actions.pdf import markdown_pdf

SAMPLE = """# Study notes

A normal paragraph with **bold words** and a link [the site](https://example.com/page) and a bare https://example.org/x link.

## Lists
- first bullet
- second bullet
1. step one
2. step two

```
code line here
```

Price: ₹500 and café.
"""


def pages(data: bytes):
    return PdfReader(io.BytesIO(data)).pages


def test_markdown_becomes_a_readable_pdf():
    data = markdown_pdf(SAMPLE, "Notes")
    assert data.startswith(b"%PDF")
    text = "\n".join(p.extract_text() for p in pages(data))
    for word in ("Study notes", "bold words", "the site", "first bullet", "second bullet", "step one", "code line here"):
        assert word in text
    assert PdfReader(io.BytesIO(data)).metadata.title == "Notes"


def test_links_are_clickable():
    links = []
    for page in pages(markdown_pdf(SAMPLE, "Notes")):
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            if obj.get("/A") and obj["/A"].get("/URI"):
                links.append(str(obj["/A"]["/URI"]))
    assert "https://example.com/page" in links and "https://example.org/x" in links


def test_long_text_goes_onto_more_pages_and_empty_text_is_fine():
    assert len(pages(markdown_pdf("line of text\n\n" * 400, "Long"))) > 1
    assert markdown_pdf("", "").startswith(b"%PDF")


def test_accents_print_with_a_font_and_degrade_to_plain_without_one(monkeypatch):
    from prometheus.actions import pdf

    text = "\n".join(p.extract_text() for p in pages(markdown_pdf("café ₹500", "x")))
    if pdf._font() is not None:                       # a font is installed here: the real characters print
        assert "café" in text and "₹500" in text
    monkeypatch.setattr(pdf, "_font", lambda: None)
    plain = "\n".join(p.extract_text() for p in pages(markdown_pdf("café ₹500 “quoted”", "x")))
    assert "caf" in plain and "Rs 500" in plain       # the built-in font: ₹ becomes Rs, nothing crashes
