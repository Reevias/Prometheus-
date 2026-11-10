import os
import time
from pathlib import Path

import pytest

from prometheus.agents.files import Files, recycle_handler, resolve_place
from prometheus.safety.paths import PathBlocked, PathPolicy, extra_root_problem
from tests.conftest import login


@pytest.fixture
def setup(make_ctx, tmp_home, tmp_path):
    trashed = []
    ctx = make_ctx(trash=lambda p: (trashed.append(p), os.remove(p) if os.path.isfile(p) else None))
    home = tmp_home.home
    for rel, text in {"Downloads/tax-2025.pdf": "a", "Downloads/holiday.jpg": "b", "Documents/notes/tax plan.txt": "c",
                      "Documents/big.zip": "x" * 3_000_000, ".ssh/id_rsa": "secret", "AppData/Chrome/tax.pdf": "no",
                      "Desktop/report.docx": "r"}.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    drive = tmp_path / "D"
    (drive / "Photos").mkdir(parents=True)
    (drive / "Photos" / "goa beach.jpg").write_text("img")
    return ctx, home, drive, trashed


def run(ctx, **inputs):
    return Files().run("t", inputs, ctx)


def test_find_by_words_kind_and_size_skips_blocked_places(setup):
    ctx, home, _, _ = setup
    res = run(ctx, action="find", query="tax")
    names = {Path(p).name for p in res.data["paths"]}
    assert names == {"tax-2025.pdf", "tax plan.txt"}                # AppData copy never shown
    assert {Path(p).name for p in run(ctx, action="find", query="*.pdf").data["paths"]} == {"tax-2025.pdf"}
    assert {Path(p).name for p in run(ctx, action="find", kind="images").data["paths"]} == {"holiday.jpg"}
    assert {Path(p).name for p in run(ctx, action="find", min_size_mb="2").data["paths"]} == {"big.zip"}
    assert run(ctx, action="find", query="id_rsa").data["paths"] == []
    assert run(ctx, action="find", kind="pdf", where="Documents").data["paths"] == []
    assert run(ctx, action="find").status == "failed"


def test_find_modified_recently(setup):
    ctx, home, _, _ = setup
    old = home / "Downloads" / "holiday.jpg"
    os.utime(old, (time.time() - 90 * 86400, time.time() - 90 * 86400))
    names = {Path(p).name for p in run(ctx, action="find", kind="images", modified="this month").data["paths"]}
    assert "holiday.jpg" not in names


def test_extra_drive_is_searched_once_added(setup, tmp_path):
    ctx, home, drive, _ = setup
    assert run(ctx, action="find", query="goa").data["paths"] == []
    ctx.policy.extra_roots = [drive.resolve()]
    assert [Path(p).name for p in run(ctx, action="find", query="goa").data["paths"]] == ["goa beach.jpg"]


def test_list_and_info(setup):
    ctx, home, _, _ = setup
    res = run(ctx, action="list", folder="Documents")
    assert "notes/" in res.summary and "big.zip" in res.summary
    assert "2.9 MB" in run(ctx, action="info", path="Documents/big.zip").summary
    assert "folder with 2 files" in run(ctx, action="info", path="Documents").summary
    assert run(ctx, action="list", folder="~/.ssh").status == "failed"


def test_copy_move_rename_then_undo_everything(setup):
    ctx, home, _, trashed = setup
    found = run(ctx, action="find", query="tax").data["paths"]
    res = run(ctx, action="copy", paths=found, to="Desktop/Taxes")
    assert res.status == "done" and (home / "Desktop/Taxes/tax-2025.pdf").exists()
    assert (home / "Downloads/tax-2025.pdf").exists()                     # copy keeps the original
    run(ctx, action="move", paths=[str(home / "Desktop/report.docx")], to="Documents")
    assert (home / "Documents/report.docx").exists()
    run(ctx, action="rename", path="Documents/report.docx", new_name="report 2026.docx")
    assert (home / "Documents/report 2026.docx").exists()
    run(ctx, action="make_folder", path="Documents/Projects")
    assert (home / "Documents/Projects").is_dir()
    for r in ctx.files.journal.runs():
        ctx.files.undo_run(r["id"])
    assert (home / "Desktop/report.docx").exists() and not (home / "Documents/report 2026.docx").exists()
    assert not (home / "Desktop/Taxes/tax-2025.pdf").exists()            # the copies went to the Recycle Bin
    assert (home / "Downloads/tax-2025.pdf").exists()
    assert any("tax-2025.pdf" in t for t in trashed)


