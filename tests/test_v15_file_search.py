import os
import time

from prometheus import file_index
from prometheus.actions.office import markdown_pptx, markdown_xlsx
from prometheus.agents.files import Files
from tests.conftest import login


def make_files(home):
    docs = home / "Documents"
    (docs / "bills").mkdir(parents=True)
    (docs / "bills" / "march.txt").write_text("Invoice 4521 from Sharma Electricals, total Rs 3,400")
    (docs / "budget.xlsx").write_bytes(markdown_xlsx("| Item | Cost |\n|---|---|\n| Hostel rent | 9000 |\n| Mess | 3000 |"))
    (docs / "bio.pptx").write_bytes(markdown_pptx("# Biology\n## Photosynthesis\n- chlorophyll traps sunlight"))
    (home / "Downloads").mkdir()
    (home / "Downloads" / "notes.md").write_text("Kirchhoff's current law: currents into a node sum to zero")
    (home / "Music").mkdir()
    (home / "Music" / "secret.txt").write_text("Invoice 4521 copy outside the main folders")


def test_reads_main_folders_and_finds_by_contents(make_ctx):
    ctx = make_ctx()
    make_files(ctx.home)
    index = file_index.FileIndex(ctx.store, ctx.policy, pause=0)
    first = index.run()
    assert first["read"] == 4 and first["total"] == 4
    assert index.run()["read"] == 0                                   # unchanged files aren't read again
    file_index.CURRENT = index
    res = Files().run("x", {"action": "search_text", "query": "invoice 4521"}, ctx)
    assert "march.txt" in res.summary and "secret.txt" not in res.summary and "Sharma" in res.summary
    assert "budget.xlsx" in Files().run("x", {"action": "search_text", "query": "hostel rent"}, ctx).summary
    assert "bio.pptx" in Files().run("x", {"action": "search_text", "query": "chlorophyll"}, ctx).summary
    # a changed file is read again; a deleted one is forgotten
    note = ctx.home / "Downloads" / "notes.md"
    note.write_text("Ohm's law: V = I R")
    os.utime(note, (time.time() + 5, time.time() + 5))
    (ctx.home / "Documents" / "bills" / "march.txt").unlink()
    again = index.run()
    assert again["read"] == 1 and again["forgotten"] == 1
    assert "march.txt" not in Files().run("x", {"action": "search_text", "query": "invoice 4521"}, ctx).summary
    assert "notes.md" in Files().run("x", {"action": "search_text", "query": "ohm"}, ctx).summary


def test_only_reads_at_night_when_on_the_clock(make_ctx):
    ctx = make_ctx()
    make_files(ctx.home)
    noon = time.mktime((2026, 11, 11, 12, 0, 0, 0, 0, -1))
    night = time.mktime((2026, 11, 12, 1, 0, 0, 0, 0, -1))
    index = file_index.FileIndex(ctx.store, ctx.policy, clock=lambda: noon, pause=0)
    assert index.tick() is None
    index.clock = lambda: night
    assert index.tick()["read"] == 4


def test_read_now_button(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    r = client.post("/settings/read-files", headers=h)
    assert r.status_code == 200 and "Reading your files" in r.text
