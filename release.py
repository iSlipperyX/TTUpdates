"""Publish a new version of TikTok Manager to the update repository on GitHub.

    python release.py 1.1.0 --notes "Faster scoring, fixes a crash when ..."
    python release.py 1.1.0 --notes-file notes.md

It sets VERSION in tokmanager/config.py, runs the tests, builds the app, then creates the
GitHub release v1.1.0 with TikTokManager-windows.zip (+ its .sha256). Every installed copy
picks the update up on its next check (at start-up and every 6 hours).

Needs a GitHub token that can write to the repo, read from the GITHUB_TOKEN environment variable
(or typed when asked). Create one at github.com/settings/personal-access-tokens: a fine-grained
token for the update repo with "Contents: Read and write".
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "tokmanager" / "config.py"
DIST = ROOT / "dist"
ZIP_NAME = "TikTokManager-windows.zip"
API = "https://api.github.com"


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def current_version() -> str:
    return re.search(r'^VERSION = "([^"]+)"', CONFIG.read_text(encoding="utf-8"), re.M)[1]


def default_repo() -> str:
    return re.search(r'update_repo: str = "([^"]+)"', CONFIG.read_text(encoding="utf-8"))[1]


def set_version(version: str) -> None:
    text = CONFIG.read_text(encoding="utf-8")
    CONFIG.write_text(re.sub(r'^VERSION = "[^"]+"', f'VERSION = "{version}"', text, count=1, flags=re.M),
                      encoding="utf-8")


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "TikTokManager-release"})

    def call(self, method: str, path: str, ok=(200, 201), **kw) -> requests.Response:
        url = path if path.startswith("http") else f"{API}/repos/{self.repo}{path}"
        r = self.s.request(method, url, timeout=kw.pop("timeout", 60), **kw)
        if r.status_code not in ok:
            msg = r.json().get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
            raise SystemExit(f"GitHub {method} {path} failed: HTTP {r.status_code}: {msg}")
        return r

    def check_access(self) -> str:
        info = self.call("GET", "").json()
        if not (info.get("permissions") or {}).get("push"):
            raise SystemExit(f"That token can't publish to {self.repo}. It needs 'Contents: Read and write'.")
        return info.get("default_branch") or "main"

    def ensure_not_empty(self, branch: str) -> None:
        """GitHub can't tag a repository with no commits, so give a brand-new repo a README first."""
        r = self.call("GET", "/commits?per_page=1", ok=(200, 409))
        if r.status_code == 409:
            readme = ("# TikTok Manager updates\n\nReleases published here are downloaded and installed "
                      "automatically by TikTok Manager.\n")
            self.call("PUT", "/contents/README.md", json={
                "message": "Set up the update channel", "branch": branch,
                "content": base64.b64encode(readme.encode()).decode()})
            print("The repository was empty: added a README so releases can be tagged.")

    def latest_version(self) -> str | None:
        r = self.call("GET", "/releases/latest", ok=(200, 404))
        return r.json().get("tag_name", "").lstrip("vV") if r.status_code == 200 else None

    def tag_exists(self, tag: str) -> bool:
        return self.call("GET", f"/releases/tags/{tag}", ok=(200, 404)).status_code == 200

    def publish(self, tag: str, branch: str, title: str, notes: str, files: list[Path]) -> str:
        # Create a draft, attach the files, then publish: users never see a release without its zip.
        rel = self.call("POST", "/releases", json={"tag_name": tag, "target_commitish": branch, "name": title,
                                                   "body": notes, "draft": True}).json()
        try:
            upload = rel["upload_url"].split("{", 1)[0]
            for f in files:
                print(f"Uploading {f.name} ({f.stat().st_size / 1e6:.0f} MB)…")
                with f.open("rb") as fh:
                    self.call("POST", upload, params={"name": f.name}, data=fh, timeout=1800, headers={
                        "Content-Type": "application/zip" if f.suffix == ".zip" else "text/plain",
                        "Content-Length": str(f.stat().st_size)})
            done = self.call("PATCH", f"/releases/{rel['id']}", json={"draft": False, "make_latest": "true"}).json()
            return done.get("html_url", "")
        except BaseException:
            self.call("DELETE", f"/releases/{rel['id']}", ok=(204, 404))
            raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="new version, e.g. 1.1.0")
    notes = ap.add_mutually_exclusive_group()
    notes.add_argument("--notes", default="", help="release notes (Markdown), shown in the app's 'What's new'")
    notes.add_argument("--notes-file", type=Path, help="read the release notes from a file")
    ap.add_argument("--repo", default=default_repo(), help="owner/name of the update repository")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-build", action="store_true", help="publish the existing dist/ build as-is")
    args = ap.parse_args()

    version = args.version.lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("Use a version like 1.2.0")
    tag = f"v{version}"
    body = args.notes_file.read_text(encoding="utf-8") if args.notes_file else (args.notes or "Bug fixes and improvements.")

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or getpass.getpass(
        f"GitHub token for {args.repo} (input hidden): ").strip()
    gh = GitHub(args.repo, token)
    branch = gh.check_access()
    latest = gh.latest_version()
    if latest and parse(version) <= parse(latest):
        raise SystemExit(f"{version} isn't newer than the published {latest}; installed apps would ignore it.")
    if gh.tag_exists(tag):
        raise SystemExit(f"A release tagged {tag} already exists.")
    if parse(version) < parse(current_version()):
        raise SystemExit(f"{version} is older than the code's current version {current_version()}.")

    if current_version() != version:
        set_version(version)
        print(f"Version set to {version} in tokmanager/config.py")
    if not args.skip_tests:
        subprocess.run([sys.executable, "-m", "pytest", "-q"], check=True, cwd=ROOT)
    if not args.skip_build:
        subprocess.run([sys.executable, "build.py"], check=True, cwd=ROOT)
    files = [DIST / ZIP_NAME, DIST / (ZIP_NAME + ".sha256")]
    for f in files:
        if not f.is_file():
            raise SystemExit(f"Missing {f}; run without --skip-build.")

    gh.ensure_not_empty(branch)
    url = gh.publish(tag, branch, f"TikTok Manager {version}", body, files)
    print(f"\nPublished {tag}: {url}\nInstalled copies will offer the update on their next check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
