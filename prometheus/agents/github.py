"""Prometheus's own GitHub repository: read it freely; posting, changing or updating waits for approval.

Uses a fine-grained token limited to one repository. The token is only ever sent to api.github.com.
"""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path

import httpx

import prometheus
from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.config import get_secret, load_settings

API = "https://api.github.com"
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/[A-Za-z0-9._-]{1,100}$")
PATH = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,8}$")
BLOCKED_WRITE = (".github/", ".git")
READ_ACTIONS = {"status", "read_file", "list_issues", "list_prs"}
WRITE_ACTIONS = {"create_issue", "comment", "propose_change", "update_self"}
REASONS = {401: "the GitHub token was rejected", 403: "the token is not allowed to do that",
           404: "GitHub can't find that, or the token has no access to this repository",
           422: "GitHub refused the request as written", 429: "GitHub is rate limiting this token"}


class GitHubError(Exception):
    pass


def _check_path(path: str, write: bool = False) -> str:
    p = str(path or "").strip()
    parts = p.split("/")
    if (not PATH.fullmatch(p) or len(p) > 200 or ".." in parts or any(x == ".git" for x in parts)
            or (write and p.startswith(BLOCKED_WRITE))):
        raise GitHubError("That file path is not allowed.")
    return p


def update_self(code_dir: Path) -> str:
    """Download the newest version (fast-forward only) and install new packages. Restart afterwards."""
    from prometheus.launcher import update

    try:
        return update(Path(code_dir))
    except RuntimeError as e:
        raise GitHubError(str(e)) from e


