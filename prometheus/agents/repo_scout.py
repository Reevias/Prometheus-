"""Finds GitHub repos for a need. Cloning waits for the user's approval and never runs repo code."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import httpx

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.config import get_secret

GITHUB_REPO = re.compile(r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})/([A-Za-z0-9][A-Za-z0-9_.-]{0,99}?)(?:\.git)?$")


def clone_handler(payload: dict, quarantine: Path) -> str:
    url = str(payload.get("url", ""))
    m = GITHUB_REPO.match(url)
    if not m or ".." in url:
        raise ValueError(f"Not a GitHub repository link: {url}")
    owner, repo = m.group(1), m.group(2)
    target = Path(quarantine) / f"{owner}__{repo}"
    if target.exists():
        raise ValueError(f"Already cloned: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(["git", "clone", "--depth", "1", "--no-tags", "--", f"https://github.com/{owner}/{repo}",
                           str(target)], env=env, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:300]}")
    return f"Cloned into quarantine: {target} (nothing was run)."


@register
class RepoScout(Agent):
    name = "repo_scout"
    description = ("Finds GitHub repositories for a need and lists them with stars, last update and license. "
                   "Cloning waits for the user's approval. Inputs: need.")
    tools = {"github_search", "clone_repo"}
    accepts_private = False

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=15.0)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "github_search")
        ctx.use(self.name, "clone_repo")
        need = str(inputs.get("need") or goal)
        headers = {"Accept": "application/vnd.github+json"}
        token = get_secret("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = self.http.get("https://api.github.com/search/repositories",
                                 params={"q": need, "sort": "stars", "per_page": 5}, headers=headers)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except (httpx.HTTPError, ValueError) as e:
            return StepResult("failed", f"GitHub search failed: {e}")
        repos = [{
            "name": it.get("full_name", ""),
            "url": it.get("html_url", ""),
            "stars": int(it.get("stargazers_count", 0)),
            "updated": str(it.get("pushed_at", ""))[:10],
            "license": ((it.get("license") or {}).get("spdx_id") or "none"),
            "description": (it.get("description") or "")[:200],
        } for it in items[:5]]
        if not repos:
            return StepResult("failed", f"No repositories found for: {need}")
        lines = []
        for r in repos:
            warn = " — no license: its code can't be reused" if r["license"] in ("none", "NOASSERTION") else ""
            lines.append(f"- {r['name']} ★{r['stars']} (updated {r['updated']}, license {r['license']}){warn}")
            ctx.approvals.request("clone_repo", f"Clone {r['name']} into quarantine (code is not run)",
                                  {"url": r["url"]})
        summary = f"Found {len(repos)} repositories for '{need}':\n" + "\n".join(lines) + \
            "\nApprove any you want cloned on the Approvals page."
        ctx.store.add_agent_note(ctx.task_id, self.name, summary)
        return StepResult("waiting_approval", summary, {"repos": repos})
