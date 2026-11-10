import json

import pytest
from fpdf import FPDF

from prometheus.agents.documents import Documents
from prometheus.agents.textutil import extract_text
from prometheus.models.base import ProviderUnavailable
from prometheus.models.fake import FakeProvider


def make_pdf(path, text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, text)
    pdf.output(str(path))


def judge(messages, system):
    content = messages[0]["content"]
    relevant = "Tax" in content
    return json.dumps({"relevant": relevant, "summary": "Tax return summary" if relevant else "other"})


def test_extracts_pdf_text(tmp_path):
    make_pdf(tmp_path / "t.pdf", "Income Tax 2025")
    assert "Income Tax 2025" in extract_text(tmp_path / "t.pdf")


def test_only_relevant_returned(tmp_home, make_ctx):
    docs = tmp_home.home / "Documents"
    docs.mkdir()
    make_pdf(docs / "tax.pdf", "Income Tax Return 2025")
    make_pdf(docs / "menu.pdf", "Pizza menu")
    local = FakeProvider(judge, name="ollama")
    result = Documents().run("summarise tax", {"paths": [str(docs / "tax.pdf"), str(docs / "menu.pdf")],
                                               "question": "tax"}, make_ctx(providers=[local]))
    assert result.status == "done"
    assert [s["path"] for s in result.data["summaries"]] == [str((docs / "tax.pdf").resolve())]
    assert "Tax return summary" in result.summary


def test_uses_private_purpose_only(tmp_home, make_ctx):
    docs = tmp_home.home / "Documents"
    docs.mkdir()
    (docs / "a.txt").write_text("Tax notes")
    cloud = FakeProvider(judge, name="anthropic", is_local=False)
    local = FakeProvider(judge, name="ollama")
    ctx = make_ctx(providers=[cloud, local], routes={"private": ["ollama"], "plan": ["anthropic"]})
    Documents().run("x", {"paths": [str(docs / "a.txt")], "question": "tax"}, ctx)
    assert cloud.calls == [] and len(local.calls) == 1


def test_blocked_path_reported_not_read(tmp_home, make_ctx):
    ssh = tmp_home.home / ".ssh"
    ssh.mkdir()
    (ssh / "id").write_text("SECRET")
    local = FakeProvider(judge, name="ollama")
    result = Documents().run("x", {"paths": [str(ssh / "id")], "question": ""}, make_ctx(providers=[local]))
    assert local.calls == []
    assert "not allowed" in result.summary.lower()


def test_no_model_pauses_cleanly(tmp_home, make_ctx):
    docs = tmp_home.home / "Documents"
    docs.mkdir()
    (docs / "a.txt").write_text("Tax notes")
    with pytest.raises(ProviderUnavailable, match="No AI model"):
        Documents().run("x", {"paths": [str(docs / "a.txt")], "question": "tax"}, make_ctx())
