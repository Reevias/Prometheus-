import pytest

from prometheus.memory.store import Store
from prometheus.safety.approvals import Approvals
from prometheus.safety.budget import Budget, BudgetExceeded
from prometheus.safety.killswitch import KillSwitch, Stopped
from prometheus.safety.permissions import Decision, Permissions


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "m.db")


def test_permission_matrix():
    perms = Permissions({"file_sorter": {"read_files", "move_files"}, "repo_scout": {"clone_repo", "mystery"}})
    assert perms.check("file_sorter", "move_files") is Decision.ALLOW
    assert perms.check("file_sorter", "web_fetch") is Decision.DENY
    assert perms.check("repo_scout", "clone_repo") is Decision.ASK
    assert perms.check("repo_scout", "mystery") is Decision.ASK
    assert perms.check("unknown_agent", "read_files") is Decision.DENY


def test_approval_runs_handler_only_when_approved(store):
    approvals = Approvals(store)
    ran = []
    approvals.register("clone_repo", lambda payload: ran.append(payload["url"]) or "cloned")
    a = approvals.request("clone_repo", "Clone a", {"url": "a"})
    b = approvals.request("clone_repo", "Clone b", {"url": "b"})
    assert approvals.resolve(a, approved=False) == "rejected"
    assert approvals.resolve(b, approved=True) == "cloned"
    assert ran == ["b"]
    assert store.approval(a).status == "rejected"
    assert store.approval(b).status == "approved"
    assert store.audit_log()[0]["action"] == "approval"


def test_approval_handler_error_marks_failed(store):
    approvals = Approvals(store)

    def bad(payload):
        raise RuntimeError("network down")

    approvals.register("clone_repo", bad)
    a = approvals.request("clone_repo", "Clone", {"url": "x"})
    assert "network down" in approvals.resolve(a, approved=True)
    assert store.approval(a).status == "failed"


def test_resolving_twice_does_nothing(store):
    approvals = Approvals(store)
    ran = []
    approvals.register("k", lambda p: ran.append(1) or "ok")
    a = approvals.request("k", "s", {})
    approvals.resolve(a, approved=True)
    approvals.resolve(a, approved=True)
    assert ran == [1]


def test_budget_warns_at_80_and_stops_at_100(store):
    budget = Budget(store, cap_usd=1.0)
    budget.record("anthropic", "m", 0.79, 1, 1)
    assert not budget.state().warn
    budget.ensure_can_spend()
    budget.record("anthropic", "m", 0.01, 1, 1)
    assert budget.state().warn and not budget.state().stopped
    budget.record("anthropic", "m", 0.2, 1, 1)
    assert budget.state().stopped
    with pytest.raises(BudgetExceeded):
        budget.ensure_can_spend()


def test_killswitch_stops_and_resets():
    ks = KillSwitch()
    ks.check()
    ks.trigger("user pressed stop")
    assert ks.is_set
    with pytest.raises(Stopped, match="user pressed stop"):
        ks.check()
    ks.reset()
    ks.check()
    assert not ks.is_set
