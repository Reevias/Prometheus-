"""v1.3: Prometheus can read its own GitHub repo, and post or update itself only after approval."""
import base64
import json
import subprocess

import httpx
import pytest

from conftest import login

from prometheus.agents.github import GitHubAgent, GitHubError, github_action_handler, update_self
from prometheus.config import Settings, set_secret
from prometheus.safety.permissions import Decision

REPO = "Reevias/Prometheus-"


class Api:
    """A fake api.github.com that records every request."""

    def __init__(self):
        self.requests = []

    def handler(self, request):
        self.requests.append((request.method, request.url.host, request.url.path, request.headers.get("authorization"),
                              json.loads(request.content) if request.content else None))
        path = request.url.path
        if path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "abcdef1234567", "commit": {"message": "feat: x\n\nbody",
                                                                               "author": {"name": "Reevias", "date": "2026-11-10T08:00:00Z"}}}])
        if path.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": [{"name": "Tests", "conclusion": "success",
                                                                "status": "completed", "head_branch": "main"}]})
        if path.endswith("/issues") and request.method == "GET":
            return httpx.Response(200, json=[{"number": 3, "title": "Bug", "user": {"login": "a"}, "pull_request": None},
                                             {"number": 4, "title": "A PR", "user": {"login": "b"}, "pull_request": {}}])
        if path.endswith("/issues") and request.method == "POST":
            return httpx.Response(201, json={"number": 9, "html_url": f"https://github.com/{REPO}/issues/9"})
        if "/comments" in path:
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/issues/3#c1"})
        if "/contents/" in path and request.method == "GET":
            return httpx.Response(200, json={"content": base64.b64encode(b"hello file").decode(), "encoding": "base64",
                                             "sha": "filesha", "type": "file"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "mainsha"}})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if "/contents/" in path and request.method == "PUT":
            return httpx.Response(201, json={"commit": {"sha": "newsha"}})
        if path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"number": 12, "html_url": f"https://github.com/{REPO}/pull/12"})
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[{"number": 4, "title": "A PR", "user": {"login": "b"}}])
        return httpx.Response(404, json={"message": "Not Found"})


def agent(api=None, repo=REPO):
    api = api or Api()
    return GitHubAgent(http=httpx.Client(transport=httpx.MockTransport(api.handler)), repo=repo), api


