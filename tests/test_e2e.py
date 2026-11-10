import io
import json
import os
import time

import pytest
from fpdf import FPDF
from PIL import Image

from prometheus import cli
from prometheus.app import build_services
from prometheus.auth import hash_password
from prometheus.config import Settings, load_settings, save_settings
from prometheus.models.fake import FakeProvider


def make_pdf(path, text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, text)
    pdf.output(str(path))


def listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def brain(messages, system):
    if "lead AI" in system:
        return json.dumps({"steps": [
            {"id": 1, "agent": "file_sorter", "goal": "Sort Downloads",
             "inputs": [{"key": "folder", "value": "Downloads"}], "depends_on": []},
            {"id": 2, "agent": "documents", "goal": "Summarise tax PDFs",
             "inputs": [{"key": "paths", "value": "$step1.pdfs"}, {"key": "question", "value": "tax"}],
             "depends_on": [1]},
        ]})
    if "documents privately" in system:
        relevant = "Income Tax" in messages[0]["content"]
        return json.dumps({"relevant": relevant, "summary": "2025 income tax return" if relevant else "menu"})
    return "Sorted your Downloads and summarised 1 tax PDF."


def test_e2e_sort_downloads_and_summarise_tax_pdfs(tmp_home):
    d = tmp_home.home / "Downloads"
    d.mkdir()
    make_pdf(d / "tax_2025.pdf", "Income Tax Return 2025")
    make_pdf(d / "menu.pdf", "Pizza menu")
    Image.new("RGB", (4, 4)).save(d / "photo.jpg")
    (d / "notes_a.txt").write_text("same text")
    (d / "notes_b.txt").write_text("same text")
    old = time.time() - 5000
    os.utime(d / "notes_a.txt", (old, old))
    (d / "backup.zip").write_bytes(b"PK\x03\x04")
    before = listing(d)

    settings = Settings(routes={"plan": ["fake"], "combine": ["fake"], "research": ["fake"], "private": ["fake"]})
    services = build_services(settings, providers={"fake": FakeProvider(brain, name="fake")}, home=tmp_home.home)
    chat = services.store.create_chat("main")
    out = services.orchestrator.run(chat, "sort my Downloads folder and summarise any PDFs about taxes")

    assert out.status == "done"
    assert out.answer == "Sorted your Downloads and summarised 1 tax PDF."
    assert (d / "Documents" / "tax_2025.pdf").exists()
    assert (d / "Archives" / "backup.zip").exists()
    assert (d / "Duplicates" / "notes_a.txt").exists()
    steps = services.store.steps(out.task_id)
    assert [s.status for s in steps] == ["done", "done"]
    assert [x["path"].endswith("tax_2025.pdf") for x in steps[1].data["summaries"]] == [True]
    assert services.store.messages(chat)[-1].text == out.answer
    assert len([e for e in services.store.audit_log() if e["action"] == "move"]) == 6
    services.files.undo_run(steps[0].data["run_id"])
    assert listing(d) == before


def with_password(tmp_home):
    save_settings(Settings(password_hash=hash_password("a long password!")))


def test_cli_init_sets_password(tmp_home, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli.main(["init", "--password-stdin"]) == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("a long password!\n"))
    assert cli.main(["init", "--password-stdin"]) == 0
    assert load_settings().password_hash.startswith("scrypt$")
    assert "a long password!" not in capsys.readouterr().out


def test_cli_serve_refuses_public_bind(tmp_home, monkeypatch):
    with_password(tmp_home)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: calls.append((host, port)))
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 2
    assert calls == []
    assert cli.main(["serve"]) == 0
    assert calls == [("127.0.0.1", 8000)]


def test_cli_serve_needs_password(tmp_home, monkeypatch):
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: pytest.fail("should not start"))
    assert cli.main(["serve"]) == 2


def test_cli_doctor_runs(tmp_home, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "Data folder" in out and "anthropic" in out and "sk-test-123" not in out
