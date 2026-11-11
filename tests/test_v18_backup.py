import json

import httpx
import pytest

from prometheus import backup
from prometheus.connectors.google import Google
from tests.conftest import login


def test_encrypt_round_trip_and_wrong_code(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "CHUNK", 1000)                     # several pieces
    src = tmp_path / "a.zip"
    src.write_bytes(bytes(range(256)) * 20)
    code = backup.new_recovery_code()
    salt = b"s" * 16
    backup.encrypt_file(src, tmp_path / "a.pbak", salt, backup.derive_key(code, salt))
    sealed = (tmp_path / "a.pbak").read_bytes()
    assert bytes(range(256)) not in sealed                          # scrambled
    backup.decrypt_file(tmp_path / "a.pbak", tmp_path / "back.zip", code.lower().replace("-", " "))
    assert (tmp_path / "back.zip").read_bytes() == src.read_bytes()
    with pytest.raises(backup.BackupError):
        backup.decrypt_file(tmp_path / "a.pbak", tmp_path / "x.zip", backup.new_recovery_code())
    cut = tmp_path / "cut.pbak"
    cut.write_bytes(sealed[:-500])                                  # a dropped last piece is noticed
    with pytest.raises(backup.BackupError):
        backup.decrypt_file(cut, tmp_path / "y.zip", code)


def test_backup_to_drive_and_restore(app_client, tmp_path):
    from prometheus.config import data_dir, get_secret, set_secret

    client, services = app_client()
    services.store.add_memory("Ravi's birthday is 12 March")
    set_secret("OPENROUTER_API_KEY", "sk-or-test-key-123")
    (services.home / "Documents" / "Prometheus Study").mkdir(parents=True, exist_ok=True)
    (services.home / "Documents" / "Prometheus Study" / "Quiz.pdf").write_bytes(b"%PDF-1.4")
    code = backup.set_up()
    uploads, calls = {}, []

    def drive(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/drive/v3/files":
            return httpx.Response(200, json={"id": "folder1"})
        if request.url.path.startswith("/upload/drive/v3/files"):
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/session/1"})
        if request.url.path == "/upload/session/1":
            uploads.setdefault("data", b"")
            uploads["data"] += request.content
            return httpx.Response(200, json={"id": "file1"})
        if request.method == "GET":
            return httpx.Response(200, json={"files": [{"id": f"old{i}", "name": f"b{i}.pbak"} for i in range(16)]})
        return httpx.Response(204)

    g = Google(httpx.Client(transport=httpx.MockTransport(drive)))
    g._access, g._until = "t", 9e18
    said = backup.run(services, google=g)
    assert "encrypted" in said and "removed 2 old" in said
    assert b"Ravi" not in uploads["data"] and uploads["data"].startswith(backup.MAGIC)
    pbak = tmp_path / "latest.pbak"
    pbak.write_bytes(uploads["data"])
    new_data, new_home = tmp_path / "newdata", tmp_path / "newhome"
    new_data.mkdir()
    out = backup.restore(pbak, code, new_data, new_home)
    assert "Restored" in out and (new_data / "memory.db").exists()
    assert "sk-or-test-key-123" in (new_data / ".env").read_text()          # keys came back
    assert (new_home / "Documents" / "Prometheus Study" / "Quiz.pdf").exists()
    import sqlite3

    rows = sqlite3.connect(str(new_data / "memory.db")).execute("SELECT text FROM memories").fetchall()
    assert ("Ravi's birthday is 12 March",) in rows


def test_settings_shows_the_code_once(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    from prometheus.config import set_secret

    set_secret("GOOGLE_TOKEN", "1//refresh-token-xyz")
    page = client.post("/settings/backup/setup", headers=h).text
    assert "Your recovery code" in page and services.settings.backup_on
    assert "Your recovery code" not in client.get("/settings").text          # never shown again
    assert client.post("/settings/backup/setup", headers=h).status_code == 400
