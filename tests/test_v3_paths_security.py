"""Tests for Prometheus 3.0 M1 Phase 1 path security hardening.

Verifies protection against:
1. CRITICAL-1: UNC paths and NTLM coercion (\\\\server, //server, \\\\?\\UNC\\).
2. CRITICAL-2: Direct access to .git repository internals.
3. HIGH-1: NTFS junctions, directory symlinks, and reparse-point escapes.
4. TOCTOU: Handle-based canonical path validation.
5. Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) and ADS.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from prometheus.safety.paths import (
    PathBlocked,
    PathPolicy,
    extra_root_problem,
    get_final_path_from_handle,
    verify_handle_containment,
)


@pytest.fixture
def policy_env(tmp_path):
    home = tmp_path / "home"
    data = home / "AppData" / "Local" / "Prometheus"
    code = home / "code" / "prometheus"
    project = home / "projects" / "my_app"
    for d in (home / "Downloads", home / ".ssh", data, code, project):
        d.mkdir(parents=True, exist_ok=True)
    policy = PathPolicy(home=home, data_dir=data, code_dir=code)
    return home, project, data, policy


# ============================================================================
# CRITICAL-1: Windows UNC Path Pre-screening & NTLM Leak Protection
# ============================================================================

@pytest.mark.parametrize("unc_path", [
    r"\\127.0.0.1\share\secret.txt",
    r"\\localhost\c$\Windows\System32",
    r"\\attacker.com\payload",
    r"//10.0.0.1/share/data",
    r"//attacker.org/smb",
    r"\\?\UNC\127.0.0.1\share\test",
    r"//?/UNC/server/share",
])
def test_unc_paths_rejected_on_read(policy_env, unc_path):
    _, _, _, policy = policy_env
    with pytest.raises(PathBlocked, match="UNC"):
        policy.check_read(unc_path)


@pytest.mark.parametrize("unc_path", [
    r"\\127.0.0.1\share\evil.py",
    r"\\localhost\c$\autoexec.bat",
    r"//server/share/exploit.txt",
    r"\\?\UNC\evil.com\drop",
])
def test_unc_paths_rejected_on_change(policy_env, unc_path):
    home, project, _, policy = policy_env
    with pytest.raises(PathBlocked, match="UNC"):
        policy.check_change(unc_path, task_root=project)


def test_unc_task_root_rejected(policy_env):
    home, project, _, policy = policy_env
    with pytest.raises(PathBlocked, match="UNC"):
        policy.check_change(project / "file.txt", task_root=r"\\127.0.0.1\share")


def test_unc_is_blocked_predicate(policy_env):
    _, _, _, policy = policy_env
    assert policy.is_blocked(r"\\attacker.com\share") is True
    assert policy.is_blocked(r"//remote/box") is True


def test_unc_extra_root_problem(policy_env):
    home, _, _, _ = policy_env
    err = extra_root_problem(r"\\192.168.1.1\share", home)
    assert "UNC" in err


# ============================================================================
# Windows Device Namespaces, Reserved Names, and Alternate Data Streams
# ============================================================================

@pytest.mark.parametrize("dev_path", [
    r"\\.\PhysicalDrive0",
    r"\\.\COM1",
    r"\\?\C:\Users\secret",
    r"//./Pipe/test",
])
def test_device_namespaces_rejected(policy_env, dev_path):
    home, project, _, policy = policy_env
    with pytest.raises(PathBlocked):
        policy.check_read(dev_path)
    with pytest.raises(PathBlocked):
        policy.check_change(dev_path, task_root=project)


@pytest.mark.parametrize("reserved_name", [
    "CON", "con", "PRN", "prn", "AUX", "aux", "NUL", "nul",
    "COM1", "com2", "COM9", "LPT1", "lpt3", "LPT9",
    "CON.txt", "aux.json", "COM1.log", "NUL.tar.gz",
])
def test_dos_reserved_device_names_rejected(policy_env, reserved_name):
    home, project, _, policy = policy_env
    target = project / reserved_name
    with pytest.raises(PathBlocked, match="reserved device name"):
        policy.check_read(target)
    with pytest.raises(PathBlocked, match="reserved device name"):
        policy.check_change(target, task_root=project)


@pytest.mark.parametrize("ads_path", [
    "file.txt:hidden",
    "test.py:stream:$DATA",
])
def test_alternate_data_streams_rejected(policy_env, ads_path):
    home, project, _, policy = policy_env
    target = project / ads_path
    with pytest.raises(PathBlocked, match="alternate data streams"):
        policy.check_read(target)
    with pytest.raises(PathBlocked, match="alternate data streams"):
        policy.check_change(target, task_root=project)


# ============================================================================
# CRITICAL-2: Direct Access to .git Repository Internals
# ============================================================================

@pytest.mark.parametrize("git_rel", [
    ".git",
    ".git/config",
    ".git/HEAD",
    ".git/hooks/pre-commit",
    ".git/objects/abc",
    ".GIT",
    ".Git/config",
    ".git/hooks/post-checkout",
])
def test_git_internals_read_blocked(policy_env, git_rel):
    home, project, _, policy = policy_env
    target = project / git_rel
    with pytest.raises(PathBlocked, match=r"\.git"):
        policy.check_read(target)
    assert policy.is_blocked(target) is True


@pytest.mark.parametrize("git_rel", [
    ".git/hooks/pre-commit",
    ".git/config",
    ".git/HEAD",
    ".GIT/hooks/pre-push",
])
def test_git_internals_change_blocked(policy_env, git_rel):
    home, project, _, policy = policy_env
    target = project / git_rel
    with pytest.raises(PathBlocked, match=r"\.git"):
        policy.check_change(target, task_root=project)


def test_git_internals_cannot_be_task_root(policy_env):
    home, project, _, policy = policy_env
    git_dir = project / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PathBlocked, match=r"\.git"):
        policy.check_change(git_dir / "hooks" / "pre-commit", task_root=git_dir)


def test_legitimate_git_files_allowed(policy_env):
    """Files like .gitignore and .gitattributes or .github workflows must remain accessible."""
    home, project, _, policy = policy_env
    gitignore = project / ".gitignore"
    gitignore.write_text("*.pyc\n")
    gitattributes = project / ".gitattributes"
    gitattributes.write_text("* text=auto\n")
    github_wf = project / ".github" / "workflows" / "ci.yml"
    github_wf.parent.mkdir(parents=True, exist_ok=True)
    github_wf.write_text("name: CI\n")

    # Read
    assert policy.check_read(gitignore) == gitignore.resolve()
    assert policy.check_read(gitattributes) == gitattributes.resolve()
    assert policy.check_read(github_wf) == github_wf.resolve()

    # Change
    assert policy.check_change(gitignore, task_root=project) == gitignore.resolve()
    assert policy.check_change(gitattributes, task_root=project) == gitattributes.resolve()
    assert policy.check_change(github_wf, task_root=project) == github_wf.resolve()


# ============================================================================
# HIGH-1: NTFS Junctions, Symlinks, and Reparse Points
# ============================================================================

@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-specific")
def test_ntfs_junction_escape_blocked(policy_env, tmp_path):
    """An NTFS junction pointing outside task root must be blocked on change."""
    home, project, _, policy = policy_env

    # Outside target folder
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir()
    secret_file = outside_dir / "confidential.txt"
    secret_file.write_text("secret payload")

    # Create junction inside project pointing outside
    junction_dir = project / "junc_escape"
    res = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction_dir), str(outside_dir)],
                         capture_output=True, text=True)
    assert res.returncode == 0, f"mklink failed: {res.stderr}"

    try:
        # 1. Attempting check_change directly on junction itself
        with pytest.raises(PathBlocked, match="Links or junctions are never moved or changed"):
            policy.check_change(junction_dir, task_root=project)

        # 2. Attempting check_change on file through the junction
        with pytest.raises(PathBlocked):
            policy.check_change(junction_dir / "confidential.txt", task_root=project)

        # 3. Attempting check_change on nested file inside junction subdirectory
        nested_dir = outside_dir / "subdir"
        nested_dir.mkdir()
        with pytest.raises(PathBlocked):
            policy.check_change(junction_dir / "subdir" / "new.txt", task_root=project)
    finally:
        # Clean up junction (rmdir removes junction without deleting target on Windows)
        subprocess.run(["cmd", "/c", "rmdir", str(junction_dir)], capture_output=True)


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-specific")
def test_ntfs_junction_as_task_root_blocked(policy_env, tmp_path):
    home, project, _, policy = policy_env
    target_dir = home / "valid_target"
    target_dir.mkdir()
    junction_dir = project / "junc_root"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction_dir), str(target_dir)],
                   capture_output=True, text=True)
    try:
        with pytest.raises(PathBlocked, match="cannot be a symlink or junction"):
            policy.check_change(junction_dir / "a.txt", task_root=junction_dir)
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(junction_dir)], capture_output=True)


# ============================================================================
# TOCTOU: Handle-Based Path Validation
# ============================================================================

def test_handle_containment_valid_file(policy_env):
    home, project, _, _ = policy_env
    valid_file = project / "script.py"
    valid_file.write_text("print(1)")

    with open(valid_file, "rb") as f:
        resolved = verify_handle_containment(f.fileno(), root=project)
        assert resolved == valid_file.resolve()


def test_handle_containment_outside_root(policy_env, tmp_path):
    home, project, _, _ = policy_env
    outside_file = tmp_path / "other" / "secret.txt"
    outside_file.parent.mkdir()
    outside_file.write_text("data")

    with open(outside_file, "rb") as f:
        with pytest.raises(PathBlocked, match="resolves outside authorized root"):
            verify_handle_containment(f.fileno(), root=project)


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-specific")
def test_handle_containment_junction_detection(policy_env, tmp_path):
    """When a file is opened through a junction, GetFinalPathNameByHandleW resolves the true target."""
    home, project, _, _ = policy_env
    outside_dir = tmp_path / "real_outside"
    outside_dir.mkdir()
    target_file = outside_dir / "target.txt"
    target_file.write_text("escaped data")

    junction_dir = project / "junction_dir"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction_dir), str(outside_dir)], capture_output=True)

    try:
        opened_via_junc = junction_dir / "target.txt"
        with open(opened_via_junc, "rb") as f:
            final = get_final_path_from_handle(f.fileno())
            assert final == target_file.resolve()

            with pytest.raises(PathBlocked, match="resolves outside authorized root"):
                verify_handle_containment(f.fileno(), root=project)
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(junction_dir)], capture_output=True)


# ============================================================================
# Parent Traversal and Containment
# ============================================================================

def test_dotdot_parent_traversal_blocked(policy_env):
    home, project, _, policy = policy_env
    outside_escape = home / ".." / "outside.txt"
    with pytest.raises(PathBlocked):
        policy.check_read(outside_escape)

    task_outside = project / ".." / "task_outside.txt"
    with pytest.raises(PathBlocked, match="Outside the folder for this task"):
        policy.check_change(task_outside, task_root=project)
