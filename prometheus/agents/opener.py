"""Opens things for the user: YouTube videos, GitHub repos, web links, and files in their default app.

Safety: YouTube and GitHub links open straight away; any other site or any local file waits for approval;
programs and scripts are never opened; addresses on the user's own computer or network are refused.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus, urlsplit

from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, register, resolve_folder, untrusted
from prometheus.models.base import ProviderUnavailable
from prometheus.safety.paths import PathBlocked
from prometheus.safety.web import UnsafeURL, check_url

AUTO_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "github.com"}
VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
NEVER_OPEN = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
              ".wsh", ".hta", ".lnk", ".reg", ".dll", ".jar", ".py", ".pyw", ".sh", ".cpl", ".msc", ".pif", ".url",
              ".appref-ms", ".application", ".gadget", ".inf", ".scf", ".app", ".command", ".desktop"}
TRANSCRIPT_CHARS = 12000


def video_id(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    if host not in AUTO_HOSTS or host == "github.com":
        return None
    m = VIDEO_ID.search(url)
    return m.group(1) if m else None


def _auto_open(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in AUTO_HOSTS and not parts.username


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def system_open(path: str) -> None:
    """Open a file or folder with its default app."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - only reached after policy + extension checks and user approval
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", path], check=False, timeout=15)


def find_video(query: str) -> str | None:
    try:
        from ddgs import DDGS

        for r in DDGS().text(f"site:youtube.com {query}", max_results=8):
            url = r.get("href", "")
            if _auto_open(url) and video_id(url):
                return url
    except Exception:
        return None
    return None


def fetch_transcript(vid: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    try:
        fetched = YouTubeTranscriptApi().fetch(vid)
        return " ".join(getattr(s, "text", "") for s in fetched)
    except (AttributeError, TypeError):  # older library versions
        return " ".join(s["text"] for s in YouTubeTranscriptApi.get_transcript(vid))


def open_link_handler(payload: dict, launcher: Callable[[str], object] = webbrowser.open) -> str:
    url = str(payload.get("url", ""))
    try:
        check_url(url)
    except UnsafeURL as e:
        raise ValueError(str(e)) from e
    launcher(url)
    return f"Opened {url}"


def _checked_local(raw: str, policy) -> Path:
    try:
        path = policy.check_read(Path(raw))
    except PathBlocked as e:
        raise ValueError(str(e)) from e
    if not path.exists():
        raise ValueError(f"Not found: {path.name}")
    if path.is_file() and path.suffix.lower() in NEVER_OPEN:
        raise ValueError(f"{path.name} is a program or script. Prometheus never runs those.")
    return path


def open_local_handler(payload: dict, policy, launcher: Callable[[str], object] = system_open) -> str:
    path = _checked_local(str(payload.get("path", "")), policy)
    launcher(str(path))
    return f"Opened {path.name}"


@register
class Opener(Agent):
    name = "opener"
    description = ("Opens things on this laptop: plays a YouTube video (and can summarise it from its captions), "
                   "opens a GitHub repository page, a web link, or a file/folder in its normal app. It never runs "
                   "programs. Inputs: kind ('youtube', 'github', 'web' or 'file'), target (a link, 'owner/repo', "
                   "search words, or a path like Documents/notes.pdf), summarise ('true' to summarise a video), "
                   "open ('false' to summarise without opening).")
    tools = {"open_link", "open_other", "open_local", "web_search", "web_fetch", "call_model"}
    accepts_private = False

    def __init__(self, launcher: Callable[[str], object] | None = None,
                 find_video: Callable[[str], str | None] | None = None,
                 transcript: Callable[[str], str] | None = None):
        self.launcher = launcher or webbrowser.open
        self.find_video = find_video or globals()["find_video"]
        self.transcript = transcript or fetch_transcript

    def _summarise(self, ctx: AgentContext, url: str, question: str) -> str:
        vid = video_id(url)
        if not vid:
            return "I couldn't work out which video that is, so there's no summary."
        try:
            text = self.transcript(vid)
        except Exception:
            return "This video has no captions I can read, so I can't summarise it."
        if not text.strip():
            return "This video has no captions I can read, so I can't summarise it."
        prompt = (f"{question or 'Summarise this video in a few short points.'}\n\nVideo captions:\n"
                  + untrusted(text[:TRANSCRIPT_CHARS], url))
        try:
            reply = ctx.router.complete("research", [{"role": "user", "content": prompt}],
                                        "You summarise videos from their captions. " + UNTRUSTED_RULE, max_tokens=1200)
        except ProviderUnavailable as e:
            return f"I couldn't summarise it: {e}"
        return reply.text.strip()

    def _youtube(self, ctx: AgentContext, target: str, inputs: dict) -> StepResult:
        ctx.use(self.name, "open_link")
        if target.startswith(("http://", "https://")):
            if not _auto_open(target):
                return self._web(ctx, target)
            url = target
        else:
            url = self.find_video(target) or f"https://www.youtube.com/results?search_query={quote_plus(target)}"
        parts = []
        if not _truthy(inputs.get("open", "true")) and _truthy(inputs.get("summarise", "")):
            pass
        else:
            self.launcher(url)
            ctx.store.audit(self.name, "open_link", {"url": url})
            parts.append(f"Opened {url}")
        if _truthy(inputs.get("summarise", "")):
            parts.append(self._summarise(ctx, url, str(inputs.get("question", ""))))
        return StepResult("done", "\n".join(parts), {"url": url})

    def _web(self, ctx: AgentContext, url: str) -> StepResult:
        try:
            check_url(url)
        except UnsafeURL as e:
            return StepResult("failed", f"I won't open that link: {e}")
        if _auto_open(url):
            ctx.use(self.name, "open_link")
            self.launcher(url)
            ctx.store.audit(self.name, "open_link", {"url": url})
            return StepResult("done", f"Opened {url}", {"url": url})
        ctx.use(self.name, "open_other")
        ctx.approvals.request("open_link", f"Open this website: {url}", {"url": url})
        return StepResult("waiting_approval", f"Approve opening {url} on the Approvals page.", {"url": url})

    def _file(self, ctx: AgentContext, target: str) -> StepResult:
        ctx.use(self.name, "open_local")
        try:
            path = _checked_local(str(resolve_folder(target, ctx.home)), ctx.policy)
        except ValueError as e:
            return StepResult("failed", f"I can't open that: {e}")
        ctx.approvals.request("open_local", f"Open {path.name} on this laptop", {"path": str(path)})
        return StepResult("waiting_approval", f"Approve opening {path.name} on the Approvals page.", {})

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        target = str(inputs.get("target") or "").strip()
        if not target:
            return StepResult("failed", "Nothing to open: no target was given.")
        kind = str(inputs.get("kind") or "").strip().lower()
        is_url = target.startswith(("http://", "https://"))
        if not kind:
            if is_url:
                kind = "youtube" if video_id(target) else "web"
            elif REPO.match(target):
                kind = "github"
            else:
                kind = "file" if (resolve_folder(target, ctx.home)).exists() else "youtube"
        if kind == "youtube":
            return self._youtube(ctx, target, inputs)
        if kind == "github":
            url = target if is_url else (f"https://github.com/{target}" if REPO.match(target) else "")
            if not url:
                return StepResult("failed", "That doesn't look like a GitHub repository (use owner/name).")
            return self._web(ctx, url)
        if kind == "web":
            return self._web(ctx, target if is_url else f"https://{target}")
        return self._file(ctx, target)
