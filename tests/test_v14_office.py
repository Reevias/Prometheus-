import io

from openpyxl import load_workbook
from pptx import Presentation

from prometheus.actions.office import markdown_pptx, markdown_xlsx
from prometheus.agents.files import Files

BUDGET = """# November budget
## Spending
| Item | Amount | Share |
|---|---|---|
| Rent | ₹12,000 | 60% |
| Food | ₹5,500 | 27.5% |
| Travel | 2500 | 12.5% |
Remember to save 10%.
"""

DECK = """# Photosynthesis
Class 10 Biology revision
## What it is
- Plants make food from light
- Needs CO2 and water
  - Happens in chloroplasts
Notes: start with a question to the class
## The equation
- 6CO2 + 6H2O → C6H12O6 + 6O2
"""


def test_excel_tables_numbers_totals_and_chart():
    wb = load_workbook(io.BytesIO(markdown_xlsx(BUDGET, "Budget")))
    ws = wb["Spending"]
    assert [c.value for c in ws[1]] == ["Item", "Amount", "Share"] and ws.freeze_panes == "A2"
    assert ws["B2"].value == 12000 and abs(ws["C3"].value - 0.275) < 1e-9          # real numbers, not text
    assert ws["A5"].value == "Total" and ws["B5"].value == "=SUM(B2:B4)"
    assert len(ws._charts) == 1 and wb["Notes"]["A2"].value == "Remember to save 10%."


def test_plain_csv_and_sentences():
    wb = load_workbook(io.BytesIO(markdown_xlsx("name,marks\nAsha,91\nRavi,85")))
    assert wb["Sheet"]["B2"].value == 91 and wb["Sheet"]["B4"].value == "=SUM(B2:B3)"
    wb = load_workbook(io.BytesIO(markdown_xlsx("Hello, this is a sentence.")))
    assert wb.sheetnames == ["Notes"]                                              # a sentence is not a table


def test_slides_title_bullets_nesting_and_notes():
    prs = Presentation(io.BytesIO(markdown_pptx(DECK)))
    slides = list(prs.slides)
    assert slides[0].shapes.title.text == "Photosynthesis"
    assert slides[0].placeholders[1].text == "Class 10 Biology revision"
    body = slides[1].placeholders[1].text_frame.paragraphs
    assert [p.text for p in body] == ["Plants make food from light", "Needs CO2 and water", "Happens in chloroplasts"]
    assert body[2].level == 1
    assert slides[1].notes_slide.notes_text_frame.text == "start with a question to the class"
    assert slides[2].shapes.title.text == "The equation"


def test_long_slide_continues():
    deck = "# T\n## Many\n" + "\n".join(f"- point {i}" for i in range(10))
    prs = Presentation(io.BytesIO(markdown_pptx(deck)))
    assert [s.shapes.title.text for s in prs.slides] == ["T", "Many", "Many (continued)"]


def test_files_agent_makes_them(make_ctx, tmp_home):
    ctx = make_ctx()
    assert Files().run("t", {"action": "create", "path": "Documents/budget.xlsx", "content": BUDGET}, ctx).status == "done"
    assert Files().run("t", {"action": "create", "path": "Desktop/bio.pptx", "content": DECK}, ctx).status == "done"
    assert load_workbook(tmp_home.home / "Documents" / "budget.xlsx")["Spending"]["A2"].value == "Rent"
    assert len(Presentation(str(tmp_home.home / "Desktop" / "bio.pptx")).slides) == 3
