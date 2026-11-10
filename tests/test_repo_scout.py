import subprocess

import httpx
import pytest

from prometheus.agents.repo_scout import RepoScout, clone_handler

ITEMS = {"items": [
    {"full_name": "acme/sorter", "html_url": "https://github.com/acme/sorter", "stargazers_count": 120,
     "pushed_at": "2026-10-01T00:00:00Z", "license": {"spdx_id": "MIT"}, "description": "sorts files"},
    {"full_name": "bob/thing", "html_url": "https://github.com/bob/thing", "stargazers_count": 5,
     "pushed_at": "2025-01-01T00:00:00Z", "license": None, "description": ""},
]}


def scout():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=ITEMS)

    return RepoScout(http=httpx.Client(transport=httpx.MockTransport(handler))), seen


def test_lists_repos_with_license(make_ctx, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    agent, seen = scout()
    result = agent.run("find", {"need": "file organizer"}, make_ctx())
    repos = result.data["repos"]
    assert [r["name"] for r in repos] == ["acme/sorter", "bob/thing"]
    assert repos[0]["license"] == "MIT" and repos[1]["license"] == "none"
    assert "search/repositories" in seen["url"] and seen["auth"] is None
    assert "no license" in result.summary.lower()


def test_creates_approvals_and_waits(make_ctx):
    agent, _ = scout()
    ctx = make_ctx()
    result = agent.run("find", {"need": "x"}, ctx)
    assert result.status == "waiting_approval"
    pending = ctx.store.approvals()
    assert [a.kind for a in pending] == ["clone_repo", "clone_repo"]
    assert pending[0].payload == {"url": "https://github.com/acme/sorter"}


@pytest.mark.parametrize("url", ["https://evil.com/a/b", "https://github.com/a/b; rm -rf /", "file:///x",
                                 "https://github.com/a/b/../../c", "https://github.com/-a/b"])
def test_clone_rejects_bad_urls(tmp_path, url):
    with pytest.raises(ValueError):
        clone_handler({"url": url}, tmp_path)


def test_clone_runs_only_git(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    msg = clone_handler({"url": "https://github.com/acme/sorter"}, tmp_path)
    argv, kwargs = calls[0]
    assert argv[:4] == ["git", "clone", "--depth", "1"]
    assert argv[-1] == str(tmp_path / "acme__sorter")
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs.get("shell") in (None, False)
    assert "acme__sorter" in msg
