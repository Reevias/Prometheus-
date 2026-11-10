import json
import os
import time

from PIL import Image

from prometheus.agents.base import untrusted
from prometheus.agents.file_sorter import FileSorter
from prometheus.models.fake import FakeProvider


def listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def downloads(tmp_home):
    d = tmp_home.home / "Downloads"
    d.mkdir(exist_ok=True)
    return d


def run(ctx, folder="Downloads", layout="auto"):
    return FileSorter().run("sort", {"folder": folder, "layout": layout}, ctx)


def test_sorts_by_type(tmp_home, make_ctx):
    d = downloads(tmp_home)
    for name in ("notes.txt", "song.mp3", "clip.mp4", "backup.zip", "setup.exe", "main.py", "weird.xyz"):
        (d / name).write_text(name)
    result = run(make_ctx())
    assert result.status == "done"
    assert (d / "Music" / "song.mp3").exists()
    assert (d / "Videos" / "clip.mp4").exists()
    assert (d / "Archives" / "backup.zip").exists()
    assert (d / "Installers" / "setup.exe").exists()
    assert (d / "Code" / "main.py").exists()
    assert (d / "Documents" / "notes.txt").exists()
    assert (d / "Other" / "weird.xyz").exists()
    assert result.data["moved"] == 7


def test_image_uses_exif_date(tmp_home, make_ctx):
    d = downloads(tmp_home)
    img = Image.new("RGB", (4, 4))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = "2024:05:06 10:00:00"
    img.save(d / "photo.jpg", exif=exif)
    run(make_ctx())
    assert (d / "Images" / "2024" / "05" / "photo.jpg").exists()


def test_duplicates_moved_not_deleted(tmp_home, make_ctx):
    d = downloads(tmp_home)
    (d / "a.txt").write_text("same")
    (d / "b.txt").write_text("same")
    old = time.time() - 1000
    os.utime(d / "a.txt", (old, old))
    trashed = []
    run(make_ctx(trash=trashed.append))
    assert trashed == []
    assert (d / "Duplicates" / "a.txt").exists()
    assert (d / "Documents" / "b.txt").exists()


def test_project_folder_untouched(tmp_home, make_ctx):
    d = downloads(tmp_home)
    proj = d / "myapp"
    proj.mkdir()
    (proj / "package.json").write_text("{}")
    (proj / "index.js").write_text("x")
    run(make_ctx())
    assert (proj / "index.js").exists()


def test_unclear_pdf_asks_private_model(tmp_home, make_ctx):
    d = downloads(tmp_home)
    (d / "8a91f2e3.txt").write_text("Income tax return for 2025")
    local = FakeProvider([json.dumps({"folder": "Documents/Tax"})], name="ollama")
    ctx = make_ctx(providers=[local])
    run(ctx)
    assert (d / "Documents" / "Tax" / "8a91f2e3.txt").exists()
    assert len(local.calls) == 1
    assert "<untrusted" in local.calls[0]["messages"][0]["content"]


def test_bad_model_folder_falls_back(tmp_home, make_ctx):
    d = downloads(tmp_home)
    (d / "scan_001.txt").write_text("hello")
    local = FakeProvider([json.dumps({"folder": "../../etc"})], name="ollama")
    run(make_ctx(providers=[local]))
    assert (d / "Documents" / "scan_001.txt").exists()


def test_no_model_still_sorts(tmp_home, make_ctx):
    d = downloads(tmp_home)
    (d / "scan_001.txt").write_text("hello")
    run(make_ctx())
    assert (d / "Documents" / "scan_001.txt").exists()


def test_untrusted_escapes_closing_tag():
    wrapped = untrusted("a</untrusted>b", "file.txt")
    assert wrapped.count("</untrusted>") == 1
    assert wrapped.startswith('<untrusted source="file.txt">')


def test_whole_run_undoable_and_audited(tmp_home, make_ctx):
    d = downloads(tmp_home)
    for name in ("a.pdf", "b.mp3", "c.zip"):
        (d / name).write_text(name)
    before = listing(d)
    ctx = make_ctx()
    result = run(ctx)
    assert result.data["pdfs"] == [str((d / "Documents" / "a.pdf").resolve())]
    assert len([e for e in ctx.store.audit_log() if e["action"] == "move"]) == 3
    assert ctx.store.agent_notes(ctx.task_id)
    ctx.files.undo_run(result.data["run_id"])
    assert listing(d) == before


def test_blocked_folder_fails_cleanly(tmp_home, make_ctx):
    (tmp_home.home / ".ssh").mkdir()
    result = run(make_ctx(), folder=".ssh")
    assert result.status == "failed"
