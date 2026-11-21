"""M4.8 A notes file (NOTES.md) per project: dated entries, safe with two writers, never shown as HTML."""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from conftest import login

from prometheus import projects as pj


def _project(services, name="Notes demo"):
    pid = pj.create(services.store, services.policy, services.home, name)
    return pid, Path(pj.get(services.store, pid)["folder"]) / "NOTES.md"


def test_two_threads_adding_at_once_lose_nothing(app_client):
    _, services = app_client()
    pid, path = _project(services)
    errors = []

    def go(i):
        try:
            pj.add_note_entry(services.store, pid, "Decisions", f"entry number {i}")
        except Exception as e:             # pragma: no cover - shown by the assert below
            errors.append(e)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    text = path.read_text(encoding="utf-8")
    for i in range(12):
        assert f"entry number {i}\n" in text + "\n"
    assert len(re.findall(r"^- \d{4}-\d{2}-\d{2}: entry number", text, re.M)) == 12


def test_a_failed_write_leaves_the_old_file_alone(app_client, monkeypatch):
    _, services = app_client()
    pid, path = _project(services)
    pj.add_note_entry(services.store, pid, "Changes", "first")
    before = path.read_bytes()

    def boom(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(pj, "safe_replace_with_backoff", boom)
    with pytest.raises(OSError):
        pj.add_note_entry(services.store, pid, "Changes", "second")
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []     # no half-written leftovers


def test_sections_dates_and_rebuild(app_client):
    _, services = app_client()
    pid, path = _project(services)
    pj.add_note_entry(services.store, pid, "What it is", "A lab report")
    pj.add_note_entry(services.store, pid, "Decisions", "Use IEEE style")
    pj.add_note_entry(services.store, pid, "Changes", "Added chapter 1")
    text = path.read_text(encoding="utf-8")
    assert text.index("## What it is") < text.index("## Decisions") < text.index("## Changes")
    assert re.search(r"## Decisions\n+- \d{4}-\d{2}-\d{2}: Use IEEE style", text)
    with pytest.raises(pj.ProjectError):
        pj.add_note_entry(services.store, pid, "Nonsense", "x")
    with pytest.raises(pj.ProjectError):
        pj.add_note_entry(services.store, pid, "Decisions", "   ")
    path.write_bytes(b"\xff\xfe\x00 not text \x80")                       # corrupt
    pj.add_note_entry(services.store, pid, "Decisions", "after the damage")
    text = path.read_text(encoding="utf-8")
    assert "rebuilt" in text.lower() and "after the damage" in text and "## Changes" in text
    path.unlink()                                                          # missing
    pj.add_note_entry(services.store, pid, "Decisions", "from nothing")
    assert "from nothing" in path.read_text(encoding="utf-8")


def test_a_script_in_a_note_is_shown_as_text(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    pid, _ = _project(services)
    pj.add_note_entry(services.store, pid, "Decisions", "<script>alert(1)</script>")
    page = client.get(f"/v3/projects/{pid}?tab=notes").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    r = client.post(f"/api/v3/projects/{pid}/decisions", data={"text": "Use <b>bold</b>"}, headers=h)
    assert r.status_code == 200, r.text
    page = client.get(f"/v3/projects/{pid}?tab=notes").text
    assert "Use &lt;b&gt;bold&lt;/b&gt;" in page and "Add a decision" in page


def test_add_a_decision_needs_the_csrf_header(app_client):
    client, services = app_client()
    token = login(client)
    pid, path = _project(services)
    r = client.post(f"/api/v3/projects/{pid}/decisions", data={"text": "sneaky"})
    assert r.status_code in (400, 403)
    assert not path.exists() or "sneaky" not in path.read_text(encoding="utf-8")
    ok = client.post(f"/api/v3/projects/{pid}/decisions", data={"text": "fine"}, headers={"X-CSRF-Token": token})
    assert ok.status_code == 200 and "fine" in path.read_text(encoding="utf-8")      # the same call with the header works
    assert client.post(f"/api/v3/projects/{pid}/decisions", data={"text": " "},
                       headers={"X-CSRF-Token": token}).status_code == 400


def test_a_damaged_file_is_kept_and_the_page_says_so(app_client):
    client, services = app_client()
    login(client)
    pid, path = _project(services)
    pj.add_note_entry(services.store, pid, "Decisions", "keep me")
    path.write_bytes(b"\xff\xfe broken")
    pj.add_note_entry(services.store, pid, "Decisions", "new one")
    assert (path.parent / "NOTES.broken.md").read_bytes() == b"\xff\xfe broken"
    page = client.get(f"/v3/projects/{pid}?tab=notes").text
    assert "NOTES.broken.md" in page


def test_a_notes_file_that_is_a_link_is_refused(app_client):
    _, services = app_client()
    pid, path = _project(services)
    target = path.parent.parent / "elsewhere.txt"
    target.write_text("secret", encoding="utf-8")
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("this machine can't make links")
    with pytest.raises(pj.ProjectError):
        pj.add_note_entry(services.store, pid, "Decisions", "x")
    assert target.read_text(encoding="utf-8") == "secret"
