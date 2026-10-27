"""Updater tests against a local fake GitHub (no network)."""

import hashlib
import http.server
import json
import threading
import zipfile
from pathlib import Path

import pytest

from tokmanager import updater


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


class FakeGitHub:
    """Serves /repos/<repo>/releases/latest and the asset download, with ETag support."""

    def __init__(self):
        self.release: dict | None = None
        self.older: list[dict] = []  # earlier releases, for the /releases list
        self.files: dict[str, bytes] = {}
        self.requests: list[tuple[str, dict]] = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                fake.requests.append((self.path, dict(self.headers)))
                if self.path.endswith("/releases/latest"):
                    if fake.release is None:
                        return self._send(404, b'{"message": "Not Found"}')
                    body = json.dumps(fake.release).encode()
                    etag = '"' + hashlib.md5(body).hexdigest() + '"'
                    if self.headers.get("If-None-Match") == etag:
                        return self._send(304, b"")
                    return self._send(200, body, {"ETag": etag, "Content-Type": "application/json"})
                if self.path.split("?")[0].endswith("/releases"):
                    items = ([fake.release] if fake.release else []) + fake.older
                    return self._send(200, json.dumps(items).encode(), {"Content-Type": "application/json"})
                name = self.path.rsplit("/", 1)[-1]
                if name in fake.files:
                    return self._send(200, fake.files[name], {"Content-Type": "application/octet-stream"})
                self._send(404, b"")

            def _send(self, code, body, headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, version: str, zip_bytes: bytes, digest: bool = True, sidecar: bool = False):
        sha = hashlib.sha256(zip_bytes).hexdigest()
        self.files[updater.ASSET_NAME] = zip_bytes
        asset = {"name": updater.ASSET_NAME, "size": len(zip_bytes),
                 "browser_download_url": f"{self.url}/dl/{updater.ASSET_NAME}"}
        if digest:
            asset["digest"] = f"sha256:{sha}"
        assets = [asset]
        if sidecar:
            self.files[updater.ASSET_NAME + ".sha256"] = f"{sha}  {updater.ASSET_NAME}\n".encode()
            assets.append({"name": updater.ASSET_NAME + ".sha256", "size": 80,
                           "browser_download_url": f"{self.url}/dl/{updater.ASSET_NAME}.sha256"})
        self.release = {"tag_name": f"v{version}", "name": f"TikTok Manager {version}", "body": "- New stuff",
                        "published_at": "2026-10-21T00:00:00Z", "html_url": "https://example/rel", "assets": assets}


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(updater, "API_ROOT", fake.url)
    yield fake
    fake.server.shutdown()


def app_zip(tmp_path: Path, extra: dict[str, bytes] | None = None) -> bytes:
    path = tmp_path / "build.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("TikTokManager/TikTokManager.exe", b"MZ fake exe")
        z.writestr("TikTokManager/_internal/python314.dll", b"dll")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path.read_bytes()


def test_version_ordering():
    assert updater.is_newer("1.0.1", "1.0.0")
    assert updater.is_newer("v1.10.0", "1.9.9")
    assert updater.is_newer("1.1", "1.0.9")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("v1.0", "1.0.0")
    assert updater.is_newer("1.0.0", "1.0.0-beta")
    assert not updater.is_newer("1.0.0-beta", "1.0.0")
    assert not updater.is_newer("garbage", "1.0.0")


def test_check_finds_newer_release_and_uses_etag(gh, tmp_path):
    gh.publish("1.2.0", app_zip(tmp_path))
    rel = updater.check("iSlipperyX/TTUpdates", current="1.0.0")
    assert rel and rel.version == "1.2.0" and rel.sha256 and rel.notes == "- New stuff"
    again = updater.check("iSlipperyX/TTUpdates", current="1.0.0")
    assert again == rel
    latest = [h for path, h in gh.requests if path.endswith("/releases/latest")]
    assert latest[-1].get("If-None-Match")  # second check was a free 304
    assert updater.check("iSlipperyX/TTUpdates", current="1.2.0") is None


def test_check_with_no_releases_and_bad_repo(gh):
    assert updater.check("iSlipperyX/TTUpdates", current="1.0.0") is None
    with pytest.raises(updater.UpdateError):
        updater.check("not a repo", current="1.0.0")


def test_release_without_zip_is_an_error(gh, tmp_path):
    gh.publish("1.2.0", app_zip(tmp_path))
    gh.release["assets"] = []
    with pytest.raises(updater.UpdateError, match="no TikTokManager-windows.zip"):
        updater.check("o/r", current="1.0.0")


def test_download_verifies_checksum(gh, tmp_path):
    data = app_zip(tmp_path)
    gh.publish("1.2.0", data)
    rel = updater.check("o/r", current="1.0.0")
    path = updater.download(rel)
    assert path.read_bytes() == data
    # Tampered file on the server: rejected and not kept.
    gh.files[updater.ASSET_NAME] = data[:-1] + b"X"
    path.unlink()
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.download(rel)
    assert not path.exists()


