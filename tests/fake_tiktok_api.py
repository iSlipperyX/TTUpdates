"""A stand-in for TikTok's official API (open.tiktokapis.com/v2) on localhost, for tests.

It answers what the app calls: the token endpoint (codes and refresh tokens), user info, the
video list (newest first, 20 a page, cursor = the create_time to continue from), creator info,
the inbox/direct post init, the upload URL and the publish status. Numbers live on the object and
can be changed between readings. `fail_with` makes the next answers an error, like TikTok's.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FakeTikTokAPI:
    def __init__(self, username: str = "cats"):
        self.user = {"open_id": "oid-1", "username": username, "display_name": "Cats", "avatar_url": "",
                     "avatar_large_url": "", "bio_description": "clips", "follower_count": 10,
                     "following_count": 3, "likes_count": 100, "video_count": 0}
        self.videos: list[dict] = []  # newest first
        self.scope = ("user.info.basic,user.info.profile,user.info.stats,video.list,video.publish,"
                      "video.upload")
        self.fail_with = ""  # an error code to answer with (e.g. "rate_limit_exceeded")
        self.status = 200  # the HTTP status for fail_with answers
        self.calls: list[str] = []
        self.token_calls: list[dict] = []
        self.uploads: list[int] = []
        self.inits: list[dict] = []
        self.lock = threading.Lock()
        self._n = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, method: str) -> None:
                url = urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                with fake.lock:
                    fake.calls.append(f"{method} {url.path}")
                    status, body = fake.answer(method, url.path, parse_qs(url.query), raw)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._answer("GET")

            def do_POST(self):  # noqa: N802
                self._answer("POST")

            def do_PUT(self):  # noqa: N802
                self._answer("PUT")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v2"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # --- what the tests set up ---------------------------------------------------

    def add_video(self, vid: str, desc: str, views: int = 0, likes: int = 0, comments: int = 0, shares: int = 0,
                  create_time: float | None = None) -> dict:
        v = {"id": vid, "video_description": desc, "title": "", "create_time": int(create_time or time.time()),
             "share_url": f"https://www.tiktok.com/@{self.user['username']}/video/{vid}", "cover_image_url": "",
             "duration": 20, "view_count": views, "like_count": likes, "comment_count": comments,
             "share_count": shares}
        with self.lock:
            self.videos.insert(0, v)
            self.videos.sort(key=lambda x: x["create_time"], reverse=True)
            self.user["video_count"] = len(self.videos)
        return v

    def bump(self, vid: str, views: int = 0, likes: int = 0, comments: int = 0, shares: int = 0) -> None:
        with self.lock:
            v = next(v for v in self.videos if v["id"] == vid)
            v["view_count"] += views
            v["like_count"] += likes
            v["comment_count"] += comments
            v["share_count"] += shares

    def video_calls(self) -> int:
        return sum(1 for c in self.calls if c.endswith("/video/list/"))

    # --- the API ------------------------------------------------------------------

    def _error(self, code: str, status: int = 200) -> tuple[int, dict]:
        return status, {"data": {}, "error": {"code": code, "message": code, "log_id": "x"}}

    def answer(self, method: str, path: str, query: dict, raw: bytes) -> tuple[int, dict]:
        path = path.removeprefix("/v2")
        if path == "/oauth/token/":
            form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            self.token_calls.append(form)
            if form.get("grant_type") == "refresh_token" and form.get("refresh_token") == "dead":
                return 400, {"error": "invalid_grant", "error_description": "refresh token expired"}
            self._n += 1
            return 200, {"access_token": f"act-{self._n}", "refresh_token": f"rft-{self._n}", "open_id": "oid-1",
                         "scope": self.scope, "expires_in": 86400, "refresh_expires_in": 31536000}
        if path == "/oauth/revoke/":
            return 200, {}
        if path == "/upload/":
            self.uploads.append(len(raw))
            return 201, {}
        if self.fail_with:
            return self._error(self.fail_with, self.status)
        if path == "/user/info/":
            fields = (query.get("fields") or [""])[0].split(",")
            return 200, {"data": {"user": {k: v for k, v in self.user.items() if k in fields}},
                         "error": {"code": "ok", "message": ""}}
        if path == "/video/list/":
            body = json.loads(raw or b"{}")
            count = min(20, int(body.get("max_count") or 20))
            cursor = body.get("cursor")
            vids = [v for v in self.videos if cursor is None or v["create_time"] * 1000 < cursor]
            page = vids[:count]
            more = len(vids) > count
            return 200, {"data": {"videos": [dict(v) for v in page], "has_more": more,
                                  "cursor": page[-1]["create_time"] * 1000 if page else 0},
                         "error": {"code": "ok", "message": ""}}
        if path == "/post/publish/creator_info/query/":
            return 200, {"data": {"creator_username": self.user["username"], "creator_nickname": "Cats",
                                  "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                                  "max_video_post_duration_sec": 600},
                         "error": {"code": "ok", "message": ""}}
        if path in ("/post/publish/inbox/video/init/", "/post/publish/video/init/"):
            self.inits.append({"path": path, **json.loads(raw or b"{}")})
            return 200, {"data": {"publish_id": "pub-1", "upload_url": self.base + "/upload/"},
                         "error": {"code": "ok", "message": ""}}
        if path == "/post/publish/status/fetch/":
            inbox = any(i["path"].startswith("/post/publish/inbox") for i in self.inits)
            return 200, {"data": {"status": "SEND_TO_USER_INBOX" if inbox else "PUBLISH_COMPLETE",
                                  "publicaly_available_post_id": [] if inbox else [7001]},
                         "error": {"code": "ok", "message": ""}}
        return 404, {"error": {"code": "not_found", "message": path}}
