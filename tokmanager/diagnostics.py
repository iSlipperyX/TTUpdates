"""`TikTokManager.exe --selftest`: check ffmpeg and the browser automation work, write a report, exit."""

from __future__ import annotations

import time
import traceback

from .config import Settings, app_dir


def run() -> str:
    lines: list[str] = [time.strftime("Self-test %Y-%m-%d %H:%M:%S")]

    def check(name: str, fn) -> None:
        t0 = time.time()
        try:
            detail = fn()
            lines.append(f"PASS {name} ({time.time() - t0:.1f}s){': ' + detail if detail else ''}")
        except Exception as exc:
            lines.append(f"FAIL {name}: {exc}\n{traceback.format_exc()}")

    def ffmpeg():
        from .media import _run, ffmpeg_exe

        out = _run([ffmpeg_exe(), "-hide_banner", "-version"]).stdout.splitlines()[0]
        return out

    def browser():
        from playwright.sync_api import sync_playwright

        s = Settings.load()
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel=s.browser_channel or "msedge", headless=True)
            page = b.new_page()
            page.set_content("<p id=x>ok</p>")
            text = page.locator("#x").inner_text()
            version = b.version
            b.close()
        return f"{s.browser_channel} {version}, page said {text!r}"

    check("ffmpeg", ffmpeg)
    check("browser automation", browser)
    report = "\n".join(lines)
    (app_dir() / "selftest.txt").write_text(report, encoding="utf-8")
    return report