def test_download_uses_sidecar_when_no_digest(gh, tmp_path):
    data = app_zip(tmp_path)
    gh.publish("1.2.0", data, digest=False, sidecar=True)
    rel = updater.check("o/r", current="1.0.0")
    assert rel.sha256 == "" and rel.sha256_url
    assert updater.download(rel).read_bytes() == data


def test_release_without_any_checksum_is_refused(gh, tmp_path):
    gh.publish("1.2.0", app_zip(tmp_path), digest=False, sidecar=False)
    rel = updater.check("o/r", current="1.0.0")
    with pytest.raises(updater.UpdateError, match="no checksum"):
        updater.download(rel)


def test_stage_extracts_app_folder(tmp_path):
    zp = tmp_path / "u.zip"
    zp.write_bytes(app_zip(tmp_path))
    target = tmp_path / "Apps" / "TikTokManager"
    target.mkdir(parents=True)
    new_app = updater.stage(zp, "1.2.0", target)
    assert (new_app / "TikTokManager.exe").read_bytes() == b"MZ fake exe"
    assert (new_app / "_internal").is_dir()


def test_stage_rejects_unsafe_or_wrong_zips(tmp_path):
    target = tmp_path / "TikTokManager"
    target.mkdir()
    evil = tmp_path / "evil.zip"
    evil.write_bytes(app_zip(tmp_path, {"../../outside.txt": b"x"}))
    with pytest.raises(updater.UpdateError, match="unsafe"):
        updater.stage(evil, "1.2.0", target)
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", b"hi")
    with pytest.raises(updater.UpdateError, match="doesn't contain"):
        updater.stage(empty, "1.2.0", target)
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(updater.UpdateError, match="damaged"):
        updater.stage(junk, "1.2.0", target)


def test_helper_script_is_ascii():
    updater.HELPER_PS1.encode("ascii")  # Windows PowerShell 5.1 misreads BOM-less UTF-8


def test_helper_gets_a_hidden_console_not_a_detached_process(tmp_path, monkeypatch):
    """Windows PowerShell 5.1 started with DETACHED_PROCESS exits at once without running the
    script: up to 1.2.0 that made every in-app update close the app and install nothing."""
    import subprocess

    for name, value in (("DETACHED_PROCESS", 0x8), ("CREATE_NEW_PROCESS_GROUP", 0x200),
                        ("CREATE_BREAKAWAY_FROM_JOB", 0x1000000), ("CREATE_NO_WINDOW", 0x8000000)):
        monkeypatch.setattr(subprocess, name, value, raising=False)
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path)
    calls = []

    def fake_popen(args, creationflags=0, **kw):
        calls.append(creationflags)
        if creationflags & 0x1000000:
            raise OSError("breakaway not allowed")

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    updater.launch_helper(tmp_path / "new", tmp_path / "app", "9.9.9", tray=False)
    assert calls == [0x8000000 | 0x200 | 0x1000000, 0x8000000 | 0x200]
    assert not any(flags & 0x8 for flags in calls)
    assert 'try { Start-App @("--updated-from"' in (tmp_path / "apply-update.ps1").read_text(encoding="ascii")


def test_notes_cover_every_version_since_yours(gh, tmp_path):
    gh.publish("1.3.0", app_zip(tmp_path))
    gh.older = [{"tag_name": f"v{v}", "body": f"- notes for {v}"} for v in ("1.2.1", "1.2.0", "1.1.0")]
    gh.older.append({"tag_name": "v1.4.0-beta", "body": "- beta", "prerelease": True})
    rel = updater.check("o/r", current="1.2.0")
    assert rel.notes.index("# 1.3.0") < rel.notes.index("# 1.2.1")
    assert "- notes for 1.2.1" in rel.notes
    assert "notes for 1.2.0" not in rel.notes and "notes for 1.1.0" not in rel.notes and "beta" not in rel.notes
    # Just one version behind: only that release's own notes.
    assert updater.check("o/r", current="1.2.1").notes == "- New stuff"


def test_the_last_check_result_is_kept_for_the_updates_tab(gh, tmp_path):
    updater.check("o/r", current="1.0.0")
    assert updater.load_state()["result"]["text"] == "No releases have been published yet."
    gh.publish("1.2.0", app_zip(tmp_path))
    updater.check("o/r", current="1.2.0")
    assert updater.load_state()["result"] | {"at": 0} == {"at": 0, "kind": "ok", "text": "Up to date (1.2.0)."}
    updater.check("o/r", current="1.0.0")
    assert updater.load_state()["result"]["kind"] == "available"
    with pytest.raises(updater.UpdateError):
        updater.check("not a repo")
    assert updater.load_state()["result"]["kind"] == "error"
