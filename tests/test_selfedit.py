import shutil

import pytest

from prometheus.memory.store import Store
from prometheus.safety.approvals import Approvals
from prometheus.selfedit.patcher import Patcher
from prometheus.selfedit.prompts import PromptStore

TOOL = "def greet():\n    # --- TOOLS ANCHOR ---\n    return 'hi'\n"


@pytest.fixture
def env(tmp_path):
    code = tmp_path / "code"
    (code / "plugins").mkdir(parents=True)
    (code / "safety").mkdir()
    (code / "plugins" / "greet.py").write_text(TOOL)
    (code / "safety" / "paths.py").write_text("X = 1\n")
    (code / "server.py").write_text("Y = 1\n")
    approvals = Approvals(Store(tmp_path / "m.db"))
    patcher = Patcher(code, approvals, tmp_path / "backups")
    approvals.register("apply_patch", patcher.apply)
    return code, approvals, patcher


@pytest.mark.parametrize("rel", ["safety/paths.py", "server.py", "selfedit/patcher.py", "../outside.py"])
def test_locked_file_refused(env, rel):
    _, _, patcher = env
    assert patcher.propose_insert(rel, "X = 1", "Z = 2", "why").status == "refused"


def test_duplicate_skipped(env):
    code, _, patcher = env
    first = patcher.propose_insert("plugins/greet.py", "# --- TOOLS ANCHOR ---", "print('x')", "add print")
    assert first.status == "queued"
    (code / "plugins" / "greet.py").write_text(TOOL.replace("# --- TOOLS ANCHOR ---",
                                                          "# --- TOOLS ANCHOR ---\n    print('x')"))
    again = patcher.propose_insert("plugins/greet.py", "# --- TOOLS ANCHOR ---", "print('x')", "add print")
    assert again.status == "skipped"


def test_anchor_twice_refused(env):
    code, _, patcher = env
    (code / "plugins" / "greet.py").write_text(TOOL + "# --- TOOLS ANCHOR ---\n")
    assert patcher.propose_insert("plugins/greet.py", "# --- TOOLS ANCHOR ---", "x = 1", "r").status == "refused"
    assert patcher.propose_insert("plugins/greet.py", "# --- MISSING ---", "x = 1", "r").status == "refused"


def test_syntax_error_refused(env):
    _, _, patcher = env
    res = patcher.propose_insert("plugins/greet.py", "# --- TOOLS ANCHOR ---", "def (:", "r")
    assert res.status == "refused" and "syntax" in res.detail.lower()


def test_queued_then_applied_with_backup(env, tmp_path):
    code, approvals, patcher = env
    res = patcher.propose_insert("plugins/greet.py", "# --- TOOLS ANCHOR ---", "name = 'world'", "r")
    assert "+    name = 'world'" in res.diff
    assert (code / "plugins" / "greet.py").read_text() == TOOL  # nothing changes before approval
    approvals.resolve(res.approval_id, approved=True)
    assert "    name = 'world'" in (code / "plugins" / "greet.py").read_text()
    assert list((tmp_path / "backups").iterdir())


def test_file_changed_after_proposal_not_applied(env):
    code, approvals, patcher = env
    res = patcher.propose_insert("plugins/greet.py", "# --- TOOLS ANCHOR ---", "a = 1", "r")
    (code / "plugins" / "greet.py").write_text(TOOL + "\n# edited by the user\n")
    assert "changed" in approvals.resolve(res.approval_id, approved=True).lower()
    assert "a = 1" not in (code / "plugins" / "greet.py").read_text()


def test_new_tool_name_rules(env):
    code, approvals, patcher = env
    assert patcher.propose_new_tool("../evil", "x = 1", "r").status == "refused"
    assert patcher.propose_new_tool("Bad-Name", "x = 1", "r").status == "refused"
    assert patcher.propose_new_tool("weather", "def (", "r").status == "refused"
    ok = patcher.propose_new_tool("weather", "def get():\n    return 1\n", "r")
    assert ok.status == "queued"
    approvals.resolve(ok.approval_id, approved=True)
    assert (code / "plugins" / "weather.py").exists()
    assert patcher.propose_new_tool("weather", "x = 2\n", "r").status == "refused"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_prompt_store_commit_and_rollback(tmp_path):
    prompts = PromptStore(tmp_path / "prompts")
    original = prompts.get("planner")
    assert "Prometheus" in original
    prompts.set("planner", "New planner text", "try shorter prompt")
    assert prompts.get("planner") == "New planner text"
    history = prompts.history("planner")
    assert len(history) == 2 and history[0][1] == "try shorter prompt"
    prompts.rollback("planner", history[1][0])
    assert prompts.get("planner") == original
    with pytest.raises(ValueError):
        prompts.get("../../etc/passwd")
