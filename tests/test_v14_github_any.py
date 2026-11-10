import base64

import httpx
import pytest

from prometheus.agents.github import GitHubAgent, repo_from


def api(routes, seen):
    def handler(request):
        seen.append(request)
        for key, (code, body) in routes.items():
            if request.url.path.endswith(key):
                return httpx.Response(code, json=body)
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


README = base64.b64encode(b"# AirLLM\nRun 70B models on a 4GB GPU.").decode()
ROUTES = {"/repos/lyogavin/airllm": (200, {"full_name": "lyogavin/airllm", "description": "70B inference on 4GB",
                                           "stargazers_count": 5000, "forks_count": 400, "language": "Python",
                                           "license": {"spdx_id": "Apache-2.0"}, "pushed_at": "2026-10-01T00:00:00Z"}),
          "/repos/lyogavin/airllm/contents/": (200, [{"name": "air_llm", "type": "dir"}, {"name": "README.md"}]),
          "/repos/lyogavin/airllm/readme": (200, {"content": README})}


def test_reads_other_public_repo_without_the_token(make_ctx, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_" + "x" * 50)
    seen = []
    agent = GitHubAgent(http=api(ROUTES, seen), repo="Reevias/Prometheus-")
    res = agent.run("what is airllm", {"action": "about", "repo": "https://github.com/lyogavin/airllm"}, make_ctx())
    assert res.status == "done"
    assert "70B inference on 4GB" in res.summary and "Stars 5000" in res.summary and "Run 70B models" in res.summary
    assert "air_llm/" in res.summary
    assert all("authorization" not in r.headers for r in seen)          # the key never left for another repo


def test_own_repo_still_uses_the_token(make_ctx, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_" + "y" * 50)
    seen = []
    routes = {"/repos/Reevias/Prometheus-/issues": (200, [])}
    agent = GitHubAgent(http=api(routes, seen), repo="Reevias/Prometheus-")
    assert agent.run("x", {"action": "list_issues"}, make_ctx()).summary == "No open issues."
    assert seen[0].headers["authorization"].startswith("Bearer github_pat_")


def test_missing_repo_and_writes_elsewhere(make_ctx):
    agent = GitHubAgent(http=api({}, []), repo="Reevias/Prometheus-")
    res = agent.run("x", {"action": "about", "repo": "nobody/nothing"}, make_ctx())
    assert res.status == "failed" and "no public repository called nobody/nothing" in res.summary
    res = agent.run("x", {"action": "create_issue", "repo": "lyogavin/airllm", "title": "hi"}, make_ctx())
    assert res.status == "failed" and "only changes its own" in res.summary


@pytest.mark.parametrize("raw,out", [("lyogavin/airllm", "lyogavin/airllm"),
                                     ("https://github.com/lyogavin/airllm", "lyogavin/airllm"),
                                     ("github.com/lyogavin/airllm.git", "lyogavin/airllm"),
                                     ("https://github.com/lyogavin/airllm/tree/main/air_llm", "lyogavin/airllm"),
                                     ("not a repo", ""), ("../../etc", "")])
def test_repo_names_and_links(raw, out):
    assert repo_from(raw) == out
