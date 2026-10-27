"""Self-update from GitHub Releases.

Each release on the update repo (Settings -> Updates, default iSlipperyX/TTUpdates) is tagged
like v1.2.0 and carries TikTokManager-windows.zip (made by build.py / release.py).

Installing never touches your data (%APPDATA% / %LOCALAPPDATA%): only the app folder is
replaced. The swap is done by a small PowerShell helper after the app exits, so files in use
aren't a problem; it keeps a backup and rolls back if the new version doesn't start.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import requests

from .config import VERSION, app_dir, updates_dir

API_ROOT = os.environ.get("TOKMANAGER_UPDATE_API", "https://api.github.com")  # overridable for testing
ASSET_NAME = "TikTokManager-windows.zip"
EXE_NAME = "TikTokManager.exe"
MARKER_TIMEOUT = int(os.environ.get("TOKMANAGER_UPDATE_MARKER_TIMEOUT", "90"))
USER_AGENT = f"TikTokManager/{VERSION} (+updater)"
MARKER_NAME = "update-started.ok"  # the helper deletes it before starting the new version


class UpdateError(RuntimeError):
    pass


@dataclass
class Release:
    version: str
    tag: str
    name: str
    notes: str
    published_at: str
    html_url: str
    asset_url: str
    asset_size: int
    sha256: str  # "" when GitHub didn't report a digest; then the .sha256 sidecar asset is used
    sha256_url: str


# --- versions -----------------------------------------------------------------------


def parse_version(text: str) -> tuple:
    """'v1.2' -> (1, 2, 0, 1); a pre-release suffix ('1.2.0-beta') sorts before the release."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)(.*)$", text or "")
    if not m:
        return ()
    nums = tuple(int(x) for x in m[1].split("."))[:4]
    nums += (0,) * (3 - len(nums))
    return nums + ((0,) if m[2].strip(" -._+") else (1,))


def is_newer(candidate: str, current: str = VERSION) -> bool:
    c = parse_version(candidate)
    return bool(c) and c > parse_version(current)


# --- small persisted state (ETag, last check, notes to show after an update) -------------


def _state_path() -> Path:
    return app_dir() / "update_state.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _state_path())


# --- checking -----------------------------------------------------------------------


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _release_from_json(data: dict) -> Release:
    assets = data.get("assets") or []
    asset = next((a for a in assets if a.get("name") == ASSET_NAME), None)
    if asset is None:
        asset = next((a for a in assets if a.get("name", "").lower().endswith(".zip")
                      and "windows" in a.get("name", "").lower()), None)
    if asset is None:
        raise UpdateError(f"Release {data.get('tag_name')} has no {ASSET_NAME} attached.")
    digest = asset.get("digest") or ""
    sha = digest.split(":", 1)[1].lower() if digest.lower().startswith("sha256:") else ""
    sidecar = next((a for a in assets if a.get("name") == asset["name"] + ".sha256"), None)
    tag = data.get("tag_name") or ""
    return Release(
        version=tag.lstrip("vV"), tag=tag, name=data.get("name") or tag, notes=data.get("body") or "",
        published_at=data.get("published_at") or "", html_url=data.get("html_url") or "",
        asset_url=asset.get("browser_download_url") or "", asset_size=int(asset.get("size") or 0),
        sha256=sha, sha256_url=(sidecar or {}).get("browser_download_url", ""),
    )


def check(repo: str, current: str = VERSION) -> Release | None:
    """The latest release if it's newer than `current`, else None. Uses an ETag so repeat checks are free.

    The outcome, good or bad, is kept as state["result"] for the Updates tab. A newer release's
    notes cover every version since `current`, not only the latest one.
    """
    try:
        release = _check(repo, current)
    except UpdateError as exc:
        _record("error", str(exc))
        raise
    if release is None:
        latest = load_state().get("latest")
        _record("ok", f"Up to date ({current})." if latest else "No releases have been published yet.")
        return None
    release.notes = notes_since(repo, current, release)
    _record("available", f"{release.version} is available.")
    return release


def _record(kind: str, text: str) -> None:
    state = load_state()
    state["result"] = {"at": time.time(), "kind": kind, "text": text}
    save_state(state)