def test_move_between_drives_copies_and_keeps_original(setup):
    ctx, home, drive, _ = setup
    ctx.policy.extra_roots = [drive.resolve()]
    res = run(ctx, action="move", paths=[str(drive / "Photos/goa beach.jpg")], to="Pictures")
    assert res.status == "done" and "keeps the original" in res.summary
    assert (home / "Pictures/goa beach.jpg").exists() and (drive / "Photos/goa beach.jpg").exists()


def test_never_touches_blocked_or_protected_places(setup):
    ctx, home, _, _ = setup
    assert run(ctx, action="copy", paths=["~/.ssh/id_rsa"], to="Desktop").status == "failed"
    assert run(ctx, action="move", paths=["Downloads/holiday.jpg"], to="AppData/x").status == "failed"
    assert (home / "Downloads/holiday.jpg").exists()
    res = run(ctx, action="move", paths=["/etc/hostname"], to="Desktop")
    assert res.status == "failed"


def test_small_recycle_runs_big_one_waits_for_approval(setup):
    ctx, home, _, trashed = setup
    res = run(ctx, action="recycle", paths=["Downloads/holiday.jpg"])
    assert res.status == "done" and trashed and not (home / "Downloads/holiday.jpg").exists()
    many = []
    for i in range(12):
        p = home / "Downloads" / f"junk{i}.tmp"
        p.write_text("j")
        many.append(str(p))
    res = run(ctx, action="recycle", paths=many)
    assert res.status == "waiting_approval" and all(Path(p).exists() for p in many)
    pending = ctx.store.approvals()[0]
    assert pending.kind == "recycle_files"
    out = recycle_handler(pending.payload, ctx.policy, ctx.files)
    assert "Sent 12" in out and not any(Path(p).exists() for p in many)


def test_places_and_onedrive_folders(tmp_path):
    home = tmp_path / "h"
    (home / "OneDrive" / "Desktop").mkdir(parents=True)
    assert resolve_place("desktop/x.txt", home) == home / "OneDrive" / "Desktop" / "x.txt"
    assert resolve_place("Downloads", home) == home / "Downloads"
    assert resolve_place("anywhere", home) == home


def test_extra_place_rules(tmp_path):
    home = tmp_path / "home"
    (home / "x").mkdir(parents=True)
    (tmp_path / "D").mkdir()
    assert extra_root_problem(str(tmp_path / "D"), home) == ""
    assert "doesn't exist" in extra_root_problem(str(tmp_path / "nope"), home)
    assert "already inside" in extra_root_problem(str(home / "x"), home)
    assert extra_root_problem("/", home) and extra_root_problem("/etc", home) and extra_root_problem("/usr/lib", home)
    policy = PathPolicy(home=home, data_dir=tmp_path / "data", code_dir=tmp_path / "code",
                        extra_roots=[tmp_path / "D"])
    (tmp_path / "D" / "KeePass").mkdir()
    assert not policy.is_blocked(tmp_path / "D" / "a.txt")
    assert policy.is_blocked(tmp_path / "D" / "KeePass" / "db.kdbx")
    with pytest.raises(PathBlocked):
        policy.check_read(tmp_path / "elsewhere.txt")


def test_settings_add_and_remove_a_place(app_client, tmp_path):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    drive = tmp_path / "Drive"
    drive.mkdir()
    bad = client.post("/settings/places", data={"folder": str(drive), "password": "wrong password"}, headers=h)
    assert bad.status_code == 403 and services.settings.extra_folders == []
    ok = client.post("/settings/places", data={"folder": str(drive), "password": "correct horse battery"}, headers=h)
    assert ok.status_code == 200 and services.settings.extra_folders == [str(drive.resolve())]
    assert drive.resolve() in services.policy.extra_roots                      # works at once
    sys_try = client.post("/settings/places", data={"folder": "/etc", "password": "correct horse battery"},
                          headers=h)
    assert sys_try.status_code == 400 and "system" in sys_try.text
    client.post("/settings/places/remove", data={"folder": str(drive.resolve())}, headers=h)
    assert services.settings.extra_folders == [] and services.policy.extra_roots == []
