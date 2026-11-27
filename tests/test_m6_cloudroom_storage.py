"""M6 step 6.3: Cloudroom storage. Everything is sealed on the laptop (backup.py's AES-256-GCM) before it is queued or
uploaded; the laptop copy wins on a clash; uploads wait while offline; one Features switch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from prometheus import backup
from prometheus import cloudroom_storage as cs
from prometheus.config import Settings
from prometheus.features import BY_KEY, on
from prometheus.memory.store import Store

SALT = b"s" * 16
KEY = backup.derive_key("AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF", SALT)
OTHER_KEY = backup.derive_key("ZZZZZ-YYYYY-XXXXX-WWWWW-VVVVV-UUUUU", SALT)
SECRET = b"my very private plan about widgets"


class FakeDrive:
    """Google Drive in memory: folders, files with the sha the laptop recorded, and a switch for 'offline'."""

    def __init__(self):
        self.online = True
        self.folders: dict[tuple, str] = {}
        self.files: dict[str, dict] = {}
        self.n = 0

    def _up(self):
        if not self.online:
            raise cs.Offline("no connection")

    def ensure_folder(self, name, parent=None):
        self._up()
        if (name, parent) not in self.folders:
            self.n += 1
            self.folders[(name, parent)] = f"folder{self.n}"
        return self.folders[(name, parent)]

    def find(self, name, parent):
        self._up()
        for fid, f in self.files.items():
            if f["name"] == name and f["parent"] == parent:
                return {"id": fid, "sha": f["sha"]}
        return None

    def put(self, name, path, parent, sha, replace_id=None):
        self._up()
        data = Path(path).read_bytes()
        if replace_id:
            self.files[replace_id].update(bytes=data, sha=sha)
            return replace_id
        self.n += 1
        self.files[f"file{self.n}"] = {"name": name, "parent": parent, "sha": sha, "bytes": data, "order": self.n}
        return f"file{self.n}"

    def fetch(self, file_id, dst):
        self._up()
        Path(dst).write_bytes(self.files[file_id]["bytes"])

    def keep_other_copy(self, file_id, from_parent, to_parent, new_name):
        self._up()
        self.files[file_id].update(parent=to_parent, name=new_name)

    def list(self, parent):
        self._up()
        return [{"id": i, "name": f["name"], "order": f["order"]} for i, f in self.files.items() if f["parent"] == parent]

    def delete(self, file_id):
        self._up()
        del self.files[file_id]

    def in_folder(self, folder_name):
        fid = next(v for (n, _), v in self.folders.items() if n == folder_name)
        return {f["name"]: f for f in self.files.values() if f["parent"] == fid}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "memory.db")


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def storage(store, drive, tmp_path):
    return cs.Storage(store, tmp_path / "outbox", drive=drive, key=lambda: (SALT, KEY))


# -- item 1: sealed before anything leaves ---------------------------------------------------------------------------
def test_backup_py_can_seal_and_open_bytes_with_the_stored_key():
    sealed = backup.encrypt_bytes(SECRET, SALT, KEY)
    assert sealed.startswith(backup.MAGIC2) and SECRET not in sealed
    assert backup.decrypt_bytes(sealed, KEY, SALT) == SECRET
    with pytest.raises(backup.BackupError):
        backup.decrypt_bytes(sealed, OTHER_KEY, SALT)
    with pytest.raises(backup.BackupError):
        backup.decrypt_bytes(sealed[:-20], KEY, SALT)


def test_a_queued_item_is_scrambled_on_the_laptops_disk(storage, tmp_path):
    storage.queue("knowledge", "repo-knowledge.pcr", SECRET)
    blobs = [p for p in (tmp_path / "outbox").iterdir() if p.suffix == ".blob"]
    assert len(blobs) == 1 and blobs[0].read_bytes().startswith(backup.MAGIC2)
    assert all(SECRET not in p.read_bytes() for p in (tmp_path / "outbox").iterdir())


def test_the_upload_holds_only_scrambled_bytes(storage, drive):
    storage.queue("knowledge", "repo-knowledge.pcr", SECRET)
    storage.flush()
    assert drive.files and all(f["bytes"].startswith(backup.MAGIC2) and SECRET not in f["bytes"]
                               for f in drive.files.values())


def test_nothing_that_is_not_sealed_is_ever_uploaded(storage, drive, tmp_path):
    out = tmp_path / "outbox"
    out.mkdir(exist_ok=True)
    (out / "evil.blob").write_bytes(b"plain text, not sealed")
    (out / "evil.blob.json").write_text(json.dumps({"kind": "knowledge", "name": "evil.pcr", "sha": "x", "queued": 1}))
    res = storage.flush()
    assert drive.files == {} and res["sent"] == 0 and res["refused"] == 1


def test_a_wrong_key_cannot_read_what_was_stored(storage, drive, store, tmp_path):
    storage.queue("knowledge", "repo-knowledge.pcr", SECRET)
    storage.flush()
    stranger = cs.Storage(store, tmp_path / "outbox2", drive=drive, key=lambda: (SALT, OTHER_KEY))
    with pytest.raises(backup.BackupError):
        stranger.open_item("knowledge", "repo-knowledge.pcr")
    assert storage.open_item("knowledge", "repo-knowledge.pcr") == SECRET


# -- item 2: the Cloudroom folder ------------------------------------------------------------------------------------
def test_everything_goes_into_the_prometheus_cloudroom_folder(storage, drive):
    storage.queue("knowledge", "repo-knowledge.pcr", b"a")
    storage.queue("sync", "sync-data.pcr", b"b")
    storage.queue("private", "private-chat-4.pcr", b"c")
    storage.flush()
    root = drive.folders[("Prometheus Cloudroom", None)]
    sub = {n: v for (n, p), v in drive.folders.items() if p == root}
    assert {"Repo knowledge", "Sync data", "Private notes"} <= set(sub)
    assert drive.in_folder("Repo knowledge").keys() == {"repo-knowledge.pcr"}
    assert drive.in_folder("Sync data").keys() == {"sync-data.pcr"}
    assert drive.in_folder("Private notes").keys() == {"private-chat-4.pcr"}


def test_a_name_cannot_walk_out_of_its_folder(storage):
    with pytest.raises(cs.StorageError):
        storage.queue("knowledge", "../../x.pcr", b"a")
    with pytest.raises(cs.StorageError):
        storage.queue("nonsense", "x.pcr", b"a")


# -- item 3: the laptop wins ---------------------------------------------------------------------------------------------
def test_a_normal_update_just_replaces_the_cloud_copy(storage, drive):
    storage.queue("sync", "sync-data.pcr", b"one")
    storage.flush()
    storage.queue("sync", "sync-data.pcr", b"two")
    res = storage.flush()
    assert res["sent"] == 1 and res["clashes"] == 0
    assert storage.open_item("sync", "sync-data.pcr") == b"two"
    assert "Other copies" not in {n for (n, _) in drive.folders}


def test_on_a_clash_the_laptop_wins_and_the_other_copy_is_kept(storage, drive):
    storage.queue("sync", "sync-data.pcr", b"laptop v1")
    storage.flush()
    other = next(f for f in drive.files.values())
    other.update(bytes=backup.encrypt_bytes(b"changed on another device", SALT, KEY),
                 sha=hashlib.sha256(b"changed on another device").hexdigest())          # someone else changed it
    storage.queue("sync", "sync-data.pcr", b"laptop v2")
    res = storage.flush()
    assert res["clashes"] == 1
    assert storage.open_item("sync", "sync-data.pcr") == b"laptop v2"                   # the laptop's copy is the main one
    kept = drive.in_folder("Other copies")
    assert len(kept) == 1 and next(iter(kept)).startswith("sync-data.pcr (other copy ")
    assert backup.decrypt_bytes(next(iter(kept.values()))["bytes"], KEY, SALT) == b"changed on another device"


def test_a_copy_that_was_never_uploaded_from_this_laptop_is_a_clash_too(storage, drive, store, tmp_path):
    other = cs.Storage(store, tmp_path / "other-outbox", drive=drive, key=lambda: (SALT, KEY))
    other.queue("sync", "sync-data.pcr", b"from elsewhere")
    other.flush()
    store.kv_set("cloudroom_state", {})                              # this laptop has no record of it
    storage.queue("sync", "sync-data.pcr", b"mine")
    assert storage.flush()["clashes"] == 1 and storage.open_item("sync", "sync-data.pcr") == b"mine"


# -- item 4: offline ------------------------------------------------------------------------------------------------------
def test_offline_uploads_wait_and_go_later_and_nothing_is_lost(storage, drive, tmp_path):
    drive.online = False
    assert storage.queue("knowledge", "repo-knowledge.pcr", SECRET) is True                 # queuing needs no internet
    res = storage.flush()
    assert res["sent"] == 0 and res["waiting"] == 1 and "offline" in res["message"].lower()
    assert storage.pending() == ["repo-knowledge.pcr"]
    assert storage.open_item("knowledge", "repo-knowledge.pcr") == SECRET                  # still readable offline
    drive.online = True
    res = storage.flush()
    assert res["sent"] == 1 and res["waiting"] == 0 and storage.pending() == []
    assert not [p for p in (tmp_path / "outbox").iterdir() if p.suffix == ".blob"]


def test_a_waiting_upload_survives_a_restart(store, drive, tmp_path):
    first = cs.Storage(store, tmp_path / "outbox", drive=drive, key=lambda: (SALT, KEY))
    drive.online = False
    first.queue("sync", "sync-data.pcr", b"x")
    first.flush()
    drive.online = True
    again = cs.Storage(store, tmp_path / "outbox", drive=drive, key=lambda: (SALT, KEY))
    assert again.pending() == ["sync-data.pcr"] and again.flush()["sent"] == 1


def test_unchanged_content_is_not_queued_again(storage):
    assert storage.queue("sync", "sync-data.pcr", b"same") is True
    storage.flush()
    assert storage.queue("sync", "sync-data.pcr", b"same") is False
    assert storage.queue("sync", "sync-data.pcr", b"different") is True


def test_not_signed_in_to_google_keeps_everything_waiting(storage, drive):
    class NoGoogle(FakeDrive):
        def ensure_folder(self, name, parent=None):
            from prometheus.connectors.google import GoogleError
            raise GoogleError("Google isn't connected. Sign in with Google in Settings, under Connections.")

    storage.drive = NoGoogle()
    storage.queue("sync", "sync-data.pcr", b"x")
    res = storage.flush()
    assert res["sent"] == 0 and res["waiting"] == 1 and "Google isn't connected" in res["message"]


def test_old_cloud_backups_are_tidied_to_the_newest_14(storage, drive):
    for i in range(16):
        storage.queue_sealed("backups", f"Prometheus laptop {i:02d}.pbak", backup.encrypt_bytes(b"b%d" % i, SALT, KEY))
        storage.flush()
    names = sorted(drive.in_folder("Backups"))
    assert len(names) == backup.KEEP and names[0] == "Prometheus laptop 02.pbak"


# -- item 5: knowledge, sync data and the private notes of left-out chats ----------------------------------------------
def services_for(store, left_out=()):
    return SimpleNamespace(store=store, settings=Settings(cloudroom_storage=True),
                           archive=SimpleNamespace(left_out=lambda cid: cid in left_out))


def test_snapshot_queues_repo_knowledge_sync_data_and_private_notes(storage, store, drive):
    store.add_document("repo:acme/tool:1", "acme/tool: README", "from acme/tool at abc1234",
                       "Widgets are made with the acme tool. " * 5, True)
    open_chat = store.create_chat("Open chat")
    store.add_message(open_chat, "user", "hello world", "fake")
    secret_chat = store.create_chat("Left out chat")
    store.add_message(secret_chat, "user", "my private plan about widgets", "fake")
    store.add_message(secret_chat, "assistant", "noted privately", "fake")
    queued = storage.snapshot(services_for(store, left_out={secret_chat}))
    assert set(queued) == {"repo-knowledge.pcr", "sync-data.pcr", f"private-chat-{secret_chat}.pcr"}
    storage.flush()
    know = json.loads(storage.open_item("knowledge", "repo-knowledge.pcr"))
    assert know["notes"][0]["source"] == "from acme/tool at abc1234" and "Widgets are made" in know["notes"][0]["text"]
    assert json.loads(storage.open_item("sync", "sync-data.pcr"))["chats"]
    assert all(b"private plan" not in f["bytes"] for f in drive.files.values())          # all scrambled


def test_only_left_out_chats_have_private_notes_and_they_can_be_looked_up_later(storage, store, drive):
    kept = store.create_chat("Kept chat")
    store.add_message(kept, "user", "an ordinary chat", "fake")
    secret = store.create_chat("Left out chat")
    store.add_message(secret, "user", "my private plan about widgets", "fake")
    queued = storage.snapshot(services_for(store, left_out={secret}))
    assert f"private-chat-{kept}.pcr" not in queued
    storage.flush()
    notes = storage.lookup_private(secret)
    assert notes["title"] == "Left out chat" and notes["messages"][0]["text"] == "my private plan about widgets"
    drive.online = False
    with pytest.raises(cs.Offline):                                     # the cloud copy needs a connection
        storage.lookup_private(secret)
    drive.online = True
    with pytest.raises(cs.StorageError):
        storage.lookup_private(kept)                                    # nothing was stored for it


def test_a_private_note_cannot_be_looked_up_with_the_wrong_key(storage, store, drive, tmp_path):
    secret = store.create_chat("Left out chat")
    store.add_message(secret, "user", "my private plan", "fake")
    storage.snapshot(services_for(store, left_out={secret}))
    storage.flush()
    stranger = cs.Storage(store, tmp_path / "o2", drive=drive, key=lambda: (SALT, OTHER_KEY))
    with pytest.raises(backup.BackupError):
        stranger.lookup_private(secret)


def test_snapshot_twice_without_changes_queues_nothing(storage, store):
    chat = store.create_chat("c")
    store.add_message(chat, "user", "hi", "fake")
    svc = services_for(store)
    assert storage.snapshot(svc)
    storage.flush()
    assert storage.snapshot(svc) == []


# -- item 6: the switch ----------------------------------------------------------------------------------------------------
def test_the_features_switch_exists_and_is_off_by_default():
    assert BY_KEY["cloudroom_storage"].label == "Cloudroom storage"
    assert on(Settings(), "cloudroom_storage") is False


def test_with_the_switch_off_nothing_is_queued_or_uploaded(store, tmp_path, drive):
    svc = SimpleNamespace(store=store, settings=Settings(), archive=SimpleNamespace(left_out=lambda c: False),
                          home=tmp_path)
    assert cs.run_once(svc, drive=drive, outbox=tmp_path / "ob") == "Cloudroom storage is off."
    assert drive.files == {} and not (tmp_path / "ob").exists()


def test_run_once_with_the_switch_on_queues_and_uploads(store, tmp_path, drive, tmp_home):
    code = backup.set_up()
    chat = store.create_chat("c")
    store.add_message(chat, "user", "hi", "fake")
    svc = SimpleNamespace(store=store, settings=Settings(cloudroom_storage=True),
                          archive=SimpleNamespace(left_out=lambda c: False), home=tmp_path)
    msg = cs.run_once(svc, drive=drive, outbox=tmp_path / "ob")
    assert "uploaded 1" in msg and drive.in_folder("Sync data")
    assert code                                                      # the same recovery code as the backups


def test_without_a_recovery_code_it_says_so_in_plain_words(store, tmp_path, drive, tmp_home):
    svc = SimpleNamespace(store=store, settings=Settings(cloudroom_storage=True),
                          archive=SimpleNamespace(left_out=lambda c: False), home=tmp_path)
    with pytest.raises(cs.StorageError, match="recovery code"):
        cs.run_once(svc, drive=drive, outbox=tmp_path / "ob")


def test_the_nightly_job_runs_it(tmp_path):
    src = (Path(cs.__file__).parent / "daily.py").read_text()
    assert "cloudroom_storage" in src and "run_once" in src


# -- backups go to the Cloudroom folder when the switch is on -------------------------------------------------------------
def test_a_backup_goes_sealed_to_the_cloudroom_folder_when_the_switch_is_on(app_client, drive, tmp_path):
    client, services = app_client()
    services.settings.cloudroom_storage = True
    code = backup.set_up()

    class NoLegacyUpload:
        def __getattr__(self, name):
            raise AssertionError("the old Backups folder must not be used while Cloudroom storage is on")

    msg = backup.run(services, google=NoLegacyUpload())
    assert "Cloudroom" in msg
    store = cs.Storage(services.store, tmp_path / "unused", drive=drive)
    store.outbox = cs.outbox_dir()
    res = store.flush()
    assert res["sent"] == 1
    blob = next(iter(drive.in_folder("Backups").values()))["bytes"]
    assert blob.startswith(backup.MAGIC2)
    plain = tmp_path / "b.zip"
    sealed = tmp_path / "b.pbak"
    sealed.write_bytes(blob)
    backup.decrypt_file(sealed, plain, code)                         # same format: restore works as before
    assert plain.read_bytes().startswith(b"PK")


# -- the real Google Drive side (HTTP faked) -----------------------------------------------------------------------------
def google_with(handler):
    from prometheus.connectors.google import Google

    g = Google(http=httpx.Client(transport=httpx.MockTransport(handler)))
    g._access, g._until = "token", 10 ** 12
    return g


def test_no_connection_is_offline_not_a_crash():
    def down(request):
        raise httpx.ConnectError("no internet")

    drive = cs.GoogleDrive(google_with(down))
    with pytest.raises(cs.Offline):
        drive.ensure_folder("Prometheus Cloudroom")


def test_the_real_drive_upload_sends_the_sealed_bytes_and_the_sha(tmp_path):
    seen = {"body": b"", "start": None}

    def handler(request):
        path = request.url.path
        if request.method == "GET" and path == "/drive/v3/files":
            return httpx.Response(200, json={"files": []})
        if request.method == "POST" and path == "/drive/v3/files":
            return httpx.Response(200, json={"id": "folder1"})
        if path == "/upload/drive/v3/files":
            seen["start"] = json.loads(request.content)
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/session/1"})
        if path == "/upload/session/1":
            seen["body"] += request.content
            return httpx.Response(200, json={"id": "file1"})
        return httpx.Response(404)

    drive = cs.GoogleDrive(google_with(handler))
    folder = drive.ensure_folder("Prometheus Cloudroom")
    sealed = tmp_path / "x.blob"
    sealed.write_bytes(backup.encrypt_bytes(SECRET, SALT, KEY))
    assert drive.put("repo-knowledge.pcr", sealed, folder, "abc123") == "file1"
    assert seen["body"].startswith(backup.MAGIC2) and SECRET not in seen["body"]
    assert seen["start"]["appProperties"] == {"sha": "abc123"} and seen["start"]["parents"] == ["folder1"]
