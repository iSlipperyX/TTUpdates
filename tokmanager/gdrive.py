"""Google Drive: signing in once, listing ClipMonster's uploads and downloading them (drive_sync.py).

Plain HTTPS (no extra package), the way Google documents it for desktop apps, and the same way
ClipMonster's own gdrive.py does it:

* Signing in uses an OAuth client from your Google Cloud project: the one ClipMonster uses
  (read from its settings on this PC, or pasted in Settings → Google Drive on another PC). The
  browser opens Google's sign-in page, which hands a code back to a one-off server on 127.0.0.1,
  with PKCE. Only the drive.file scope is asked for. Google grants that per Cloud project, so
  TikTok Manager sees the files and folders ClipMonster made, and nothing else in your Drive.
* The refresh token is kept in %APPDATA%\\TikTokManager\\google-drive.json, encrypted for your
  Windows account (DPAPI, vault.py), so it's no use copied to another PC or account.
* Downloads go to a .part file and carry on from where they stopped (HTTP Range).

TIKTOKMANAGER_GOOGLE_BASE points every address at a fake Google, for the tests.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from .config import VERSION, app_dir
from .vault import protect, unprotect

SCOPES = "https://www.googleapis.com/auth/drive.file openid email"
FOLDER_TYPE = "application/vnd.google-apps.folder"
CHUNK = 1024 * 1024  # read and written this much at a time while downloading
RETRY_WAITS = (2.0, 4.0, 8.0, 16.0)  # rate limits, busy servers and dropped connections are tried again
SIGN_IN_TIMEOUT = 300.0
FILE_FIELDS = "id,name,mimeType,parents,size,md5Checksum,createdTime,modifiedTime,description,appProperties"
SETUP_HELP = "https://github.com/iSlipperyX/TTUpdates/blob/main/docs/google-drive.md"
CLIPMONSTER_HELP = "https://github.com/iSlipperyX/ClipMostr/blob/main/docs/google-drive-setup.md"


def _base(name: str) -> str:
    fake = os.environ.get("TIKTOKMANAGER_GOOGLE_BASE", "").rstrip("/")
    if fake:
        return fake + {"auth": "/o/oauth2/v2/auth", "token": "/token", "userinfo": "/userinfo",
                       "api": "/drive/v3"}[name]
    return {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
            "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
            "api": "https://www.googleapis.com/drive/v3"}[name]


class DriveError(RuntimeError):
    """A plain-English reason a Drive call failed. kind: "" (try again later) | "sign_in" (sign in
    again) | "setup" (the client ID or secret is wrong, or the Drive API is off) | "gone" (deleted
    in Drive) | "disk" (no room on this PC) | "cancelled"."""

    def __init__(self, message: str, kind: str = ""):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------- the saved sign-in

def token_path() -> Path:
    return app_dir() / "google-drive.json"


@dataclass
class Account:
    email: str
    client_id: str
    refresh_token: str


def load_account(client_id: str = "") -> Account | None:
    """The saved sign-in, when there is one (for this client ID, when one is given)."""
    try:
        data = json.loads(token_path().read_text(encoding="utf-8"))
        token = unprotect(str(data.get("refresh_token") or ""))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not token or (client_id and data.get("client_id") != client_id.strip()):
        return None
    return Account(str(data.get("email") or ""), str(data.get("client_id") or ""), token)


def save_account(account: Account) -> None:
    path = token_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"email": account.email, "client_id": account.client_id,
                               "refresh_token": protect(account.refresh_token), "saved": time.time()}, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


def sign_out() -> None:
    """Forget the sign-in on this PC. Google isn't asked to cancel it: TikTok Manager shares
    ClipMonster's OAuth client, and cancelling it there would sign ClipMonster out too."""
    token_path().unlink(missing_ok=True)
    _tokens.clear()


