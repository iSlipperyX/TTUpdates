"""TikTok's official Login Kit (desktop), Display API and Content Posting API.

Needs your own app from developers.tiktok.com with the redirect URI
http://localhost:<port>/callback/ and the scopes in SCOPES (Settings → TikTok API walks through
it). Until TikTok audits the app, direct posts can only go to a *private* account and are forced
to "Only me"; drafts sent to the TikTok app's inbox can still be posted publicly from the phone.
methods.py decides which accounts use this; tiktok/__init__.py is the front door.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import math
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

import requests

from ..config import Settings
from ..store import Profile, TikTokVideo
from ..vault import protect, unprotect

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = ["user.info.basic", "user.info.profile", "user.info.stats", "video.list", "video.publish", "video.upload"]

# What each scope is for, in the words Settings → TikTok API uses
SCOPE_USES = {
    "user.info.basic": "your name and picture", "user.info.profile": "your @username and bio",
    "user.info.stats": "follower and like counts", "video.list": "your videos' views",
    "video.publish": "publishing videos", "video.upload": "sending drafts to the TikTok app",
}

PRIVACY_MAP = {"public": "PUBLIC_TO_EVERYONE", "friends": "MUTUAL_FOLLOW_FRIENDS", "private": "SELF_ONLY"}
PRIVACY_NAMES = {"PUBLIC_TO_EVERYONE": "Everyone", "MUTUAL_FOLLOW_FRIENDS": "Friends",
                 "FOLLOWER_OF_CREATOR": "Followers", "SELF_ONLY": "Only me"}
MB = 1024 * 1024
POLL_SECONDS = 5  # how often to ask whether TikTok has finished processing a post

Log = Callable[[str], None]

# One lock per tokens file: the live view (live.py) and a post can both find the access token
# expired at once, and two refreshes with the same refresh token would race.
_refresh_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _refresh_lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _refresh_locks.setdefault(str(path), threading.Lock())


class ApiError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


FRIENDLY = {
    "unaudited_client_can_only_post_to_private_accounts":
        "Your TikTok developer app isn't audited yet, so it can only post to a private account. Make the "
        "account private, or use the browser upload method.",
    "spam_risk_too_many_posts": "TikTok's daily post limit for this account has been reached. Try tomorrow.",
    "spam_risk_too_many_pending_share": "You already have 5 drafts waiting in the TikTok app inbox. Post or "
                                        "delete them first.",
    "spam_risk_user_banned_from_posting": "TikTok has blocked this account from posting through apps.",
    "reached_active_user_cap": "Your developer app hit its daily limit of posting users.",
    "privacy_level_option_mismatch": "That privacy option isn't allowed for this account right now.",
    "access_token_invalid": "The TikTok login expired. Connect the account again.",
    "scope_not_authorized": "The app wasn't given permission for this. Connect again and allow all permissions.",
    "rate_limit_exceeded": "Too many requests to TikTok. Wait a minute and try again.",
    "invalid_grant": "The TikTok API login has run out or was removed in TikTok's settings. Press Connect API "
                     "on the Account page again.",
    "url_ownership_unverified": "TikTok wants the app to pull videos from a verified website; TikTok Manager "
                                "uploads the file instead, so this shouldn't happen. Try again.",
}


class Window(Protocol):
    """The browser window the approval page opened in (login.BrowserWindow)."""

    @property
    def closed(self) -> bool: ...

    def wait_closed(self, timeout: float = 25) -> None: ...


@dataclass
class Tokens:
    access_token: str = ""
    refresh_token: str = ""
    open_id: str = ""
    scope: str = ""
    expires_at: float = 0.0
    refresh_expires_at: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "Tokens":
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        t = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        t.access_token, t.refresh_token = unprotect(t.access_token), unprotect(t.refresh_token)
        return t

    def save(self, path: Path) -> None:
        d = asdict(self)
        d["access_token"], d["refresh_token"] = protect(self.access_token), protect(self.refresh_token)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Whole or not at all: another thread (the live view, a post) may be reading it right now
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: being read this instant
                if attempt == 9:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.05)

    @staticmethod
    def clear(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    @property
    def usable(self) -> bool:
        return bool(self.refresh_token) and self.refresh_expires_at > time.time()


def _new_pkce() -> tuple[str, str]:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    # TikTok's desktop flow wants the SHA-256 as hex, not the usual base64url.
    return verifier, hashlib.sha256(verifier.encode("ascii")).hexdigest()


class TikTokAPI:
    """One connected TikTok account; its tokens are kept (encrypted) in `tokens_path`."""

    def __init__(self, settings: Settings, tokens_path: Path, log: Log = print):
        self.s = settings
        self.log = log
        self.tokens_path = tokens_path
        self.tokens = Tokens.load(tokens_path)
        self.http = requests.Session()
        self.wait_on_rate_limit = True  # the live view backs off by itself instead of waiting here

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.s.api_redirect_port}/callback/"

    def _need_app(self) -> None:
        if not self.s.api_client_key or not self.s.api_client_secret:
            raise ApiError("Add your TikTok developer app's client key and secret in Settings → TikTok API.")

    # --- OAuth ------------------------------------------------------------------

    def authorize_url(self, state: str, challenge: str) -> str:
        params = {
            "client_key": self.s.api_client_key, "scope": ",".join(SCOPES), "response_type": "code",
            "redirect_uri": self.redirect_uri, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }
        return AUTH_URL + "?" + urllib.parse.urlencode(params)

    def connect(self, timeout: float = 600, cancelled: Callable[[], bool] = lambda: False,
                open_page: Callable[[str], Window | None] | None = None) -> None:
        """Show TikTok's approval page and catch its redirect on localhost.

        `open_page(url)` opens it (the app uses the account's own browser profile, so the right
        TikTok account is already logged in there) and returns the window, which is closed once
        TikTok answers; without it, or when it returns None, the default browser opens it.
        """
        self._need_app()
        verifier, challenge = _new_pkce()
        state = secrets.token_urlsafe(16)
        result: dict[str, str] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                result.update({k: v[0] for k, v in q.items()})
                ok = "code" in q and q.get("state", [""])[0] == state
                body = ("<h2>Connected. You can close this tab and return to TikTok Manager.</h2>" if ok else
                        "<h2>TikTok didn't connect.</h2><p>Return to TikTok Manager for details.</p>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='font-family:sans-serif'>{body}</body></html>".encode())

            def log_message(self, *args):
                pass

        try:
            server = http.server.HTTPServer(("127.0.0.1", self.s.api_redirect_port), Handler)
        except OSError as exc:
            raise ApiError(f"Port {self.s.api_redirect_port} is busy; pick another in Settings "
                           "(and add the matching redirect URI to your TikTok app).") from exc
        server.timeout = 1
        url = self.authorize_url(state, challenge)
        window = None
        try:
            window = open_page(url) if open_page else None
            if window is None:
                webbrowser.open(url)
                self.log("Approve TikTok Manager in the browser tab that just opened. If that browser is logged in "
                         "to a different TikTok account, switch accounts on TikTok's page first.")
            else:
                self.log("Approve TikTok Manager on TikTok's page in the window that just opened (log in there "
                         "first if TikTok asks). The window closes by itself once you've approved.")
            end = time.time() + timeout
            gone = 0.0
            while not result and time.time() < end and not cancelled():
                server.handle_request()
                if window is not None and window.closed:
                    gone = gone or time.time()
                    if time.time() - gone > 2:  # a last moment for a redirect already on its way
                        break
        finally:
            server.server_close()
            if window is not None and not window.closed:
                time.sleep(1)  # let the "Connected" page show for a moment
                window.wait_closed()
        if cancelled() and not result:
            raise ApiError("Cancelled")
        if not result:
            if window is not None and window.closed:
                raise ApiError("The window was closed before TikTok Manager was approved.")
            raise ApiError("Timed out waiting for TikTok's approval page.")
        if result.get("state") != state:
            raise ApiError("TikTok's reply didn't match this login attempt; try again.")
        if "code" not in result:
            raise ApiError(f"TikTok login failed: {result.get('error_description') or result.get('error') or 'no code'}")
        self._token_request({"grant_type": "authorization_code", "code": result["code"],
                             "redirect_uri": self.redirect_uri, "code_verifier": verifier})
        granted = set(self.tokens.scope.split(","))
        missing = [s for s in SCOPES if s not in granted]
        if missing:
            self.log(f"⚠ TikTok didn't grant: {', '.join(missing)}. Some features won't work.")
        self.log("TikTok account connected.")

    def _token_request(self, data: dict) -> None:
        data = {"client_key": self.s.api_client_key, "client_secret": self.s.api_client_secret, **data}
        r = self.http.post(f"{API}/oauth/token/", data=data, timeout=30,
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
        body = r.json() if r.content else {}
        if "access_token" not in body:
            code = body.get("error", "")
            if data.get("grant_type") == "refresh_token" and code in ("invalid_grant", "invalid_request"):
                # The refresh token is dead (a year old, or access removed in TikTok's settings).
                Tokens.clear(self.tokens_path)
                self.tokens = Tokens()
                raise ApiError(FRIENDLY["invalid_grant"], "invalid_grant")
            raise ApiError(f"TikTok token error: {body.get('error_description') or code or r.status_code}", code)
        now = time.time()
        self.tokens = Tokens(
            access_token=body["access_token"], refresh_token=body.get("refresh_token") or self.tokens.refresh_token,
            open_id=body.get("open_id", ""), scope=body.get("scope", ""),
            expires_at=now + float(body.get("expires_in", 86400)) - 120,
            refresh_expires_at=now + float(body.get("refresh_expires_in", 31536000)),
        )
        self.tokens.save(self.tokens_path)

    def disconnect(self) -> None:
        if self.tokens.access_token and self.s.api_client_key:
            try:
                self.http.post(f"{API}/oauth/revoke/", timeout=15, data={
                    "client_key": self.s.api_client_key, "client_secret": self.s.api_client_secret,
                    "token": self.tokens.access_token})
            except requests.RequestException:
                pass
        Tokens.clear(self.tokens_path)
        self.tokens = Tokens()

    @property
    def connected(self) -> bool:
        return self.tokens.usable

    def granted(self, scope: str) -> bool:
        return scope in self.tokens.scope.split(",")

    def _token(self) -> str:
        if not self.tokens.usable:
            raise ApiError("This account isn't connected to the TikTok API. Press Connect API on the Account page.",
                           "not_connected")
        if time.time() >= self.tokens.expires_at:
            self._need_app()
            with _refresh_lock(self.tokens_path):
                fresh = Tokens.load(self.tokens_path)  # another thread may have refreshed them meanwhile
                if fresh.usable and fresh.expires_at > self.tokens.expires_at:
                    self.tokens = fresh
                if time.time() >= self.tokens.expires_at:
                    self._token_request({"grant_type": "refresh_token", "refresh_token": self.tokens.refresh_token})
        return self.tokens.access_token

    # --- requests ---------------------------------------------------------------

    def _call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=UTF-8"}
        for attempt in range(3):
            r = self.http.request(method, f"{API}{path}", params=params, headers=headers, timeout=60,
                                  data=json.dumps(body) if body is not None else None)
            if r.status_code == 429 and attempt < 2 and self.wait_on_rate_limit:
                time.sleep(15 * (attempt + 1))
                continue
            break
        try:
            payload = r.json()
        except ValueError:
            raise ApiError(f"TikTok returned HTTP {r.status_code}") from None
        err = payload.get("error") or {}
        code = err.get("code", "ok")
        if code != "ok":
            raise ApiError(FRIENDLY.get(code) or f"TikTok error: {err.get('message') or code}", code)
        return payload.get("data") or {}

    def profile(self) -> Profile:
        # Only ask for what was granted: TikTok refuses the whole request over one missing scope.
        fields = ["open_id", "avatar_url", "avatar_large_url", "display_name"]
        if self.granted("user.info.profile"):
            fields += ["username", "bio_description"]
        if self.granted("user.info.stats"):
            fields += ["follower_count", "following_count", "likes_count", "video_count"]
        u = self._call("GET", "/user/info/", params={"fields": ",".join(fields)}).get("user", {})
        return Profile(username=u.get("username", ""), nickname=u.get("display_name", ""),
                       avatar=u.get("avatar_large_url") or u.get("avatar_url", ""), bio=u.get("bio_description", ""),
                       followers=int(u.get("follower_count") or 0),
                       following=int(u.get("following_count") or 0), likes=int(u.get("likes_count") or 0),
                       video_count=int(u.get("video_count") or 0), fetched_at=time.time())

    def videos(self, max_videos: int = 200, username: str = "") -> list[TikTokVideo]:
        fields = ("id,create_time,cover_image_url,share_url,video_description,duration,title,"
                  "like_count,comment_count,share_count,view_count")
        out: list[TikTokVideo] = []
        cursor = None
        while len(out) < max_videos:
            body: dict = {"max_count": 20}
            if cursor:
                body["cursor"] = cursor
            data = self._call("POST", "/video/list/", params={"fields": fields}, body=body)
            for v in data.get("videos", []):
                out.append(TikTokVideo(
                    id=str(v["id"]), description=v.get("video_description") or v.get("title") or "",
                    create_time=float(v.get("create_time") or 0), url=v.get("share_url") or "",
                    cover=v.get("cover_image_url") or "", duration=float(v.get("duration") or 0),
                    views=int(v.get("view_count") or 0), likes=int(v.get("like_count") or 0),
                    comments=int(v.get("comment_count") or 0), shares=int(v.get("share_count") or 0)))
            if not data.get("has_more"):
                break
            cursor = data.get("cursor")
        return out

    def creator_info(self) -> dict:
        return self._call("POST", "/post/publish/creator_info/query/", body={})

    def check(self, drafts: bool = False, audited: bool = False) -> tuple[str, list[str]]:
        """Test the connection without posting: ("ok" | "warn", what was found, line by line).

        Raises ApiError when the API can't be used at all (no login, refused, wrong scopes).
        """
        lines, verdict = [], "ok"
        p = self.profile()
        who = f"@{p.username}" if p.username else (p.nickname or "your account")
        lines.append(f"Connected as {who}" + (f" ({p.followers:,} followers)" if self.granted("user.info.stats") else ""))
        missing = [s for s in SCOPES if not self.granted(s)]
        if missing:
            verdict = "warn"
            lines.append("Not allowed: " + ", ".join(SCOPE_USES.get(s, s) for s in missing) +
                         ". Connect again and leave every box ticked on TikTok's page.")
        need = "video.upload" if drafts else "video.publish"
        if not self.granted(need):
            raise ApiError(f"TikTok didn't allow {SCOPE_USES[need]} for this app. Check that the Content Posting "
                           "API is added to your developer app, then connect again.", "scope_not_authorized")
        if not drafts:
            info = self.creator_info()
            options = info.get("privacy_level_options") or []
            names = ", ".join(PRIVACY_NAMES.get(o, o) for o in options) or "none"
            lines.append(f"Can post as {info.get('creator_nickname') or who}, visible to: {names}")
            longest = int(info.get("max_video_post_duration_sec") or 0)
            if longest:
                lines.append(f"Videos up to {_mins(longest)}")
            if not audited:
                verdict = "warn"
                lines.append("Your developer app isn't marked as audited, so posts go up as 'Only me' (TikTok keeps "
                             "them private even after the audit). Sending drafts to the TikTok app avoids this.")
            elif "PUBLIC_TO_EVERYONE" not in options:
                verdict = "warn"
                lines.append("TikTok won't allow public posts for this account right now.")
        else:
            lines.append("Can send drafts to the TikTok app's inbox (at most 5 waiting at a time)")
        days = (self.tokens.refresh_expires_at - time.time()) / 86400
        lines.append(f"The API login lasts {int(days)} more days")
        return verdict, lines

    # --- posting ----------------------------------------------------------------

    def post(self, video: Path, caption: str, privacy: str, allow_comments: bool, allow_duet: bool,
             allow_stitch: bool, ai_label: bool, cover_ms: int = 1000, inbox: bool = False,
             progress: Callable[[str], None] | None = None,
             cancelled: Callable[[], bool] = lambda: False, expected_user: str = "",
             audited: bool = False, duration: float = 0.0) -> dict:
        """Upload and publish (or send to the TikTok app's inbox). Returns the final status payload.

        Unaudited apps can only publish "Only me" posts, so `privacy` is replaced by that (and
        said so) unless `audited`. The caption only goes along with a published post; a draft
        gets it typed in the TikTok app.
        """
        step = progress or self.log
        size = video.stat().st_size
        chunk, count = _chunking(size)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        if inbox:
            data = self._call("POST", "/post/publish/inbox/video/init/", body={"source_info": source})
        else:
            info = self.creator_info()
            actual, expected = info.get("creator_username") or "", expected_user.lstrip("@").strip()
            if actual and expected and actual.lower() != expected.lower():
                raise ApiError(f"These API tokens belong to @{actual}, not @{expected}. Connect the account again.")
            longest = int(info.get("max_video_post_duration_sec") or 0)
            if longest and duration > longest + 0.5:
                raise ApiError(f"This video is {_mins(duration)} long; TikTok lets this account post up to "
                               f"{_mins(longest)} through the API.")
            level = PRIVACY_MAP.get(privacy, "SELF_ONLY")
            if not audited and level != "SELF_ONLY":
                self.log("⚠ Your TikTok developer app isn't audited yet, so TikTok only allows 'Only me' posts. "
                         "Posting as 'Only me'.")
                level = "SELF_ONLY"
            options = info.get("privacy_level_options") or []
            if options and level not in options:
                if "SELF_ONLY" in options:
                    self.log(f"⚠ '{privacy}' isn't allowed for this account right now; posting as 'Only me'.")
                    level = "SELF_ONLY"
                else:
                    raise ApiError(FRIENDLY["privacy_level_option_mismatch"])
            step(f"Posting as {info.get('creator_nickname') or info.get('creator_username') or 'you'}…")
            post_info = {
                "title": caption[:2200], "privacy_level": level,
                "disable_comment": (not allow_comments) or bool(info.get("comment_disabled")),
                "disable_duet": (not allow_duet) or bool(info.get("duet_disabled")),
                "disable_stitch": (not allow_stitch) or bool(info.get("stitch_disabled")),
                "video_cover_timestamp_ms": cover_ms, "is_aigc": ai_label,
                "brand_content_toggle": False, "brand_organic_toggle": False,
            }
            data = self._call("POST", "/post/publish/video/init/", body={"post_info": post_info, "source_info": source})
        publish_id, upload_url = data["publish_id"], data["upload_url"]
        mime = {".mov": "video/quicktime", ".webm": "video/webm"}.get(video.suffix.lower(), "video/mp4")
        with video.open("rb") as f:
            for i in range(count):
                if cancelled():
                    raise ApiError("Cancelled")
                first = i * chunk
                last = size - 1 if i == count - 1 else first + chunk - 1
                f.seek(first)
                blob = f.read(last - first + 1)
                step(f"Uploading {100 * (i + 1) // count}%…")
                r = self.http.put(upload_url, data=blob, timeout=600, headers={
                    "Content-Type": mime, "Content-Length": str(len(blob)),
                    "Content-Range": f"bytes {first}-{last}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise ApiError(f"Upload failed at chunk {i + 1}/{count} (HTTP {r.status_code}).")
        step("TikTok is processing the video…")
        end = time.time() + 900
        status: dict = {}
        while time.time() < end:
            if cancelled():
                raise ApiError("Cancelled")
            time.sleep(POLL_SECONDS)
            status = self._call("POST", "/post/publish/status/fetch/", body={"publish_id": publish_id})
            st = status.get("status")
            if st == "FAILED":
                raise ApiError(f"TikTok couldn't publish the video: {status.get('fail_reason') or 'unknown reason'}")
            if st in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                break
        else:
            self.log("⚠ TikTok was still processing the video after 15 minutes. It usually appears on the "
                     "account later; the next stats refresh links it.")
        status["publish_id"] = publish_id
        return status


def _mins(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60} s" if seconds % 60 and seconds > 60 else \
        f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"


def _chunking(size: int) -> tuple[int, int]:
    """TikTok's rules: 5-64 MB chunks (last may be up to 128 MB); files under 5 MB go in one piece."""
    if size <= 64 * MB:
        return size, 1
    chunk = 10 * MB
    count = math.floor(size / chunk)
    return chunk, max(1, min(count, 1000))
