"""Build the Windows app: dist/TikTokManager/TikTokManager.exe, a .zip of it, and the zip's SHA-256.

    pip install -e .[build]
    python build.py

To publish the build as an update, use release.py instead (it runs this).
"""

import hashlib
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


def main() -> int:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
