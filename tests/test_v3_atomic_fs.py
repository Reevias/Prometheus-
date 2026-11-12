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
