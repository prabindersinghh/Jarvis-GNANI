"""State-based waits with bounded timeouts (no arbitrary sleeps as synchronization)."""
from __future__ import annotations

import glob as _glob
import os
import time

from ..computer import uia
from ..computer.engine import process as eproc
from ..computer.engine import winfind

POLL = 0.12


def _deadline(timeout: float):
    return time.perf_counter() + max(0.05, float(timeout))


def for_window(query: str, timeout: float = 10, ready: str = "input_idle") -> dict:
    return eproc.wait_for_window(query, timeout, ready=ready)


def for_window_gone(query: str, timeout: float = 10) -> dict:
    end = _deadline(timeout)
    while time.perf_counter() < end:
        if not winfind.find_windows(query):
            return {"ok": True, "gone": True}
        time.sleep(POLL)
    return {"ok": False, "timed_out": True, "still_open": [w["title"] for w in winfind.find_windows(query)][:3]}


def for_process(name: str, timeout: float = 10, present: bool = True) -> dict:
    end = _deadline(timeout)
    while time.perf_counter() < end:
        pids = eproc._list_pids_by_image(name)
        if bool(pids) == present:
            return {"ok": True, "pids": pids}
        time.sleep(POLL)
    return {"ok": False, "timed_out": True}


def for_file(path: str, timeout: float = 10, stable: bool = True, absent: bool = False) -> dict:
    """Wait until `path` (may contain a glob) exists (or is absent) and, if stable, stops growing."""
    end = _deadline(timeout)
    last = None
    while time.perf_counter() < end:
        matches = _glob.glob(path) if any(c in path for c in "*?[") else ([path] if os.path.exists(path) else [])
        if absent:
            if not matches:
                return {"ok": True}
        elif matches:
            p = matches[0]
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = -1
            if not stable or sz == last:
                return {"ok": True, "path": p, "size": sz}
            last = sz
        time.sleep(POLL if not matches else 0.25)
    return {"ok": False, "timed_out": True}


def for_element(name: str | None = None, role: str | None = None, window: str | None = None,
                timeout: float = 10, automation_id: str | None = None, absent: bool = False) -> dict:
    end = _deadline(timeout)
    last_err = None
    while time.perf_counter() < end:
        try:
            _w, els, _m = uia.snapshot(window, max_elements=200)
            found = uia.find_elements(els, name=name, role=role, automation_id=automation_id, enabled_only=False)
            if bool(found) != absent:
                return {"ok": True, "count": len(found), "element": (found[0] if found else None)}
        except LookupError as e:
            last_err = str(e)
            if absent:
                return {"ok": True, "count": 0, "note": "window gone"}
        time.sleep(POLL)
    return {"ok": False, "timed_out": True, "error": last_err}


def for_text(text: str, window: str | None = None, timeout: float = 10, use_ocr: bool = False) -> dict:
    from ..computer.engine import windows as ewin
    end = _deadline(timeout)
    q = text.lower()
    while time.perf_counter() < end:
        try:
            r = ewin.get_text(window)
            blob = (r.get("text") or "").lower()
            if q in blob:
                return {"ok": True, "source": r.get("source", "uia")}
            if use_ocr:
                from ..computer.engine import capture, ocr
                hwnd = r.get("hwnd") or winfind.foreground_window()
                img, _g, _o = capture.grab_window(hwnd)
                if q in (ocr.ocr_image(img) or "").lower():
                    return {"ok": True, "source": "ocr"}
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    return {"ok": False, "timed_out": True}


def for_title(contains: str, timeout: float = 10) -> dict:
    end = _deadline(timeout)
    q = contains.lower()
    while time.perf_counter() < end:
        hwnd = winfind.foreground_window()
        for w in winfind.list_windows():
            if w["hwnd"] == hwnd and q in (w["title"] or "").lower():
                return {"ok": True, "title": w["title"]}
        time.sleep(POLL)
    return {"ok": False, "timed_out": True}