@pytest.fixture(autouse=True)
def token(tmp_home, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    set_secret("GITHUB_TOKEN", "github_pat_TESTTOKEN123")


def test_permissions(make_ctx):
    ctx = make_ctx()
    assert ctx.permissions.check("github", "github_read") is Decision.ALLOW
    assert ctx.permissions.check("github", "github_write") is Decision.ASK
    assert GitHubAgent.accepts_private is False


def test_status_reads_commits_ci_issues_and_prs(make_ctx):
    ag, api = agent()
    result = ag.run("status", {"action": "status"}, make_ctx())
    assert result.status == "done"
    for needle in ("feat: x", "success", "Bug", "A PR"):
        assert needle in result.summary
    assert all(host == "api.github.com" for _, host, *_ in api.requests)
    assert all(auth == "Bearer github_pat_TESTTOKEN123" for *_, auth, _ in api.requests)
    assert all(method == "GET" for method, *_ in api.requests)
    assert "github_pat_TESTTOKEN123" not in result.summary


def test_read_file_returns_text_and_refuses_odd_paths(make_ctx):
    ag, _ = agent()
    ctx = make_ctx()
    assert "hello file" in ag.run("r", {"action": "read_file", "path": "docs/brief.md"}, ctx).summary
    for bad in ("../etc/passwd", "/abs", "a//b", "x" * 300, ".git/config"):
        assert ag.run("r", {"action": "read_file", "path": bad}, ctx).status == "failed"


@pytest.mark.parametrize("repo", ["", "a", "a/b/c", "a b/c", "../x", "a/b;c"])
def test_repo_name_is_validated(repo, make_ctx):
    ag, api = agent(repo=repo)
    assert ag.run("s", {"action": "status"}, make_ctx()).status == "failed" and api.requests == []


def test_no_token_gives_clear_message(make_ctx, tmp_home, monkeypatch):
    from prometheus.config import delete_secret
    delete_secret("GITHUB_TOKEN")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ag, api = agent()
    result = ag.run("s", {"action": "status"}, make_ctx())
    assert result.status == "failed" and "Settings" in result.summary and api.requests == []


def test_writes_wait_for_approval_and_post_nothing(make_ctx):
    ag, api = agent()
    ctx = make_ctx()
    for inputs in ({"action": "create_issue", "title": "Crash on start", "body": "Steps..."},
                   {"action": "comment", "number": "3", "body": "Fixed in main"},
                   {"action": "propose_change", "path": "docs/notes.md", "content": "new text", "title": "Add notes",
                    "body": "why"},
                   {"action": "update_self"}):
        assert ag.run("w", inputs, ctx).status == "waiting_approval"
    assert api.requests == []
    kinds = [(a.kind, a.payload["action"]) for a in ctx.store.approvals()]
    assert kinds == [("github_action", "create_issue"), ("github_action", "comment"),
                     ("github_action", "propose_change"), ("github_action", "update_self")]


def test_approved_issue_and_comment_are_posted():
    ag, api = agent()
    out = github_action_handler({"action": "create_issue", "title": "Crash on start", "body": "Steps"}, ag)
    assert "issues/9" in out
    method, host, path, auth, body = api.requests[-1]
    assert (method, host, path) == ("POST", "api.github.com", f"/repos/{REPO}/issues") and body["title"] == "Crash on start"
    assert "issues/3" in github_action_handler({"action": "comment", "number": "3", "body": "ok"}, ag)
    with pytest.raises(RuntimeError):
        github_action_handler({"action": "comment", "number": "x", "body": "ok"}, ag)
    with pytest.raises(RuntimeError):
        github_action_handler({"action": "create_issue", "title": "", "body": ""}, ag)


def test_propose_change_makes_a_branch_commit_and_pr_never_touching_main():
    ag, api = agent()
    out = github_action_handler({"action": "propose_change", "path": "docs/notes.md", "content": "new text",
                                 "title": "Add notes", "body": "why"}, ag)
    assert "pull/12" in out
    calls = [(m, p) for m, _, p, _, _ in api.requests]
    assert ("POST", f"/repos/{REPO}/git/refs") in calls and ("POST", f"/repos/{REPO}/pulls") in calls
    puts = [(p, b) for m, _, p, _, b in api.requests if m == "PUT"]
    assert len(puts) == 1 and puts[0][1]["branch"].startswith("prometheus/") and puts[0][1]["branch"] != "main"
    assert base64.b64decode(puts[0][1]["content"]) == b"new text"
    ref = next(b for m, _, p, _, b in api.requests if p.endswith("/git/refs"))
    assert ref["ref"].startswith("refs/heads/prometheus/") and ref["sha"] == "mainsha"
    pr = next(b for m, _, p, _, b in api.requests if p.endswith("/pulls") and m == "POST")
    assert pr["base"] == "main" and pr["head"].startswith("prometheus/")
    with pytest.raises(RuntimeError):
        github_action_handler({"action": "propose_change", "path": ".github/workflows/tests.yml", "content": "x",
                               "title": "t", "body": ""}, ag)


def test_update_self_only_fast_forwards_in_a_git_checkout(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "Updating abc..def\nFast-forward\n 3 files changed", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GitHubError, match="not a Git"):
        update_self(tmp_path)
    (tmp_path / ".git").mkdir()
    msg = update_self(tmp_path)
    assert ["git", "-C", str(tmp_path), "pull", "--ff-only", "origin", "main"] in calls
    assert calls[-1][1:5] == ["-m", "pip", "install", "--quiet"]      # new packages get installed too
    assert "restart" in msg.lower()


def test_settings_page_has_github_connection(app_client):
    client, services = app_client()
    login(client)
    page = client.get("/settings").text
    assert "GitHub" in page and REPO in page and "github.com/settings/personal-access-tokens" in page
    assert "github_pat_TESTTOKEN123" not in page
    assert Settings().github_repo == REPO