def notes_since(repo: str, current: str, latest: Release) -> str:
    """Release notes for every version after `current` up to `latest`, newest first.

    Falls back to the latest release's own notes if the list can't be read.
    """
    try:
        r = requests.get(f"{API_ROOT}/repos/{repo}/releases", params={"per_page": 30}, headers=_headers(), timeout=20)
        items = r.json() if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        items = []
    parts = []
    for data in items if isinstance(items, list) else []:
        if data.get("draft") or data.get("prerelease"):
            continue
        version = (data.get("tag_name") or "").lstrip("vV")
        if is_newer(version, current) and not is_newer(version, latest.version):
            parts.append((parse_version(version), version, (data.get("body") or "").strip()))
    if len(parts) <= 1:
        return latest.notes
    parts.sort(reverse=True)
    return "\n\n".join(f"# {version}\n\n{body or 'No notes.'}" for _, version, body in parts)


def _check(repo: str, current: str) -> Release | None:
    repo = (repo or "").strip().strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise UpdateError(f"'{repo}' isn't a GitHub repository like owner/name.")
    state = load_state()
    headers = _headers()
    cached = state.get("latest") if state.get("repo") == repo else None
    if cached and state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    try:
        r = requests.get(f"{API_ROOT}/repos/{repo}/releases/latest", headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise UpdateError(f"Couldn't reach GitHub: {exc.__class__.__name__}") from exc
    state["last_check"] = time.time()
    if r.status_code == 304 and cached:
        release = Release(**cached)
    elif r.status_code == 404:
        state.update(repo=repo, etag="", latest=None)
        save_state(state)
        return None  # no published releases yet (or the repo is private/renamed)
    elif r.status_code in (403, 429):
        save_state(state)
        raise UpdateError("GitHub is rate-limiting update checks; will try again later.")
    elif r.status_code != 200:
        save_state(state)
        raise UpdateError(f"GitHub returned HTTP {r.status_code}.")
    else:
        release = _release_from_json(r.json())
        state.update(repo=repo, etag=r.headers.get("ETag", ""), latest=asdict(release))
    save_state(state)
    return release if is_newer(release.version, current) else None


# --- downloading --------------------------------------------------------------------


def _expected_sha(release: Release) -> str:
    if release.sha256:
        return release.sha256
    if release.sha256_url:
        try:
            r = requests.get(release.sha256_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
            m = re.search(r"\b[0-9a-fA-F]{64}\b", r.text)
            if m:
                return m[0].lower()
        except requests.RequestException as exc:
            raise UpdateError(f"Couldn't fetch the update's checksum: {exc.__class__.__name__}") from exc
    raise UpdateError("This release has no checksum, so it can't be verified. Not installing it.")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(release: Release, progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Download and verify the release zip (re-uses an already verified download)."""
    expected = _expected_sha(release)
    folder = updates_dir() / release.version
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / ASSET_NAME
    if final.exists() and _sha256(final) == expected:
        return final
    part = final.with_suffix(".part")
    try:
        with requests.get(release.asset_url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=60) as r:
            if r.status_code != 200:
                raise UpdateError(f"Download failed (HTTP {r.status_code}).")
            total = int(r.headers.get("Content-Length") or release.asset_size or 0)
            done = 0
            with part.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if cancelled():
                        raise UpdateError("Cancelled")
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except requests.RequestException as exc:
        raise UpdateError(f"Download failed: {exc.__class__.__name__}") from exc
    if release.asset_size and part.stat().st_size != release.asset_size:
        part.unlink(missing_ok=True)
        raise UpdateError("The download was incomplete. Try again.")
    if _sha256(part) != expected:
        part.unlink(missing_ok=True)
        raise UpdateError("The downloaded update didn't match its checksum, so it was deleted.")
    os.replace(part, final)
    return final


# --- installing ---------------------------------------------------------------------


def install_dir() -> Path | None:
    """The folder of the running TikTokManager.exe, or None when running from source."""
    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent
    return folder if (folder / EXE_NAME).is_file() else None


def _writable(folder: Path) -> bool:
    try:
        probe = folder / f".write-test-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _same_drive(a: Path, b: Path) -> bool:
    return os.path.splitdrive(str(a.resolve()))[0].lower() == os.path.splitdrive(str(b.resolve()))[0].lower()


def stage(zip_path: Path, version: str, target: Path) -> Path:
    """Unpack the zip next to where it will be installed; returns the new app folder."""
    # Same drive as the install folder, so the final swap is an instant rename.
    base = updates_dir() if _same_drive(updates_dir(), target) else target.parent
    root = base / f".tiktokmanager-staged-{version}"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    root_resolved = root.resolve()
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                dest = (root / name).resolve()
                if dest != root_resolved and root_resolved not in dest.parents:
                    raise UpdateError(f"The update contains an unsafe path ({name}).")
            z.extractall(root)
    except zipfile.BadZipFile as exc:
        raise UpdateError("The update file is damaged.") from exc
    exes = sorted(root.rglob(EXE_NAME), key=lambda p: len(p.parts))
    if not exes or not (exes[0].parent / "_internal").is_dir():
        raise UpdateError(f"The update doesn't contain {EXE_NAME}.")
    return exes[0].parent


def preflight(target: Path) -> None:
    if not _writable(target) or not _writable(target.parent):
        raise UpdateError(f"TikTok Manager can't write to {target}. Move the TikTokManager folder somewhere you "
                          "own (e.g. Documents) to enable updates.")


def launch_helper(new_app: Path, target: Path, new_version: str, tray: bool) -> None:
    """Start the detached helper that swaps folders once this process has exited."""
    marker = updates_dir() / MARKER_NAME
    marker.unlink(missing_ok=True)
    script = updates_dir() / "apply-update.ps1"
    script.write_text(HELPER_PS1, encoding="ascii")
    args = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", str(script),
        "-AppPid", str(os.getpid()), "-Source", str(new_app), "-Target", str(target), "-ExeName", EXE_NAME,
        "-Marker", str(marker), "-LogFile", str(updates_dir() / "update.log"),
        "-OldVersion", VERSION, "-NewVersion", new_version, "-Mode", "tray" if tray else "normal",
        "-MarkerTimeout", str(MARKER_TIMEOUT),
    ]
    # Never DETACHED_PROCESS: Windows PowerShell 5.1 started without a console quits at once
    # (exit code 0) without running the script. That's why updates up to 1.2.0 closed the app
    # and installed nothing. CREATE_NO_WINDOW gives it a hidden console instead.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        subprocess.Popen(args, creationflags=flags | breakaway, **kwargs)
    except OSError:  # the parent job doesn't allow breakaway
        subprocess.Popen(args, creationflags=flags, **kwargs)


def mark_started() -> None:
    """Tell the helper the new version came up (it rolls back if this never happens)."""
    try:
        (updates_dir() / MARKER_NAME).write_text(f"{VERSION} {os.getpid()}", encoding="ascii")
    except OSError:
        pass


UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{70C03803-B73F-4A78-AF2E-A0653E15CF49}_is1"


def sync_installed_version() -> None:
    """Keep the version in Windows' list of installed apps right after an in-app update.

    Only for a copy installed with TikTokManager-Setup.exe (whose AppId is in UNINSTALL_KEY);
    a copy unzipped by hand has no entry there.
    """
    target = install_dir()
    if os.name != "nt" or target is None:
        return
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            location = winreg.QueryValueEx(k, "InstallLocation")[0]
            if Path(location).resolve() != target.resolve():
                return  # that entry is for a different copy
            if winreg.QueryValueEx(k, "DisplayVersion")[0] != VERSION:
                winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
    except OSError:
        pass


def cleanup() -> None:
    """Remove downloads and staging folders left from earlier updates."""
    root = updates_dir()
    for p in root.iterdir():
        try:
            if p.is_dir() and (p.name.startswith(".tiktokmanager-staged-") or not is_newer(p.name)):
                shutil.rmtree(p, ignore_errors=True)
            elif p.name == MARKER_NAME and time.time() - p.stat().st_mtime > 600:
                p.unlink()
        except OSError:
            pass
    target = install_dir()
    if target:
        for leftover in (target.parent / f"{target.name}.old",
                         *target.parent.glob(".tiktokmanager-staged-*")):
            shutil.rmtree(leftover, ignore_errors=True)


def log_tail(lines: int = 15) -> str:
    try:
        return "\n".join((updates_dir() / "update.log").read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


# ASCII only: Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.
HELPER_PS1 = r"""
param(
    [int]$AppPid, [string]$Source, [string]$Target, [string]$ExeName, [string]$Marker, [string]$LogFile,
    [string]$OldVersion, [string]$NewVersion, [string]$Mode = "normal", [int]$MarkerTimeout = 90
)
$ErrorActionPreference = "Stop"

function Log([string]$m) {
    try { Add-Content -LiteralPath $LogFile -Value ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 } catch {}
}
function Start-App([string[]]$extra) {
    $a = @()
    if ($Mode -eq "tray") { $a += "--tray" }
    if ($extra) { $a += $extra }
    $exe = Join-Path $Target $ExeName
    if ($a.Count -gt 0) { Start-Process -FilePath $exe -ArgumentList $a -WorkingDirectory $Target }
    else { Start-Process -FilePath $exe -WorkingDirectory $Target }
}
function Copy-Tree([string]$from, [string]$to) {
    & robocopy $from $to /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    return ($LASTEXITCODE -lt 8)
}
function Restore([string]$how) {
    if ($how -eq "rename") {
        if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force -ErrorAction SilentlyContinue }
        Rename-Item -LiteralPath $Backup -NewName (Split-Path $Target -Leaf)
    } else {
        Copy-Tree $Backup $Target | Out-Null
        Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Log "Updating $OldVersion -> $NewVersion in $Target"

# 1. Wait for the running app to exit (it saves everything first).
try { Wait-Process -Id $AppPid -Timeout 180 -ErrorAction Stop } catch {}
if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) { Log "The app didn't exit; update cancelled."; exit 1 }
Start-Sleep -Milliseconds 800

# 2. Swap the folders, keeping the old one as a backup.
$Backup = "$Target.old"
if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue }
$method = ""
for ($i = 0; $i -lt 15 -and $method -eq ""; $i++) {
    try { Rename-Item -LiteralPath $Target -NewName (Split-Path $Backup -Leaf); $method = "rename" }
    catch { Start-Sleep -Seconds 1 }
}
try {
    if ($method -eq "rename") {
        try { Move-Item -LiteralPath $Source -Destination $Target }
        catch {
            Log "Move failed ($($_.Exception.Message)); copying instead."
            if (-not (Copy-Tree $Source $Target)) { throw "copy failed" }
        }
    } else {
        Log "The app folder is in use; updating the files in place."
        if (-not (Copy-Tree $Target $Backup)) { Log "Couldn't back up the app folder; update cancelled."; Start-App @("--update-failed", $NewVersion); exit 1 }
        $method = "copy"
        if (-not (Copy-Tree $Source $Target)) { throw "copy failed" }
    }
} catch {
    Log "Install failed ($($_.Exception.Message)); restoring $OldVersion."
    Restore $method
    Start-App @("--update-failed", $NewVersion)
    exit 1
}

# 3. Start the new version and wait for it to report that it came up.
if (Test-Path -LiteralPath $Marker) { Remove-Item -LiteralPath $Marker -Force }
try { Start-App @("--updated-from", $OldVersion) }
catch { Log "Couldn't start $NewVersion ($($_.Exception.Message))." }
$deadline = (Get-Date).AddSeconds($MarkerTimeout)
while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $Marker)) { Start-Sleep -Milliseconds 500 }
if (Test-Path -LiteralPath $Marker) {
    Log "Version $NewVersion started. Cleaning up."
    Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Source) { Remove-Item -LiteralPath $Source -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}

# 4. It never came up: roll back.
Log "Version $NewVersion didn't start within $MarkerTimeout s; rolling back to $OldVersion."
$exePath = Join-Path $Target $ExeName
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exePath } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
try { Restore $method } catch { Log "Rollback problem: $($_.Exception.Message)" }
Start-App @("--update-failed", $NewVersion)
Log "Rolled back to $OldVersion."
exit 1
"""