# ---------------------------------------------------------------- HTTP

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _check_address(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost"):
        raise DriveError(f"Refusing to talk to Google over an insecure address: {url}")


def _open(method: str, url: str, token: str = "", body: bytes | None = None, headers: dict | None = None,
          timeout: float = 60):
    """The open response (the caller reads and closes it), or (status, headers, body) for an HTTP error."""
    _check_address(url)
    req = urllib.request.Request(url, data=body, method=method, headers={
        "User-Agent": f"TikTokManager/{VERSION}", **({"Authorization": f"Bearer {token}"} if token else {}),
        **(headers or {})})
    try:
        return _opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read()
        except (OSError, http.client.HTTPException):
            data = b""
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, data
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        raise DriveError(f"Couldn't reach Google Drive ({getattr(exc, 'reason', exc)}). It's tried again "
                         "later.") from exc


def _request(method: str, url: str, *, token: str = "", body: bytes | None = None,
             headers: dict | None = None, timeout: float = 60) -> tuple[int, dict, bytes]:
    """(status, headers, body); HTTP errors are answers too, network trouble is a DriveError."""
    resp = _open(method, url, token, body, headers, timeout)
    if isinstance(resp, tuple):
        return resp
    try:
        with resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except (http.client.HTTPException, TimeoutError, OSError) as exc:
        raise DriveError(f"The connection to Google Drive dropped ({exc}). It's tried again later.") from exc


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _reason(body: bytes) -> tuple[str, str]:
    """(Google's own reason code, its message) from an error answer."""
    data = _json(body)
    err = data.get("error")
    if isinstance(err, dict):
        reasons = [e.get("reason", "") for e in err.get("errors") or [] if isinstance(e, dict)]
        return (reasons[0] if reasons else str(err.get("status") or "")), str(err.get("message") or "")
    return str(err or ""), str(data.get("error_description") or "")


def _post(url: str, form: dict, timeout: float = 30) -> dict:
    status, _, body = _request("POST", url, body=urllib.parse.urlencode(form).encode(),
                               headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=timeout)
    if status != 200:
        code, message = _reason(body)
        if code in ("invalid_client", "unauthorized_client"):
            raise DriveError("Google doesn't accept the OAuth client ID and secret. Use the ones ClipMonster "
                             "uses (Google Auth Platform → Clients in your Google Cloud project; it must be a "
                             "Desktop app client).", "setup")
        if code == "invalid_grant":
            raise DriveError("Google has signed TikTok Manager out of your Drive (the sign-in expired or was "
                             "removed). Sign in again in Settings → Google Drive.", "sign_in")
        raise DriveError(f"Google answered {status}{': ' + message if message else ''}.")
    return _json(body)


# ---------------------------------------------------------------- signing in

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _Callback(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        self.server.result = {k: v[0] for k, v in query.items()}  # type: ignore[attr-defined]
        ok = "code" in query
        page = ("<h2>TikTok Manager is signed in to Google Drive.</h2><p>You can close this tab.</p>" if ok else
                "<h2>Google Drive wasn't connected.</h2><p>You can close this tab and try again in TikTok "
                "Manager.</p>")
        body = (f"<!doctype html><meta charset=utf-8><title>TikTok Manager</title><body style='font-family:"
                f"sans-serif;background:#121212;color:#eee;padding:40px'>{page}</body>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def sign_in(client_id: str, client_secret: str, open_browser: Callable[[str], object] | None = None,
            cancelled: Callable[[], bool] | None = None, timeout: float = SIGN_IN_TIMEOUT) -> Account:
    """Open Google's sign-in page in the browser and wait for it to come back. Saves and returns
    the account."""
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        raise DriveError("Enter the OAuth client ID and secret first (the ones ClipMonster uses).", "setup")
    verifier = _b64url(secrets.token_bytes(48))
    state = secrets.token_urlsafe(16)
    server = HTTPServer(("127.0.0.1", 0), _Callback)
    server.result = {}  # type: ignore[attr-defined]
    server.timeout = 0.5
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = _base("auth") + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
        "code_challenge_method": "S256", "state": state, "access_type": "offline", "prompt": "consent"})
    try:
        threading.Thread(target=open_browser or webbrowser.open, args=(url,), daemon=True).start()
        end = time.monotonic() + timeout
        while not server.result:  # type: ignore[attr-defined]
            if cancelled and cancelled():
                raise DriveError("Signing in was cancelled.", "cancelled")
            if time.monotonic() > end:
                raise DriveError("Google's sign-in page didn't come back within 5 minutes. Try again.")
            server.handle_request()
        result = server.result  # type: ignore[attr-defined]
    finally:
        server.server_close()
    if result.get("state") != state:
        raise DriveError("The answer from Google's sign-in page didn't match. Try again.")
    if "error" in result:
        if result["error"] == "access_denied":
            raise DriveError("Access to Google Drive wasn't allowed on Google's page, so nothing was connected.")
        raise DriveError(f"Google's sign-in page said: {result['error']}.")
    tokens = _post(_base("token"), {"code": result.get("code", ""), "client_id": client_id,
                                    "client_secret": client_secret, "redirect_uri": redirect,
                                    "grant_type": "authorization_code", "code_verifier": verifier})
    refresh = tokens.get("refresh_token") or ""
    if not refresh:
        raise DriveError("Google didn't send a lasting sign-in. Remove the app's access at "
                         "myaccount.google.com/connections and sign in again.")
    if "drive.file" not in str(tokens.get("scope") or "drive.file"):
        raise DriveError("Drive access was unticked on Google's page, so TikTok Manager can't see the clips. "
                         "Sign in again and leave it ticked.")
    access = tokens.get("access_token") or ""
    email = ""
    if access:
        status, _, body = _request("GET", _base("userinfo"), token=access, timeout=20)
        email = str(_json(body).get("email") or "") if status == 200 else ""
        _tokens[client_id] = (access, time.time() + float(tokens.get("expires_in") or 3600) - 120)
    account = Account(email, client_id, refresh)
    save_account(account)
    return account


