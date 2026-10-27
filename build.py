"""Build the Windows app: dist/TikTokManager/TikTokManager.exe, a .zip of it, the zip's SHA-256
and, when Inno Setup 6 is installed, the installer dist/TikTokManager-Setup.exe.

    pip install -e .[build]
    python build.py               (--installer: fail if Inno Setup isn't there)

Releases are built by GitHub Actions (.github/workflows/release.yml) when release.py pushes a tag.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
ZIP_NAME = "TikTokManager-windows.zip"  # the updater looks for exactly this asset name
SETUP_NAME = "TikTokManager-Setup.exe"


def app_version() -> str:
    text = (ROOT / "tokmanager" / "config.py").read_text(encoding="utf-8")
    return re.search(r'^VERSION = "([^"]+)"', text, re.M)[1]


def write_version_info(version: str) -> Path:
    """Windows file properties (Explorer > Properties > Details) for the exe."""
    nums = [int(x) for x in re.findall(r"\d+", version)[:4]]
    nums += [0] * (4 - len(nums))
    path = PACKAGING / "version_info.txt"
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'TikTok Manager'),
      StringStruct('FileDescription', 'TikTok Manager'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'TikTokManager'),
      StringStruct('OriginalFilename', 'TikTokManager.exe'),
      StringStruct('ProductName', 'TikTok Manager'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def find_iscc() -> Path | None:
    """Inno Setup's compiler, if it's installed."""
    found = shutil.which("iscc")
    if found:
        return Path(found)
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        for sub in (("Inno Setup 6",), ("Programs", "Inno Setup 6")):
            if base and (path := Path(base).joinpath(*sub, "ISCC.exe")).is_file():
                return path
    return None


def build_installer(version: str, app_dir: Path) -> Path:
    iscc = find_iscc()
    if iscc is None:
        raise FileNotFoundError("Inno Setup 6 isn't installed (winget install JRSoftware.InnoSetup)")
    subprocess.run([str(iscc), "/Q", f"/DAppVersion={version}", f"/DSourceDir={app_dir}", f"/DOutputDir={DIST}",
                    str(PACKAGING / "TikTokManager.iss")], check=True, cwd=PACKAGING)
    return DIST / SETUP_NAME


def main() -> int:
    need_installer = "--installer" in sys.argv[1:]
    version = app_version()
    write_version_info(version)
    subprocess.run([sys.executable, str(PACKAGING / "make_icon.py"), str(PACKAGING / "tiktokmanager.ico")], check=True)
    # Build scratch goes to %TEMP% so OneDrive doesn't sync thousands of temp files.
    work = Path(tempfile.gettempdir()) / "tiktokmanager-build"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(work), str(PACKAGING / "TikTokManager.spec")],
                   check=True, cwd=ROOT)
    app_dir = DIST / "TikTokManager"
    archive = Path(shutil.make_archive(str(DIST / ZIP_NAME.removesuffix(".zip")), "zip", DIST, "TikTokManager"))
    digest = sha256(archive)
    (DIST / (ZIP_NAME + ".sha256")).write_text(f"{digest}  {ZIP_NAME}\n", encoding="ascii")
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1e6
    print(f"\nBuilt TikTok Manager {version}: {app_dir / 'TikTokManager.exe'} ({size:.0f} MB folder)")
    print(f"Zip: {archive} ({archive.stat().st_size / 1e6:.0f} MB)\nSHA-256: {digest}")
    try:
        setup = build_installer(version, app_dir)
        print(f"Installer: {setup} ({setup.stat().st_size / 1e6:.0f} MB)")
    except FileNotFoundError as exc:
        if need_installer:
            raise SystemExit(f"No installer: {exc}") from exc
        print(f"No installer built: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
