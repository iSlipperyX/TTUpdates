"""Encrypt API keys and tokens at rest with Windows DPAPI (tied to your Windows login)."""

from __future__ import annotations

import base64
import os

PREFIX = "dpapi:"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    if not text or os.name != "nt":
        return text
    return PREFIX + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def unprotect(text: str) -> str:
    if not text or not text.startswith(PREFIX):
        return text
    try:
        return _dpapi(base64.b64decode(text[len(PREFIX):]), False).decode("utf-8")
    except Exception:
        return ""  # copied from another PC/user: the secret can't be recovered, ask again
