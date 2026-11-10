import pytest

from prometheus.agents.github import GitHubAgent, GitHubError
from prometheus.safety.secrets import find_secrets, secret_problem

FAKE_ANT = "sk-ant-" + "a1B2" * 10
FAKE_GH = "ghp_" + "Z9y8" * 9


@pytest.mark.parametrize("text,label", [
    (f"key={FAKE_ANT}", "Anthropic key"),
    (f"token {FAKE_GH}", "GitHub token"),
    ("AKIA" + "ABCDEFGHIJKLMNOP", "AWS access key"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private key"),
    ("AIza" + "x" * 35, "Google API key"),
])
def test_finds_common_secrets(text, label):
    assert label in find_secrets(text, saved=[])


def test_plain_text_and_short_words_pass():
    assert find_secrets("Fix the sk- prefix check in the sorter. Use ghp_ tokens.", saved=[]) == []


def test_saved_key_value_is_caught_even_without_a_pattern():
    assert find_secrets("my key is plainvalue123456", saved=["plainvalue123456"]) == ["one of your saved keys"]
    assert "plainvalue" not in secret_problem("my key is plainvalue123456", saved=["plainvalue123456"])


def test_github_write_with_secret_is_blocked(tmp_home):
    agent = GitHubAgent(repo="Reevias/Prometheus-")
    with pytest.raises(GitHubError, match="secret"):
        agent.perform_write({"action": "create_issue", "title": "bug", "body": f"my key {FAKE_ANT}"})


def test_github_agent_does_not_queue_secret(make_ctx):
    ctx = make_ctx()
    agent = GitHubAgent(repo="Reevias/Prometheus-")
    res = agent.run("post", {"action": "comment", "number": 3, "body": FAKE_GH}, ctx)
    assert res.status == "failed" and ctx.store.approvals() == []


def test_self_edit_with_secret_refused(tmp_path):
    from prometheus.memory.store import Store
    from prometheus.safety.approvals import Approvals
    from prometheus.selfedit.patcher import Patcher

    code = tmp_path / "code"
    (code / "plugins").mkdir(parents=True)
    patcher = Patcher(code, Approvals(Store(tmp_path / "m.db")), tmp_path / "b")
    res = patcher.propose_new_tool("leaky", f"KEY = '{FAKE_ANT}'\n", "r")
    assert res.status == "refused" and "secret" in res.detail.lower()
