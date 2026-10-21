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
    assert gh.requests[-1][1].get("If-None-Match")  # second check was a free 304
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
