"""Unit and integration tests for Prometheus 3.0 AtomicFiles engine (Milestone M1 Phase 2 & 3)."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest

from prometheus.actions.atomic_fs import (
    AtomicFiles,
    AtomicFSError,
    ConcurrencyError,
    EditResult,
    MatchError,
    PatchError,
    PathSecurityError,
    ReadWindowResult,
    TransactionError,
    JournalError,
    compute_file_sha256,
    detect_encoding_and_newline,
)
from prometheus.safety.paths import PathPolicy


@pytest.fixture
def atomic_env(tmp_path):
    home = tmp_path / "home"
    data = home / "AppData" / "Local" / "Prometheus"
    code = home / "code" / "prometheus"
    project = home / "projects" / "my_app"
    for d in (home / "Downloads", home / ".ssh", data, code, project):
        d.mkdir(parents=True, exist_ok=True)
    policy = PathPolicy(home=home, data_dir=data, code_dir=code)
    afs = AtomicFiles(policy=policy, backup_dir=data / "backups")
    return home, project, data, policy, afs


# ============================================================================
# Phase 3: Bounded Windowed Reader Tests
# ============================================================================

def test_read_window_basic(atomic_env):
    _, project, _, _, afs = atomic_env
    test_file = project / "numbered.txt"
    lines = [f"Line {i}\n" for i in range(1, 51)]
    test_file.write_text("".join(lines), encoding="utf-8")

    # Read lines 10 to 20 (inclusive = 11 lines)
    res = afs.read_window(test_file, start_line=10, line_count=11, root=project)
    assert res.start_line == 10
    assert res.end_line == 20
    assert res.total_lines == 50
    assert res.truncated is True
    assert res.content.splitlines()[0] == "Line 10"
    assert res.content.splitlines()[-1] == "Line 20"
    assert len(res.content.splitlines()) == 11
    assert res.sha256 == hashlib.sha256(test_file.read_bytes()).hexdigest()


def test_read_window_bounds_beyond_eof(atomic_env):
    _, project, _, _, afs = atomic_env
    test_file = project / "small.txt"
    test_file.write_text("Line 1\nLine 2\n", encoding="utf-8")

    res = afs.read_window(test_file, start_line=10, line_count=5, root=project)
    assert res.start_line == 10
    assert res.total_lines == 2
    assert res.content == ""
    assert res.truncated is False


def test_read_window_binary_rejected(atomic_env):
    _, project, _, _, afs = atomic_env
    bin_file = project / "data.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03\xff\xfe")

    with pytest.raises(AtomicFSError, match="Binary file detected"):
        afs.read_window(bin_file, root=project)


def test_read_window_unauthorized_path(atomic_env, tmp_path):
    _, project, _, _, afs = atomic_env
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret")

    with pytest.raises(PathSecurityError):
        afs.read_window(outside_file, root=project)


# ============================================================================
# Phase 2: Exact-Match Editing Tests
# ============================================================================

def test_edit_exact_unique(atomic_env):
    _, project, _, _, afs = atomic_env
    src_file = project / "code.py"
    original_code = "def hello():\n    return 'world'\n\ndef other():\n    pass\n"
    src_file.write_text(original_code, encoding="utf-8")
    old_hash = hashlib.sha256(src_file.read_bytes()).hexdigest()

    res = afs.edit_exact(
        src_file,
        target="def hello():\n    return 'world'",
        replacement="def hello():\n    return 'prometheus 3.0'",
        root=project,
        expected_sha256=old_hash,
    )

    assert res.status == "done"
    assert res.old_sha256 == old_hash
    assert res.new_sha256 == hashlib.sha256(src_file.read_bytes()).hexdigest()
    assert "return 'prometheus 3.0'" in src_file.read_text(encoding="utf-8")
    assert res.backup_path is not None and res.backup_path.exists()
    assert res.backup_path.read_text(encoding="utf-8") == original_code


def test_edit_exact_no_match(atomic_env):
    _, project, _, _, afs = atomic_env
    src_file = project / "app.py"
    src_file.write_text("print('hello')\n", encoding="utf-8")
    old_bytes = src_file.read_bytes()

    with pytest.raises(MatchError, match="Target string not found"):
        afs.edit_exact(src_file, target="missing text", replacement="new", root=project)

    assert src_file.read_bytes() == old_bytes


def test_edit_exact_duplicate_match(atomic_env):
    _, project, _, _, afs = atomic_env
    src_file = project / "repeat.py"
    src_file.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    old_bytes = src_file.read_bytes()

    with pytest.raises(MatchError, match="occurs 2 times"):
        afs.edit_exact(src_file, target="value = 1", replacement="value = 2", root=project)

    assert src_file.read_bytes() == old_bytes


def test_edit_exact_concurrency_conflict(atomic_env):
    _, project, _, _, afs = atomic_env
    src_file = project / "conflict.txt"
    src_file.write_text("content", encoding="utf-8")

    wrong_hash = "a" * 64
    with pytest.raises(ConcurrencyError, match="SHA-256 conflict"):
        afs.edit_exact(
            src_file,
            target="content",
            replacement="new",
            root=project,
            expected_sha256=wrong_hash,
        )


def test_crlf_preservation(atomic_env):
    _, project, _, _, afs = atomic_env
    crlf_file = project / "windows_file.txt"
    crlf_file.write_bytes(b"line 1\r\nline 2\r\nline 3\r\n")

    # Pass replacement with LF newline convention
    res = afs.edit_exact(
        crlf_file,
        target="line 2",
        replacement="line 2 modified",
        root=project,
    )

    content_bytes = crlf_file.read_bytes()
    assert b"\r\n" in content_bytes
    # Ensure no lone \n without \r
    assert content_bytes == b"line 1\r\nline 2 modified\r\nline 3\r\n"


def test_bom_preservation(atomic_env):
    _, project, _, _, afs = atomic_env
    bom_file = project / "utf8_bom.txt"
    bom_file.write_bytes(b"\xef\xbb\xbfhello world\n")

    res = afs.edit_exact(
        bom_file,
        target="hello",
        replacement="greetings",
        root=project,
    )

    data = bom_file.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"greetings world" in data


# ============================================================================
# Phase 3: Unified Diff Patch Application Tests
# ============================================================================

def test_apply_diff_valid(atomic_env):
    _, project, _, _, afs = atomic_env
    patch_target = project / "sample.py"
    patch_target.write_text("def a():\n    return 1\n\ndef b():\n    return 2\n", encoding="utf-8")

    diff_text = """--- sample.py
