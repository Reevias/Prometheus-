"""Google Calendar and Drive with the owner's own Google sign-in (OAuth for a desktop app, PKCE, loopback redirect).

The owner makes a free Google Cloud "Desktop app" client once and pastes its ID and secret in Settings; signing in
happens in their browser. The refresh token is kept in the key store. Scopes: calendar events, and Drive read only.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from prometheus.config import get_secret, set_secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
DRIVE = "https://www.googleapis.com/drive/v3/files"
SCOPES = ("https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/drive.readonly "
          "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send "
          "https://www.googleapis.com/auth/drive.file")       # drive.file: only files Prometheus itself makes
READ_CHARS = 20_000
EXPORTS = {"application/vnd.google-apps.document": "text/plain",
           "application/vnd.google-apps.spreadsheet": "text/csv",
           "application/vnd.google-apps.presentation": "text/plain"}


class GoogleError(Exception):
    pass


def client() -> tuple[str, str]:
    raw = get_secret("GOOGLE_CLIENT") or ""
    cid, _, csecret = raw.partition("|")
    return cid, csecret


def start_sign_in(redirect_uri: str) -> tuple[str, dict]:
    """The Google page to open, and what to remember until Google sends the owner back."""
    cid, _ = client()
    if not cid:
        raise GoogleError("Paste your Google client ID and secret in Settings first.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    url = AUTH_URL + "?" + urlencode({"client_id": cid, "redirect_uri": redirect_uri, "response_type": "code",
                                      "scope": SCOPES, "state": state, "code_challenge": challenge,
                                      "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
    return url, {"state": state, "verifier": verifier, "redirect_uri": redirect_uri, "made": time.time()}


def finish_sign_in(code: str, pending: dict, http: httpx.Client | None = None) -> None:
    cid, csecret = client()
    http = http or httpx.Client(timeout=30.0)
    resp = http.post(TOKEN_URL, data={"code": code, "client_id": cid, "client_secret": csecret,
                                      "redirect_uri": pending["redirect_uri"], "grant_type": "authorization_code",
                                      "code_verifier": pending["verifier"]})
    if resp.status_code != 200:
        raise GoogleError("Google didn't accept the sign-in. Try again from Settings.")
    data = resp.json()
    if not data.get("refresh_token"):
        raise GoogleError("Google didn't give a lasting sign-in. Remove Prometheus at myaccount.google.com/permissions "
                          "and sign in again.")
    set_secret("GOOGLE_TOKEN", data["refresh_token"])


class Google:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30.0)
        self._access, self._until = "", 0.0

    def _token(self) -> str:
        if self._access and time.time() < self._until - 60:
            return self._access
        refresh = get_secret("GOOGLE_TOKEN")
        cid, csecret = client()
        if not (refresh and cid):
            raise GoogleError("Google isn't connected. Sign in with Google in Settings, under Connections.")
        resp = self.http.post(TOKEN_URL, data={"client_id": cid, "client_secret": csecret, "refresh_token": refresh,
                                               "grant_type": "refresh_token"})
        if resp.status_code != 200:
            raise GoogleError("The Google sign-in has expired. Sign in again in Settings.")
        data = resp.json()
        self._access, self._until = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self._access

    def _call(self, method: str, url: str, **kw):
        try:
            resp = self.http.request(method, url, headers={"Authorization": f"Bearer {self._token()}"}, **kw)
        except httpx.HTTPError as e:
            raise GoogleError(f"Couldn't reach Google ({type(e).__name__}).") from e
        if resp.status_code == 404:
            raise GoogleError("Google couldn't find that.")
        if resp.status_code >= 400:
            raise GoogleError(f"Google answered with error {resp.status_code}.")
        return resp

    # -- calendar ---------------------------------------------------------------------
    @staticmethod
    def _iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")

    def events(self, start: float, end: float, query: str = "") -> list[dict]:
        params = {"timeMin": self._iso(start), "timeMax": self._iso(end), "singleEvents": "true",
                  "orderBy": "startTime", "maxResults": 50}
        if query:
            params["q"] = query[:100]
        items = self._call("GET", CAL, params=params).json().get("items", [])
        out = []
        for e in items:
            s, f = e.get("start", {}), e.get("end", {})
            out.append({"id": e.get("id", ""), "title": e.get("summary", "(no title)"),
                        "start": s.get("dateTime") or s.get("date", ""), "end": f.get("dateTime") or f.get("date", ""),
                        "location": e.get("location", "")})
        return out

    def add_event(self, title: str, start: float, minutes: int = 60, location: str = "") -> dict:
        body = {"summary": title[:200], "start": {"dateTime": self._iso(start)},
                "end": {"dateTime": self._iso(start + minutes * 60)}}
        if location:
            body["location"] = location[:200]
        return self._call("POST", CAL, json=body).json()

    def delete_event(self, event_id: str) -> None:
        self._call("DELETE", f"{CAL}/{event_id}")

    def move_event(self, event_id: str, start: float, minutes: int = 60) -> dict:
        body = {"start": {"dateTime": self._iso(start)}, "end": {"dateTime": self._iso(start + minutes * 60)}}
        return self._call("PATCH", f"{CAL}/{event_id}", json=body).json()

    # -- drive ------------------------------------------------------------------------
    def drive_search(self, query: str, limit: int = 15) -> list[dict]:
        words = query.replace("'", " ").replace("\\", " ").strip()[:100]
        q = f"(name contains '{words}' or fullText contains '{words}') and trashed = false"
        files = self._call("GET", DRIVE, params={"q": q, "pageSize": limit, "orderBy": "modifiedTime desc",
                                                 "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"}).json()
        return files.get("files", [])

    def drive_read(self, file_id: str) -> tuple[str, str]:
        meta = self._call("GET", f"{DRIVE}/{file_id}", params={"fields": "name,mimeType"}).json()
        mime = meta.get("mimeType", "")
        if mime in EXPORTS:
            text = self._call("GET", f"{DRIVE}/{file_id}/export", params={"mimeType": EXPORTS[mime]}).text
        elif mime.startswith("text/") or mime in ("application/json", "text/csv"):
            text = self._call("GET", f"{DRIVE}/{file_id}", params={"alt": "media"}).text
        elif mime == "application/pdf":
            import io

            from pypdf import PdfReader

            data = self._call("GET", f"{DRIVE}/{file_id}", params={"alt": "media"}).content
            text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages[:30])
        else:
            raise GoogleError(f"I can read Docs, Sheets, Slides, text and PDF files, not {mime or 'this kind'}.")
        return meta.get("name", ""), text[:READ_CHARS]
