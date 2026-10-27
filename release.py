"""Publish a new version of TikTok Manager to the update repository on GitHub.

    python release.py 1.1.0                      (notes from release-notes/v1.1.0.md)
    python release.py 1.1.0 --notes "Faster scoring, fixes a crash when ..."
    python release.py 1.1.0 --notes-file notes.md

It checks that this clone matches the repository's main branch on GitHub, sets VERSION in
tokmanager/config.py, runs the tests, builds the app, then creates the GitHub release v1.1.0,
tagged on the commit you built, with TikTokManager-windows.zip (+ its .sha256). Every installed
copy picks the update up on its next check (at start-up and every 6 hours).

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
NOTES_DIR = ROOT / "release-notes"
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


def git(*args: str) -> str | None:
    """Output of a git command in this clone, or None when git or the clone isn't there."""
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.rstrip() if r.returncode == 0 else None  # keep leading spaces: status lines start with one


def local_commit() -> tuple[str | None, list[str]]:
    """(HEAD commit, files with uncommitted changes) of this clone; (None, []) when it isn't a git clone.

    tokmanager/config.py is left out: release.py bumps VERSION there itself.
    """
    head = git("rev-parse", "HEAD")
    if head is None:
        return None, []
    status = git("status", "--porcelain", "--untracked-files=no") or ""
    changed = [line[3:] for line in status.splitlines() if line[3:] != "tokmanager/config.py"]
    return head, changed


def default_notes(version: str) -> Path | None:
    path = NOTES_DIR / f"v{version}.md"
    return path if path.is_file() else None


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

    def branch_head(self, branch: str) -> str | None:
        r = self.call("GET", f"/branches/{branch}", ok=(200, 404))
        return r.json()["commit"]["sha"] if r.status_code == 200 else None

    def latest_version(self) -> str | None:
        r = self.call("GET", "/releases/latest", ok=(200, 404))
        return r.json().get("tag_name", "").lstrip("vV") if r.status_code == 200 else None

    def tag_exists(self, tag: str) -> bool:
        return self.call("GET", f"/releases/tags/{tag}", ok=(200, 404)).status_code == 200

    def create_draft(self, tag: str, target: str, title: str, notes: str) -> dict:
        """A draft release, made before the build so a token that can't publish fails in seconds.

        Drafts are invisible to installed apps, and no tag exists until it's published.
        """
        r = self.call("POST", "/releases", ok=(201, 403, 404), json={
            "tag_name": tag, "target_commitish": target, "name": title, "body": notes, "draft": True})
        if r.status_code != 201:
            raise SystemExit(f"That token can read {self.repo} but isn't allowed to create releases there.\n"
                             "At github.com/settings/personal-access-tokens, give it access to this repository "
                             "with 'Contents: Read and write', then try again.")
        return r.json()

    def delete_draft(self, rel: dict) -> None:
        self.call("DELETE", f"/releases/{rel['id']}", ok=(204, 404))

    def publish(self, rel: dict, files: list[Path]) -> str:
        """Attach the files to the draft, then publish it: users never see a release without its zip."""
        upload = rel["upload_url"].split("{", 1)[0]
        for f in files:
            print(f"Uploading {f.name} ({f.stat().st_size / 1e6:.0f} MB)…")
            with f.open("rb") as fh:
                self.call("POST", upload, params={"name": f.name}, data=fh, timeout=1800, headers={
                    "Content-Type": "application/zip" if f.suffix == ".zip" else "text/plain",
                    "Content-Length": str(f.stat().st_size)})
        done = self.call("PATCH", f"/releases/{rel['id']}", json={"draft": False, "make_latest": "true"}).json()
        return done.get("html_url", "")

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
    notes_file = args.notes_file or (None if args.notes else default_notes(version))
    if notes_file:
        body = notes_file.read_text(encoding="utf-8").strip()
        print(f"Release notes: {notes_file}")
    else:
        body = args.notes or "Bug fixes and improvements."

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

    # Build exactly what is on GitHub, and tag that commit, so the release matches the code.
    head, changed = local_commit()
    target = branch
    if head is None:
        print("Warning: this folder isn't a git clone, so the tag goes on the latest commit on GitHub.")
    else:
        if changed:
            raise SystemExit("This clone has changes that aren't committed: " + ", ".join(changed[:5])
                             + ("…" if len(changed) > 5 else "") + "\nCommit or undo them first.")
        remote = gh.branch_head(branch)
        if remote and remote != head:
            raise SystemExit(f"This clone isn't at the latest {branch} on GitHub ({remote[:7]}); you're on "
                             f"{head[:7]}.\nRun 'git checkout {branch}' and 'git pull' (or 'git push' if the "
                             "newer commit is yours), then try again.")
        target = head

    gh.ensure_not_empty(branch)
    draft = gh.create_draft(tag, target, f"TikTok Manager {version}", body)
    try:
        bumped = current_version() != version
        if bumped:
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
        url = gh.publish(draft, files)
    except BaseException:
        gh.delete_draft(draft)  # a failed test, build or upload leaves nothing behind on GitHub
        raise
    print(f"\nPublished {tag}: {url}\nInstalled copies will offer the update on their next check.")
    if bumped and head is not None:
        print(f"\nThe version bump isn't on GitHub yet. Commit and push it:\n"
              f"    git commit -am \"Release {version}\"\n    git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