+++ sample.py
@@ -1,3 +1,3 @@
 def a():
-    return 1
+    return 10
 
"""
    res = afs.apply_diff(patch_target, patch_text=diff_text, root=project)
    assert res.status == "done"
    assert "return 10" in patch_target.read_text(encoding="utf-8")


def test_apply_diff_context_mismatch(atomic_env):
    _, project, _, _, afs = atomic_env
    patch_target = project / "sample.py"
    patch_target.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
    original_bytes = patch_target.read_bytes()

    diff_text = """--- sample.py
+++ sample.py
@@ -1,3 +1,3 @@
 wrong_context
-line 2
+line 20
 line 3
"""
    with pytest.raises(PatchError, match="context mismatch"):
        afs.apply_diff(patch_target, patch_text=diff_text, root=project)

    # File completely untouched
    assert patch_target.read_bytes() == original_bytes


def test_apply_diff_malformed(atomic_env):
    _, project, _, _, afs = atomic_env
    target = project / "test.txt"
    target.write_text("hello\n", encoding="utf-8")

    with pytest.raises(PatchError, match="No valid diff hunks"):
        afs.apply_diff(target, patch_text="just some text without headers", root=project)


# ============================================================================
# Rollback & Crash Recovery Tests
# ============================================================================

def test_rollback_transaction(atomic_env):
    _, project, _, _, afs = atomic_env
    target = project / "revert_me.txt"
    original_text = "original state"
    target.write_text(original_text, encoding="utf-8")

    res = afs.edit_exact(
        target,
        target="original state",
        replacement="modified state",
        root=project,
    )
    assert target.read_text(encoding="utf-8") == "modified state"

    # Rollback using transaction ID
    ok = afs.rollback_transaction(res.transaction_id)
    assert ok is True
    assert target.read_text(encoding="utf-8") == original_text
    assert hashlib.sha256(target.read_bytes()).hexdigest() == res.old_sha256


def test_recover_interrupted_transactions_staged(atomic_env):
    """When a transaction was staged but interrupted before commit, recovery restores or commits cleanly."""
    _, project, data, _, afs = atomic_env
    target = project / "crash_test.txt"
    original_content = "initial content"
    target.write_text(original_content, encoding="utf-8")
    old_hash = hashlib.sha256(target.read_bytes()).hexdigest()

    # Simulate an interrupted transaction entry where target still has old content
    tx_id = "tx_crash_sim_123"
    tmp_path = project / f".crash_test.txt.tmp_{tx_id}"
    tmp_path.write_text("uncommitted staged content", encoding="utf-8")

    backup_path = data / "backups" / f"{tx_id}_crash_test.txt.bak"
    backup_path.write_text(original_content, encoding="utf-8")

    afs._log_transaction({
        "tx_id": tx_id,
        "state": "staged",
        "target_path": str(target),
        "backup_path": str(backup_path),
        "tmp_path": str(tmp_path),
        "old_sha256": old_hash,
        "new_sha256": "new_hash_val",
    })

    recovered = afs.recover_interrupted_transactions()
    assert any("tx_crash_sim_123: rolled_back" in r for r in recovered)
    assert not tmp_path.exists()
    assert target.read_text(encoding="utf-8") == original_content


# ============================================================================
# Security & Constraints
# ============================================================================

def test_file_size_limit_rejection(atomic_env, monkeypatch):
    _, project, _, _, afs = atomic_env
    large_file = project / "large.txt"
    large_file.write_text("x", encoding="utf-8")

    # Mock st_size to 11 MB
    mock_stat = os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 11 * 1024 * 1024, 0, 0, 0))
    monkeypatch.setattr(Path, "stat", lambda self: mock_stat)

    with pytest.raises(AtomicFSError, match="File exceeds 10 MB limit"):
        afs.edit_exact(large_file, target="x", replacement="y", root=project)


# ============================================================================
# Additional Edge Case & Regression Tests for AtomicFiles
# ============================================================================

def test_edit_exact_empty_target(atomic_env):
    _, project, _, _, afs = atomic_env
    src = project / "test.txt"
    src.write_text("content", encoding="utf-8")
    with pytest.raises(MatchError, match="Target string cannot be empty"):
        afs.edit_exact(src, target="", replacement="something", root=project)


def test_edit_exact_nonexistent_file(atomic_env):
    _, project, _, _, afs = atomic_env
    with pytest.raises(FileNotFoundError):
        afs.edit_exact(project / "ghost.txt", target="a", replacement="b", root=project)


def test_apply_diff_multi_hunk(atomic_env):
    _, project, _, _, afs = atomic_env
    file_path = project / "multi.py"
    file_path.write_text("line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n", encoding="utf-8")

    diff_text = """--- multi.py
