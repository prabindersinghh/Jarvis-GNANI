"""Human confirmation for CONFIRM-level operations.

Shows a topmost Windows message box (works from the MCP server process, which
runs in the interactive desktop session). Times out to DENY. An in-process
override hook lets the JARVIS console/tray answer instead of a raw MessageBox.
Confirmations are remembered for a short window so one approval covers a
multi-step operation on the same subject.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time

from .. import config

log = logging.getLogger("jarvis.confirm")

MB_YESNO = 0x4
MB_ICONWARNING = 0x30
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000
IDYES = 6
DEFAULT_TIMEOUT_S = 90

_hook = None  # callable(title, message, timeout_s) -> bool
_lock = threading.Lock()
_recent: dict[str, float] = {}
REMEMBER_S = 120


def set_hook(fn):
    global _hook
    _hook = fn


def ask(subject: str, detail: str = "", timeout_s: int = DEFAULT_TIMEOUT_S, remember_key: str | None = None) -> bool:
    """Return True if the human approved. Auto-approve only when JARVIS_AUTO_CONFIRM=1 (tests)."""
    key = remember_key or subject
    with _lock:
        t = _recent.get(key)
        if t and time.time() - t < REMEMBER_S:
            return True
    if os.environ.get("JARVIS_AUTO_CONFIRM") == "1":
        log.warning("AUTO-CONFIRM (test mode): %s", subject)
        return True
    brand = config.SETTINGS.get("brand", "JARVIS")
    title = f"{brand} — confirm action"
    msg = f"{subject}\n\n{detail}\n\nAllow?".strip()
    ok = False
    try:
        if _hook is not None:
            ok = bool(_hook(title, msg, timeout_s))
        else:
            fn = ctypes.windll.user32.MessageBoxTimeoutW
            fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_ushort, ctypes.c_uint]
            fn.restype = ctypes.c_int
            r = fn(None, msg, title, MB_YESNO | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND, 0, int(timeout_s * 1000))
            ok = r == IDYES
    except Exception as e:  # noqa: BLE001
        log.error("confirm dialog failed: %s", e)
        ok = False
    log.info("confirm %r → %s", subject, "APPROVED" if ok else "DENIED")
    if ok:
        with _lock:
            _recent[key] = time.time()
    return ok
