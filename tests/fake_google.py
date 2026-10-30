"""A fake Google (sign-in and the bits of the Drive API TikTok Manager uses), on 127.0.0.1, for the tests.

Point the app at it with TIKTOKMANAGER_GOOGLE_BASE=<server.url>. Files live in `server.files`
(id -> dict). Only the searches drive_sync makes are understood: finding a folder at the top of
My Drive by name, and "trashed = false" (everything the app can see).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FOLDER = "application/vnd.google-apps.folder"


class FakeGoogle(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.files: dict[str, dict] = {}
        self.page_size = 1000
        self.client_id = "123-abc.apps.googleusercontent.com"
        self.client_secret = "GOCSPX-test"
        self.refresh_tokens: set[str] = set()
        self.cut_downloads: dict[str, int] = {}  # file id -> bytes to send before dropping the connection (once)
        self.requests: list[tuple[str, str]] = []
        self.patches: list[tuple[str, dict]] = []
        self.drive_api_off = False
        self._n = 0
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def stop(self) -> None:
        self.shutdown()
        self.server_close()

    # ------------------------------------------------------------ building a Drive
    def add(self, name: str, parent: str = "root", content: bytes | None = None, folder: bool = False,
            created: str = "2026-10-29T12:00:00.000Z", description: str = "", props: dict | None = None,
            trashed: bool = False) -> str:
        self._n += 1
        fid = f"f{self._n:04d}"
        self.files[fid] = {"id": fid, "name": name, "mimeType": FOLDER if folder else "video/mp4",
                           "parents": [parent], "createdTime": created, "modifiedTime": created,
                           "description": description, "appProperties": dict(props or {}), "trashed": trashed,
                           "content": content}
        if name.endswith(".json"):
            self.files[fid]["mimeType"] = "application/json"
        return fid

    def folders(self, *names: str, parent: str = "root") -> str:
        """Make (or find) nested folders; returns the last one's id."""
        for name in names:
            known = next((f["id"] for f in self.files.values() if f["name"] == name and f["parents"] == [parent]
                          and f["mimeType"] == FOLDER), None)
            parent = known or self.add(name, parent, folder=True)
        return parent

    def meta(self, fid: str) -> dict:
        f = dict(self.files[fid])
        content = f.pop("content")
        f.pop("trashed")
        if content is not None:
            f["size"] = str(len(content))
            f["md5Checksum"] = hashlib.md5(content).hexdigest()
        return f


class _Handler(BaseHTTPRequestHandler):
    server: FakeGoogle

    def log_message(self, *args) -> None:
        pass

    def _send(self, status: int, body: bytes = b"", ctype: str = "application/json", headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode())

    def _authorized(self) -> bool:
        if self.headers.get("Authorization", "").startswith("Bearer access-"):
            return True
        self._json(401, {"error": {"code": 401, "message": "Invalid Credentials"}})
        return False

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self) -> None:  # noqa: N802
        self.server.requests.append(("POST", self.path))
        if self.path != "/token":
            return self._json(404, {})
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self._body().decode()).items()}
        if form.get("client_id") != self.server.client_id or form.get("client_secret") != self.server.client_secret:
            return self._json(401, {"error": "invalid_client", "error_description": "The OAuth client was not found."})
        if form.get("grant_type") == "authorization_code":
            if form.get("code") != "good-code" or not form.get("code_verifier"):
                return self._json(400, {"error": "invalid_grant"})
            token = f"refresh-{len(self.server.refresh_tokens) + 1}"
            self.server.refresh_tokens.add(token)
            return self._json(200, {"access_token": "access-1", "refresh_token": token, "expires_in": 3599,
                                    "scope": "https://www.googleapis.com/auth/drive.file openid email"})
        if form.get("grant_type") == "refresh_token":
            if form.get("refresh_token") not in self.server.refresh_tokens:
                return self._json(400, {"error": "invalid_grant", "error_description": "Token has been revoked."})
            return self._json(200, {"access_token": "access-2", "expires_in": 3599})
        return self._json(400, {"error": "unsupported_grant_type"})

    def do_GET(self) -> None:  # noqa: N802
        self.server.requests.append(("GET", self.path))
        parts = urllib.parse.urlsplit(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        if parts.path == "/userinfo":
            return self._json(200, {"email": "justyn@example.com"}) if self._authorized() else None
        if not self._authorized():
            return
        if self.server.drive_api_off:
            return self._json(403, {"error": {"code": 403, "message": "Google Drive API has not been used in "
                                              "project 1 before or it is disabled.",
                                              "errors": [{"reason": "accessNotConfigured"}]}})
        if parts.path == "/drive/v3/files":
            return self._list(query)
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", parts.path)
        if not m or m.group(1) not in self.server.files:
            return self._json(404, {"error": {"code": 404, "message": "File not found."}})
        fid = m.group(1)
        if query.get("alt") != "media":
            return self._json(200, self.server.meta(fid))
        content = self.server.files[fid]["content"] or b""
        start = 0
        rng = self.headers.get("Range", "")
        if rng:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            if start >= len(content):
                return self._send(416, b"")
        body = content[start:]
        status = 206 if rng else 200
        cut = self.server.cut_downloads.pop(fid, None)
        if cut is not None:  # say the whole length, send part of it, hang up
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[:cut])
            self.wfile.flush()
            self.close_connection = True
            return
        self._send(status, body, "video/mp4",
                   {"Content-Range": f"bytes {start}-{len(content) - 1}/{len(content)}"} if rng else None)

    def do_PATCH(self) -> None:  # noqa: N802
        self.server.requests.append(("PATCH", self.path))
        if not self._authorized():
            return
        m = re.fullmatch(r"/drive/v3/files/([^/?]+)", urllib.parse.urlsplit(self.path).path)
        if not m or m.group(1) not in self.server.files:
            return self._json(404, {"error": {"code": 404, "message": "File not found."}})
        data = json.loads(self._body() or b"{}")
        self.server.patches.append((m.group(1), data))
        self.server.files[m.group(1)]["appProperties"].update(data.get("appProperties") or {})
        self._json(200, {"id": m.group(1)})

    def _list(self, query: dict) -> None:
        q = query.get("q", "")
        files = [f for f in self.server.files.values() if not f["trashed"]]
        m = re.match(r"name = '((?:[^'\\]|\\.)*)' and mimeType = '[^']+' and 'root' in parents", q)
        if m:
            name = m.group(1).replace("\\'", "'").replace("\\\\", "\\")
            files = [f for f in files if f["name"] == name and f["parents"] == ["root"] and f["mimeType"] == FOLDER]
        elif q != "trashed = false":
            return self._json(400, {"error": {"code": 400, "message": f"Invalid query {q}"}})
        start = int(query.get("pageToken") or 0)
        size = min(int(query.get("pageSize") or 100), self.server.page_size)
        page = files[start:start + size]
        out = {"files": [self.server.meta(f["id"]) for f in page]}
        if start + size < len(files):
            out["nextPageToken"] = str(start + size)
        self._json(200, out)
