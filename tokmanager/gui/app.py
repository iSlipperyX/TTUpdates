"""Launch the TikTok Manager desktop app.

    TikTokManager.exe              open the window
    TikTokManager.exe --tray       start hidden in the system tray (used by "Start with Windows")
    TikTokManager.exe FILES...     open and add these videos to the library
    TikTokManager.exe --selftest   check ffmpeg + browser automation, write selftest.txt, exit
    TikTokManager.exe --update     check GitHub for a new version and install it without asking

The update helper also starts new versions with --updated-from OLD (or the old one with
--update-failed NEW after a rollback).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading

SERVER_NAME = "TikTokManager-single-instance"


def _prepare_process() -> None:
    from ..config import app_dir

    # A windowed .exe has no console: libraries that print would crash writing to None.
    if sys.stdout is None or sys.stderr is None:
        log = open(app_dir() / "tokmanager.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    # Child processes (ffmpeg, the Playwright driver) would each flash a console window.
    if os.name == "nt":
        original = subprocess.Popen.__init__

        def no_window(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            original(self, *args, **kwargs)

        subprocess.Popen.__init__ = no_window

    if getattr(sys, "frozen", False):
        os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="TikTokManager", add_help=False)
    p.add_argument("files", nargs="*")
    p.add_argument("--tray", action="store_true")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--update", action="store_true")
    p.add_argument("--updated-from", default="")
    p.add_argument("--update-failed", default="")
    ns, _unknown = p.parse_known_args(argv)  # ignore Qt's own options such as -platform
    return ns


def _hand_off_to_running_instance(ns: argparse.Namespace) -> bool:
    """If the app is already open, ask it to come forward (and add any files) instead of opening twice.

    Two copies running at once could post the same queued video twice.
    """
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    command = "update" if ns.update else "tray" if ns.tray and not ns.files else "show"
    sock.write((command + "\n" + "\n".join(os.path.abspath(f) for f in ns.files)).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main() -> int:
    _prepare_process()
    ns = _parse_args(sys.argv[1:])
    if ns.selftest:
        from ..diagnostics import run

        print(run())
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QApplication

    from ..config import APP_NAME
    from . import theme

    if os.name == "nt":
        try:  # own taskbar icon instead of python.exe's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TikTokManager.App")
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # the tray icon keeps autopilot alive
    if _hand_off_to_running_instance(ns):
        return 0

    app.setWindowIcon(theme.app_icon())
    theme.apply(app)

    from .. import updater
    from .main_window import MainWindow
    from .state import AppState

    state = AppState()
    win = MainWindow(state, start_hidden=ns.tray or ns.update)
    if ns.updated_from:
        updater.mark_started()  # the window came up: tell the update helper not to roll back
        win.after_update(ns.updated_from)
    elif ns.update_failed:
        win.after_failed_update(ns.update_failed)
    # Old downloads and backups can be hundreds of MB; delete them off the GUI thread.
    threading.Thread(target=updater.cleanup, daemon=True).start()
    if ns.update:
        win.check_for_updates(unattended=True)

    server = QLocalServer()
    QLocalServer.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)

    def on_connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return

        def read():
            win.handle_message(bytes(sock.readAll()).decode("utf-8", "replace"))

        sock.readyRead.connect(read)

    server.newConnection.connect(on_connection)

    if ns.files:
        from pathlib import Path

        win.library.add_paths([Path(f) for f in ns.files])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
