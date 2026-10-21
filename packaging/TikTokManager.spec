# PyInstaller spec: builds dist/TikTokManager/ with TikTokManager.exe.
# Build with:  python build.py
import os

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(SPECPATH, "tiktokmanager.ico")
VERSION_INFO = os.path.join(SPECPATH, "version_info.txt")  # written by build.py from tokmanager/config.py

datas = (
    collect_data_files("imageio_ffmpeg", includes=["binaries/*"])  # bundled ffmpeg.exe
    + collect_data_files("playwright")  # Playwright's Node driver; the browser is your installed Edge/Chrome
)
hiddenimports = ["anthropic", "playwright.sync_api", "PySide6.QtNetwork"]
excludes = [
    "tkinter", "matplotlib", "IPython", "pytest", "PIL", "scipy", "pandas", "torch", "numpy",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtBluetooth", "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtMultimedia",
]

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
# Software OpenGL fallback and Qt's UI translations aren't used (the app is English-only).
a.binaries = [b for b in a.binaries if "opengl32sw" not in b[0].lower()]
a.datas = [d for d in a.datas if "translations" not in d[0].replace("\\", "/").split("/")[:2]]
exe = EXE(
    PYZ(a.pure), a.scripts, [],
    exclude_binaries=True, name="TikTokManager", icon=ICON, console=False, upx=False,
    version=VERSION_INFO if os.path.exists(VERSION_INFO) else None,
)
COLLECT(exe, a.binaries, a.datas, name="TikTokManager", upx=False)
