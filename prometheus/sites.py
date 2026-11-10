"""Well-known websites Prometheus may open straight away (and the web version of popular apps)."""
from __future__ import annotations

from urllib.parse import quote, urlsplit

# name said by the owner -> home page
SITES = {
    "youtube": "https://www.youtube.com/", "youtube music": "https://music.youtube.com/",
    "spotify": "https://open.spotify.com/", "netflix": "https://www.netflix.com/",
    "prime video": "https://www.primevideo.com/", "hotstar": "https://www.hotstar.com/",
    "google": "https://www.google.com/", "gmail": "https://mail.google.com/", "maps": "https://maps.google.com/",
    "google maps": "https://maps.google.com/", "drive": "https://drive.google.com/",
    "google drive": "https://drive.google.com/", "calendar": "https://calendar.google.com/",
    "google docs": "https://docs.google.com/", "translate": "https://translate.google.com/",
    "wikipedia": "https://www.wikipedia.org/", "github": "https://github.com/", "reddit": "https://www.reddit.com/",
    "whatsapp": "https://web.whatsapp.com/", "telegram": "https://web.telegram.org/",
    "discord": "https://discord.com/app", "instagram": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/", "x": "https://x.com/", "twitter": "https://x.com/",
    "linkedin": "https://www.linkedin.com/", "chatgpt": "https://chatgpt.com/", "claude": "https://claude.ai/",
    "gemini": "https://gemini.google.com/", "outlook": "https://outlook.live.com/",
    "teams": "https://teams.microsoft.com/", "office": "https://www.office.com/", "onedrive": "https://onedrive.live.com/",
    "amazon": "https://www.amazon.in/", "flipkart": "https://www.flipkart.com/", "twitch": "https://www.twitch.tv/",
    "stackoverflow": "https://stackoverflow.com/", "stack overflow": "https://stackoverflow.com/",
    "notion": "https://www.notion.so/", "canva": "https://www.canva.com/",
}
AUTO_HOSTS = {urlsplit(u).hostname for u in SITES.values()} | {"www.youtube.com", "youtube.com", "m.youtube.com",
                                                                "youtu.be", "en.wikipedia.org"}
# Search pages that only show results (nothing is sent or bought by opening them).
SEARCH = {
    "youtube": "https://www.youtube.com/results?search_query={}",
    "spotify": "https://open.spotify.com/search/{}",
    "google": "https://www.google.com/search?q={}",
    "wikipedia": "https://en.wikipedia.org/wiki/Special:Search?search={}",
    "amazon": "https://www.amazon.in/s?k={}",
    "maps": "https://www.google.com/maps/search/{}",
}


def site_for(name: str) -> str:
    """The website for a name the owner said ("Spotify", "whatsapp web"), or ''."""
    low = " ".join((name or "").lower().replace(".com", "").replace(" web", "").split())
    return SITES.get(low, "")


def search_url(site: str, query: str) -> str:
    template = SEARCH.get((site or "").lower())
    return template.format(quote(query.strip()[:200], safe="")) if template and query.strip() else ""