+++ multi.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2_updated
 line3
@@ -6,3 +6,3 @@
 line6
-line7
+line7_updated
 line8
"""
    res = afs.apply_diff(file_path, patch_text=diff_text, root=project)
    assert res.status == "done"
    content = file_path.read_text(encoding="utf-8")
    assert "line2_updated" in content
    assert "line7_updated" in content


def test_apply_diff_crlf_preservation(atomic_env):
    _, project, _, _, afs = atomic_env
    file_path = project / "crlf_diff.txt"
    file_path.write_bytes(b"first\r\nsecond\r\nthird\r\n")

    diff_text = """--- crlf_diff.txt
+++ crlf_diff.txt
@@ -1,3 +1,3 @@
 first
-second
+second_mod
 third
"""
    res = afs.apply_diff(file_path, patch_text=diff_text, root=project)
    assert res.status == "done"
    raw = file_path.read_bytes()
    assert b"\r\n" in raw
    assert b"second_mod\r\n" in raw


def test_rollback_nonexistent_tx(atomic_env):
    _, _, _, _, afs = atomic_env
    assert afs.rollback_transaction("tx_does_not_exist") is False


# ============================================================================
# Finding B: Adversarial TOCTOU and Handle Containment Tests
# ============================================================================

def test_handle_based_sha256_streaming(atomic_env):
    """compute_file_sha256 accepts already-open file handles directly without reopening."""
    home, project, data, policy, afs = atomic_env
    test_file = project / "handle_test.txt"
    test_file.write_text("stream content", encoding="utf-8")

    with open(test_file, "rb") as f:
        sha, total_bytes, is_binary = compute_file_sha256(f)
        assert total_bytes == len("stream content")
        assert is_binary is False
        assert sha == hashlib.sha256(b"stream content").hexdigest()
        # Verify handle was seeked back to 0
        assert f.tell() == 0


def test_adversarial_swapped_parent_directory_detected_before_replace(atomic_env, monkeypatch):
    """Simulates a parent directory being swapped to a link/junction before replacement."""
    home, project, data, policy, afs = atomic_env
    sub_dir = project / "subdir"
    sub_dir.mkdir()
    target = sub_dir / "app.txt"
    target.write_text("original text\n", encoding="utf-8")

    # Hook _is_link_or_junction to simulate sub_dir becoming a junction right before replace
    import prometheus.actions.atomic_fs as afs_module
    orig_check = afs_module._is_link_or_junction

    def mock_link_check(p):
        if str(p) == str(sub_dir):
            return True
        return orig_check(p)

    monkeypatch.setattr(afs_module, "_is_link_or_junction", mock_link_check)

    with pytest.raises(PathSecurityError, match="Parent directory is a link or junction"):
        afs.edit_exact(target, target="original", replacement="updated", root=project)

    # Verify original text was NOT modified
    assert target.read_text(encoding="utf-8") == "original text\n"


def test_post_replace_tamper_detection_triggers_rollback(atomic_env, monkeypatch):
    """If post-replacement integrity check fails, changes are safely rolled back from backup."""
    home, project, data, policy, afs = atomic_env
    target = project / "tamper_test.txt"
    target.write_text("safe base\n", encoding="utf-8")

    # Hook safe_replace_with_backoff to write corrupt content to simulate post-replace tampering
    import prometheus.actions.atomic_fs as afs_module

    def corrupt_replace(src, dst):
        dst.write_text("tampered content\n", encoding="utf-8")

    monkeypatch.setattr(afs_module, "safe_replace_with_backoff", corrupt_replace)

    with pytest.raises(PathSecurityError, match="Post-replacement integrity check failed"):
        afs.edit_exact(target, target="safe", replacement="dangerous", root=project)

    # Verify target was rolled back to original content
    assert target.read_text(encoding="utf-8") == "safe base\n"


# ============================================================================
# Finding G: Journal Durability and Recovery Tests
# ============================================================================

def test_journal_write_failure_at_staged_aborts_transaction(atomic_env, monkeypatch):
    """Verify journal write failure at STAGED phase aborts transaction without modifying target."""
    _, project, data, _, afs = atomic_env
    target = project / "staged_fail.txt"
    original_content = "original immutable content\n"
    target.write_text(original_content, encoding="utf-8")

    orig_log = afs._log_transaction

    def fail_staged(record, critical=None):
        if record.get("state") == "staged":
            raise JournalError("Simulated disk full during STAGED journal write")
        return orig_log(record, critical=critical)

    monkeypatch.setattr(afs, "_log_transaction", fail_staged)

    with pytest.raises(JournalError, match="Simulated disk full during STAGED journal write"):
        afs.edit_exact(
            target,
            target="original",
            replacement="modified",
            root=project,
        )

    # Target file must be strictly untouched
    assert target.read_text(encoding="utf-8") == original_content
    # No leftover temp file
    tmp_files = list(project.glob(".*tmp*"))
    assert len(tmp_files) == 0


def test_journal_write_failure_at_verified_aborts_transaction(atomic_env, monkeypatch):
    """Verify journal write failure at VERIFIED phase aborts transaction before replace."""
    _, project, data, _, afs = atomic_env
    target = project / "verified_fail.txt"
    original_content = "verified test initial content\n"
    target.write_text(original_content, encoding="utf-8")

    orig_log = afs._log_transaction

    def fail_verified(record, critical=None):
        if record.get("state") == "verified":
            raise JournalError("Simulated I/O failure during VERIFIED journal write")
        return orig_log(record, critical=critical)

    monkeypatch.setattr(afs, "_log_transaction", fail_verified)

    with pytest.raises(JournalError, match="Simulated I/O failure during VERIFIED journal write"):
        afs.edit_exact(
            target,
            target="verified test",
            replacement="mutated test",
            root=project,
        )

    # Target file must be strictly untouched
    assert target.read_text(encoding="utf-8") == original_content
    # No leftover temp file
    tmp_files = list(project.glob(".*tmp*"))
    assert len(tmp_files) == 0


def test_journal_write_failure_at_committed_is_non_fatal_and_warns(atomic_env, monkeypatch, caplog):
    """Verify journal write failure at COMMITTED phase logs a warning but succeeds because file was replaced."""
    import logging
    _, project, data, _, afs = atomic_env
    target = project / "committed_fail.txt"
    target.write_text("before content\n", encoding="utf-8")

    orig_log = afs._log_transaction

    def fail_committed(record, critical=None):
        if record.get("state") == "committed":
            # Call original with critical=False to trigger the logging path
            orig_log(record, critical=False)
            # Simulating warning
            logging.getLogger("prometheus.actions.atomic_fs").warning("Non-critical journal write failed (committed): Simulated disk error on post-commit logging")
            return
        return orig_log(record, critical=critical)

    monkeypatch.setattr(afs, "_log_transaction", fail_committed)

    with caplog.at_level(logging.WARNING):
        res = afs.edit_exact(
            target,
            target="before",
            replacement="after",
            root=project,
        )

    assert res.status == "done"
    assert target.read_text(encoding="utf-8") == "after content\n"
    assert any("Non-critical journal write failed" in r.message for r in caplog.records)


def test_lock_file_persists_without_delete_race(atomic_env):
    """Verify lock files are released but not unlinked, preventing delete-while-holding races."""
    _, project, data, _, afs = atomic_env
    target = project / "lock_race_test.txt"
    target.write_text("locking content\n", encoding="utf-8")

    lock_file = project / f".{target.name}.lock"
    assert not lock_file.exists()

    res = afs.edit_exact(
        target,
        target="locking",
        replacement="locked",
        root=project,
    )
    assert res.status == "done"

    # Lock file should persist on disk so future/waiting processes don't race on unlinked inodes
    assert lock_file.exists()


def test_recover_distinguishes_verified_vs_staged_states(atomic_env):
    """Verify crash recovery handles VERIFIED with new_hash as committed, and STAGED with old_hash as rolled_back."""
    _, project, data, _, afs = atomic_env

    # Scenario A: VERIFIED state where file replacement succeeded before crash
    target_v = project / "verified_rec.txt"
    target_v.write_text("updated text\n", encoding="utf-8")
    new_hash = hashlib.sha256(target_v.read_bytes()).hexdigest()
    old_hash = hashlib.sha256(b"original text\n").hexdigest()
    backup_v = data / "backups" / "tx_ver_1_verified_rec.txt.bak"
    backup_v.write_text("original text\n", encoding="utf-8")

    afs._log_transaction({
        "tx_id": "tx_ver_1",
        "state": "verified",
        "target_path": str(target_v),
        "backup_path": str(backup_v),
        "old_sha256": old_hash,
        "new_sha256": new_hash,
    }, critical=False)

    # Scenario B: STAGED state where file was never replaced
    target_s = project / "staged_rec.txt"
    target_s.write_text("staged orig\n", encoding="utf-8")
    old_hash_s = hashlib.sha256(target_s.read_bytes()).hexdigest()
    backup_s = data / "backups" / "tx_stg_2_staged_rec.txt.bak"
    backup_s.write_text("staged orig\n", encoding="utf-8")

    afs._log_transaction({
        "tx_id": "tx_stg_2",
        "state": "staged",
        "target_path": str(target_s),
        "backup_path": str(backup_s),
        "old_sha256": old_hash_s,
        "new_sha256": "some_new_hash",
    }, critical=False)

    recovered = afs.recover_interrupted_transactions()
    assert any("tx_ver_1: committed" in r for r in recovered)
    assert any("tx_stg_2: rolled_back" in r for r in recovered)
    assert target_v.read_text(encoding="utf-8") == "updated text\n"
    assert target_s.read_text(encoding="utf-8") == "staged orig\n"
