"""Opens things for the user: YouTube videos, GitHub repos, web links, and files in their default app.

Safety: YouTube, GitHub and a fixed list of well-known sites (Spotify, Gmail, WhatsApp Web…) open straight away,
with their search pages; any other site or any local file waits for approval;
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
from prometheus.sites import AUTO_HOSTS as SITE_HOSTS
from prometheus.sites import SEARCH, SITES

AUTO_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "github.com"}
VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
# Only these kinds of files may be opened (documents and media). Programs, scripts, installers, web pages,
# disk images, shortcuts and macro-enabled Office files are never opened.
OPENABLE = {".pdf", ".txt", ".md", ".csv", ".log", ".json", ".rtf", ".epub", ".docx", ".xlsx", ".pptx", ".odt", ".ods",
            ".odp", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".tif", ".tiff", ".mp3", ".wav",
            ".flac", ".m4a", ".ogg", ".mp4", ".mov", ".mkv", ".avi", ".webm"}
SAFE_URL = re.compile(r"^[A-Za-z0-9._~:/?#&=%+-]+$")
YOUTUBE_PATHS = re.compile(r"^/$|^/(watch|results|playlist)$|^/shorts/[\w-]{11}$")
YOUTU_BE_PATH = re.compile(r"^/[\w-]{11}$")
GITHUB_REPO_PATH = re.compile(r"^/$|^/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/?$")
TRANSCRIPT_CHARS = 12000
KNOWN_SITES = SITES
SPOTIFY_ITEM = re.compile(r"^/(track|album|playlist|artist|episode|show)/[A-Za-z0-9]{10,40}$|^/search/[^/]{1,200}$")
WIKI_ARTICLE = re.compile(r"^/wiki/[^/:]{1,200}$")


def infer_target(goal: str) -> str:
    """When the plan forgot the target, work it out from what was asked (a link, or a well-known site)."""
    m = re.search(r"https?://[^\s\"'<>]+", goal or "")
    if m:
        return m.group(0).rstrip(".,)")
    text = " " + " ".join(re.findall(r"[a-z]+", (goal or "").lower())) + " "
    for site in sorted(KNOWN_SITES, key=len, reverse=True):     # "youtube music" before "youtube"
        if f" {site} " in text:
            return KNOWN_SITES[site]
    return ""


def video_id(url: str) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if host not in AUTO_HOSTS or host == "github.com":
        return None
    m = VIDEO_ID.search(url)
    return m.group(1) if m else None


def _auto_open(url: str) -> bool:
    """True only for a plain YouTube video/search page or a GitHub repository home page."""
    if not SAFE_URL.match(url):
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username or parts.port:
            return False
    except ValueError:
        return False
    if host == "github.com":
        return bool(GITHUB_REPO_PATH.match(parts.path)) and not parts.query
    if host == "youtu.be":
        return bool(YOUTU_BE_PATH.match(parts.path))
    if host in AUTO_HOSTS:
        return bool(YOUTUBE_PATHS.match(parts.path))
    if host not in SITE_HOSTS:
        return False
    if url in SITES.values() or (parts.path in ("", "/") and not parts.query):
        return True                                   # the home page of a well-known site
    if host == "open.spotify.com":
        return bool(SPOTIFY_ITEM.match(parts.path)) and not parts.query
    if host == "en.wikipedia.org":
        return bool(WIKI_ARTICLE.match(parts.path)) or url.startswith(SEARCH["wikipedia"].split("{")[0])
    return any(url.startswith(t.split("{")[0]) for t in SEARCH.values())   # a search results page


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def system_open(path: str) -> None:
    """Open a file or folder with its default app."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - only reached after policy + extension checks and user approval
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", path], check=False, timeout=15)


def reveal(path: str) -> None:
    """Show a folder, or the folder holding a file with the file selected (Explorer on Windows)."""
    p = Path(path)
    if sys.platform == "win32":
        explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
        argv = [str(explorer), str(p)] if p.is_dir() else [str(explorer), f"/select,{p}"]
        subprocess.Popen(argv)               # noqa: S603 - fixed program, path checked by the caller
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(p if p.is_dir() else p.parent)],
                       check=False, timeout=15)


def open_output(path: str, how: str, policy, opener=None, revealer=None) -> str:
    """Open something listed under Outputs. Documents and media open in their app; anything else (code, scripts,
    unknown types) is only shown in its folder, because opening those could run them."""
    opener, revealer = opener or system_open, revealer or reveal
    p = Path(policy.check_read(path))
    if not p.exists():
        return "It isn't there any more."
    if how == "folder" or p.is_dir():
        revealer(str(p))
        return "Shown in its folder." if not p.is_dir() else "Opened the folder."
    if p.suffix.lower() in OPENABLE:
        opener(str(p))
        return f"Opened {p.name}."
    revealer(str(p))
    return f"{p.name} isn't a document or media file, so it is shown in its folder instead of opened."


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
    if not SAFE_URL.match(url):
        raise ValueError("That link contains characters a normal link doesn't have.")
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
    if ":" in Path(raw).name:
        raise ValueError("File names with ':' are not opened.")
    if not path.exists():
        raise ValueError(f"Not found: {path.name}")
    if not path.is_dir() and path.suffix.lower() not in OPENABLE:
        raise ValueError(f"{path.name} is not a document, picture, sound or video. Prometheus only opens those, "
                         "never programs, scripts or installers.")
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
    required_inputs = ("target",)

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
        if not SAFE_URL.match(url):
            return StepResult("failed", "I won't open that link: it contains characters a normal link doesn't have.")
        try:
            check_url(url)
        except (UnsafeURL, ValueError) as e:
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
        ctx.approvals.request("open_local", f"Open this on your laptop: {path}", {"path": str(path)})
        return StepResult("waiting_approval", f"Approve opening {path.name} on the Approvals page.", {})

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        target = str(inputs.get("target") or "").strip() or infer_target(goal)
        if not target:
            return StepResult("failed", "I need to know which website, link or file to open. Tell me which one.")
        kind = str(inputs.get("kind") or "").strip().lower()
        if target in KNOWN_SITES.values() and not kind:
            kind = "web"
        is_url = target.startswith(("http://", "https://"))
        if is_url and not SAFE_URL.match(target):
            return StepResult("failed", "I won't open that link: it contains characters a normal link doesn't have.")
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