@register
class GitHubAgent(Agent):
    name = "github"
    description = ("Reads Prometheus's own GitHub repository: latest commits, whether the tests passed, open issues and "
                   "pull requests, and file contents. Can also (each waits for the owner's approval) create an issue, "
                   "comment, propose a one-file change as a pull request, or update this laptop to the newest "
                   "version. Inputs: action = status | read_file (path) | list_issues | list_prs | create_issue "
                   "(title, body) | comment (number, body) | propose_change (path, content, title, body) | update_self.")
    tools = {"github_read", "github_write"}
    accepts_private = False

    def __init__(self, http: httpx.Client | None = None, repo: str | None = None, code_dir: Path | None = None):
        self.http = http or httpx.Client(timeout=20.0, follow_redirects=False)
        self._repo = repo
        self.code_dir = Path(code_dir) if code_dir else Path(prometheus.__file__).resolve().parent.parent

    @property
    def repo(self) -> str:
        repo = self._repo if self._repo is not None else load_settings().github_repo
        if not REPO.fullmatch(repo or ""):
            raise GitHubError("The GitHub repository name in Settings is not valid (use owner/name).")
        return repo

    # -- API ------------------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None):
        token = get_secret("GITHUB_TOKEN")
        if not token:
            raise GitHubError("No GitHub token yet. Add one in Settings, under GitHub.")
        try:
            resp = self.http.request(method, f"{API}/repos/{self.repo}{path}", json=body, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"})
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub can't be reached ({type(e).__name__}).") from e
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub: {REASONS.get(resp.status_code, f'error {resp.status_code}')}.")
        try:
            return resp.json()
        except ValueError as e:
            raise GitHubError("GitHub sent an unreadable reply.") from e

    # -- reads ------------------------------------------------------------------
    def _status(self) -> str:
        commits = self._call("GET", "/commits?per_page=5")
        runs = self._call("GET", "/actions/runs?per_page=3").get("workflow_runs", [])
        issues = [i for i in self._call("GET", "/issues?state=open&per_page=10") if not i.get("pull_request")]
        prs = self._call("GET", "/pulls?state=open&per_page=10")
        lines = [f"Repository {self.repo}", "", "Latest commits:"]
        lines += [f"- {c['sha'][:7]} {c['commit']['message'].splitlines()[0][:90]}" for c in commits]
        lines += ["", "Tests on GitHub:"] + [f"- {r.get('name')} on {r.get('head_branch')}: "
                                               f"{r.get('conclusion') or r.get('status')}" for r in runs]
        lines += ["", f"Open issues ({len(issues)}):"] + [f"- #{i['number']} {i['title'][:90]}" for i in issues]
        lines += ["", f"Open pull requests ({len(prs)}):"] + [f"- #{p['number']} {p['title'][:90]}" for p in prs]
        return "\n".join(lines)

    def _read_file(self, path: str) -> str:
        data = self._call("GET", f"/contents/{_check_path(path)}?ref=main")
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            raise GitHubError("That path is a folder or can't be shown.")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")[:12000]

    # -- writes (only called after the owner approved) --------------------------------
    def _create_issue(self, p: dict) -> str:
        title, body = str(p.get("title", "")).strip()[:200], str(p.get("body", ""))[:6000]
        if not title:
            raise GitHubError("An issue needs a title.")
        return "Created " + self._call("POST", "/issues", {"title": title, "body": body})["html_url"]

    def _comment(self, p: dict) -> str:
        number, body = str(p.get("number", "")), str(p.get("body", "")).strip()[:6000]
        if not number.isdigit() or not body:
            raise GitHubError("A comment needs an issue number and some text.")
        return "Commented: " + self._call("POST", f"/issues/{int(number)}/comments", {"body": body})["html_url"]

    def _propose_change(self, p: dict) -> str:
        path = _check_path(p.get("path"), write=True)
        content, title = str(p.get("content", "")), str(p.get("title", "")).strip()[:120]
        if not title or not content or len(content) > 100_000:
            raise GitHubError("A change needs a title and file content (up to 100,000 characters).")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30] or "change"
        branch = f"prometheus/{slug}-{int(time.time()) % 100000:05d}"
        base_sha = self._call("GET", "/git/ref/heads/main")["object"]["sha"]
        self._call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})
        try:
            existing = self._call("GET", f"/contents/{path}?ref=main").get("sha")
        except GitHubError:
            existing = None
        put = {"message": title, "content": base64.b64encode(content.encode("utf-8")).decode("ascii"), "branch": branch}
        if existing:
            put["sha"] = existing
        self._call("PUT", f"/contents/{path}", put)
        pr = self._call("POST", "/pulls", {"title": title, "head": branch, "base": "main",
                                            "body": str(p.get("body", ""))[:4000] + "\n\nOpened by Prometheus."})
        return "Opened " + pr["html_url"] + " (you review and merge it; main was not touched)."

    def perform_write(self, p: dict) -> str:
        from prometheus.safety.secrets import secret_problem

        action = str(p.get("action", ""))
        problem = secret_problem(" ".join(str(p.get(k, "")) for k in ("title", "body", "content", "path")))
        if problem:
            raise GitHubError(problem)
        if action == "create_issue":
            return self._create_issue(p)
        if action == "comment":
            return self._comment(p)
        if action == "propose_change":
            return self._propose_change(p)
        if action == "update_self":
            return update_self(self.code_dir)
        raise GitHubError(f"'{action}' is not a GitHub action I can do.")

    # -- the agent ------------------------------------------------------------------
    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        action = str(inputs.get("action") or "status").strip().lower()
        if action not in READ_ACTIONS | WRITE_ACTIONS:
            return StepResult("failed", f"'{action}' is not a GitHub action. I can: "
                                        + ", ".join(sorted(READ_ACTIONS | WRITE_ACTIONS)) + ".")
        try:
            repo = self.repo
            if action in WRITE_ACTIONS:
                ctx.use(self.name, "github_write")
                shown = {k: str(inputs.get(k, ""))[:8000] for k in ("title", "body", "number", "path", "content")
                         if inputs.get(k) not in (None, "")}
                from prometheus.safety.secrets import secret_problem

                problem = secret_problem(" ".join(shown.values()))
                if problem:        # never even queued: the approval would store the secret
                    return StepResult("failed", problem)
                what = {"create_issue": "Create an issue", "comment": "Post a comment", "update_self":
                        "Update Prometheus to the newest version", "propose_change": "Open a pull request"}[action]
                ctx.approvals.request("github_action", f"{what} on {repo}", {"action": action, **shown})
                return StepResult("waiting_approval", f"{what} on {repo} is waiting for your approval on the "
                                                      "Approvals page.")
            ctx.use(self.name, "github_read")
            if action == "status":
                text = self._status()
            elif action == "read_file":
                text = self._read_file(inputs.get("path"))
            elif action == "list_issues":
                text = "\n".join(f"- #{i['number']} {i['title']} (by {i['user']['login']})"
                                 for i in self._call("GET", "/issues?state=open&per_page=20")
                                 if not i.get("pull_request")) or "No open issues."
            else:
                text = "\n".join(f"- #{p['number']} {p['title']} (by {p['user']['login']})"
                                 for p in self._call("GET", "/pulls?state=open&per_page=20")) or "No open pull requests."
        except GitHubError as e:
            return StepResult("failed", str(e))
        return StepResult("done", text)


def github_action_handler(payload: dict, agent: GitHubAgent) -> str:
    try:
        return agent.perform_write(payload)
    except GitHubError as e:
        raise RuntimeError(str(e)) from e