_tokens: dict[str, tuple[str, float]] = {}  # client ID -> (access token, good until)
_token_lock = threading.Lock()


def access_token(client_id: str, client_secret: str, force: bool = False) -> str:
    """A current access token (they last an hour), from the saved sign-in."""
    client_id = client_id.strip()
    with _token_lock:
        cached = _tokens.get(client_id)
        if cached and not force and cached[1] > time.time():
            return cached[0]
        account = load_account(client_id)
        if account is None:
            raise DriveError("TikTok Manager isn't signed in to Google Drive. Sign in in Settings → Google Drive.",
                             "sign_in")
        tokens = _post(_base("token"), {"refresh_token": account.refresh_token, "client_id": client_id,
                                        "client_secret": client_secret.strip(), "grant_type": "refresh_token"})
        token = tokens.get("access_token") or ""
        if not token:
            raise DriveError("Google didn't send an access token. Try signing in again.", "sign_in")
        _tokens[client_id] = (token, time.time() + float(tokens.get("expires_in") or 3600) - 120)
        return token


# ---------------------------------------------------------------- Drive

def _quote(text: str) -> str:
    """A value inside a Drive search's single quotes."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


class Drive:
    """Calls to Drive as the signed-in account, retrying what's worth retrying."""

    def __init__(self, client_id: str, client_secret: str, sleep: Callable[[float], None] = time.sleep):
        self.client_id, self.client_secret, self.sleep = client_id, client_secret, sleep

    def _problem(self, status: int, body: bytes, fresh: bool) -> DriveError | None:
        """The error for an answer that isn't worth trying again (None: try again)."""
        code, message = _reason(body)
        if status == 401:
            return None if not fresh else DriveError(
                "Google Drive stopped accepting TikTok Manager's sign-in. Sign in again in Settings → Google "
                "Drive.", "sign_in")
        if status == 404:
            return DriveError(f"Drive says it's not there any more{': ' + message if message else ''}.", "gone")
        if status == 403 and (code in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used" in message):
            return DriveError("The Google Drive API isn't turned on in your Google Cloud project. Turn it on "
                              "(APIs & Services → Library → Google Drive API → Enable) and try again.", "setup")
        if status == 429 or status >= 500 or code in ("rateLimitExceeded", "userRateLimitExceeded"):
            return None
        return DriveError(f"Google Drive answered {status}{': ' + message if message else ''}.")

    def call(self, method: str, url: str, *, body: bytes | None = None, headers: dict | None = None,
             timeout: float = 60, ok: tuple[int, ...] = (200,)) -> tuple[int, dict, bytes]:
        waits = list(RETRY_WAITS)
        fresh = False
        while True:
            token = access_token(self.client_id, self.client_secret, force=fresh)
            try:
                status, head, data = _request(method, url, token=token, body=body, headers=headers, timeout=timeout)
            except DriveError:
                if not waits:
                    raise
                self.sleep(waits.pop(0))
                continue
            if status in ok:
                return status, head, data
            err = self._problem(status, data, fresh)
            if status == 401 and err is None:  # the access token ran out early: get a new one, once
                fresh = True
                continue
            if err is not None:
                raise err
            if not waits:
                raise DriveError(f"Google Drive is busy (it answered {status}). It's tried again later.")
            self.sleep(waits.pop(0))

    def get_json(self, method: str, url: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json; charset=UTF-8"} if body is not None else None
        return _json(self.call(method, url, body=body, headers=headers)[2])

    # ------------------------------------------------------------ listing
    def search(self, q: str, fields: str = FILE_FIELDS) -> list[dict]:
        """Every file matching the Drive search `q` (all pages)."""
        out: list[dict] = []
        page = ""
        while True:
            params = {"q": q, "fields": f"nextPageToken,files({fields})", "spaces": "drive", "pageSize": "1000"}
            if page:
                params["pageToken"] = page
            data = self.get_json("GET", _base("api") + "/files?" + urllib.parse.urlencode(params))
            out += [f for f in data.get("files") or [] if isinstance(f, dict) and f.get("id")]
            page = str(data.get("nextPageToken") or "")
            if not page:
                return out

    def find_top(self, name: str) -> str:
        """The id of the folder called `name` at the top of My Drive (ClipMonster's), or ""."""
        q = f"name = '{_quote(name)}' and mimeType = '{FOLDER_TYPE}' and 'root' in parents and trashed = false"
        found = self.search(q, "id,createdTime")
        found.sort(key=lambda f: str(f.get("createdTime") or ""))  # the first one made, if there are two
        return str(found[0]["id"]) if found else ""

    def everything(self) -> list[dict]:
        """Every file and folder TikTok Manager can see and that isn't in the bin. With drive.file
        that's only what ClipMonster (or TikTok Manager) made, so it's a short list."""
        return self.search("trashed = false")

    def set_properties(self, file_id: str, props: dict[str, str]) -> None:
        """Private notes on a file (appProperties), e.g. which PC took a clip."""
        self.get_json("PATCH", _base("api") + f"/files/{urllib.parse.quote(file_id)}?fields=id",
                      {"appProperties": props})

    def read(self, file_id: str, limit: int = 4 * 1024 * 1024) -> bytes:
        """A small file's contents (a clips.json)."""
        _, _, data = self.call("GET", _base("api") + f"/files/{urllib.parse.quote(file_id)}?alt=media", timeout=60)
        if len(data) > limit:
            raise DriveError("That file in Drive is far bigger than expected.")
        return data

    # ------------------------------------------------------------ downloading
    def download(self, file_id: str, dest: Path, size: int, progress: Callable[[float], None] | None = None,
                 cancelled: Callable[[], bool] | None = None, md5: str = "") -> None:
        """Download a file to `dest` by way of `dest`.part, carrying on from a .part left by an
        earlier try. `dest` only appears (or is replaced) once the whole file is there and, given
        Drive's `md5`, is the same as in Drive."""
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = _base("api") + f"/files/{urllib.parse.quote(file_id)}?alt=media"
        waits = list(RETRY_WAITS)
        fresh = False
        while True:
            have = part.stat().st_size if part.is_file() else 0
            if size and have > size:
                part.unlink()
                have = 0
            if size and have == size:
                break
            token = access_token(self.client_id, self.client_secret, force=fresh)
            try:
                resp = _open("GET", url, token, headers={"Range": f"bytes={have}-"} if have else None, timeout=120)
            except DriveError:
                if not waits:
                    raise
                self.sleep(waits.pop(0))
                continue
            if isinstance(resp, tuple):
                status, _, data = resp
                if status == 416 and have:  # asked from past the end: what's there is all of it
                    break
                err = self._problem(status, data, fresh)
                if status == 401 and err is None:
                    fresh = True
                    continue
                if err is not None:
                    raise err
                if not waits:
                    raise DriveError(f"Google Drive is busy (it answered {status}). It's tried again later.")
                self.sleep(waits.pop(0))
                continue
            try:
                with resp, open(part, "ab" if resp.status == 206 else "wb") as out:
                    done = have if resp.status == 206 else 0
                    while True:
                        if cancelled and cancelled():
                            raise DriveError("The download was stopped; it carries on next time.", "cancelled")
                        piece = resp.read(CHUNK)
                        if not piece:
                            break
                        out.write(piece)
                        done += len(piece)
                        if progress and size:
                            progress(min(1.0, done / size))
            except DriveError:
                raise
            except OSError as exc:
                if getattr(exc, "winerror", None) == 112 or exc.errno == 28:
                    raise DriveError(f"There's no space left on the drive with {dest.parent}.", "disk") from exc
                if not waits:  # the connection dropped: carry on from where it got to
                    raise DriveError(f"The download kept being cut off ({exc}). It's tried again later.") from exc
                self.sleep(waits.pop(0))
                continue
            except http.client.HTTPException as exc:
                if not waits:
                    raise DriveError(f"The download kept being cut off ({exc}). It's tried again later.") from exc
                self.sleep(waits.pop(0))
                continue
            if not size or part.stat().st_size >= size:
                break
            if not waits:
                raise DriveError("The download kept being cut off. It's tried again later.")
            self.sleep(waits.pop(0))
        if size and part.stat().st_size != size:
            part.unlink(missing_ok=True)
            raise DriveError("The downloaded file wasn't the size Drive said. It's tried again later.")
        if md5 and md5_of(part) != md5:
            part.unlink(missing_ok=True)
            raise DriveError("it came down damaged (its checksum didn't match). It's tried again later.")
        os.replace(part, dest)


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def link(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"
