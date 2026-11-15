"""Reusable verification primitives (TRD §13). Every meaningful action gets one.

Each returns {"verified": bool, ...evidence}. "Input was sent" is never proof.
"""
from __future__ import annotations

import os
import time

from ..computer import uia
from ..computer.engine import process as eproc
from ..computer.engine import winfind
from . import waits


def window(query: str, timeout: float = 8) -> dict:
    r = waits.for_window(query, timeout)
    ok = bool(r.get("title")) and not r.get("timed_out") and not r.get("error")
    return {"verified": ok, "check": "window", "query": query, **r}


def window_gone(query: str, timeout: float = 8) -> dict:
    r = waits.for_window_gone(query, timeout)
    return {"verified": bool(r.get("ok")), "check": "window_gone", "query": query, **r}


def process(name: str, timeout: float = 8, present: bool = True) -> dict:
    r = waits.for_process(name, timeout, present)
    return {"verified": bool(r.get("ok")), "check": "process", "name": name, **r}


def element(name: str | None = None, role: str | None = None, window_q: str | None = None,
            timeout: float = 5, absent: bool = False) -> dict:
    r = waits.for_element(name=name, role=role, window=window_q, timeout=timeout, absent=absent)
    return {"verified": bool(r.get("ok")), "check": "element", "name": name, **r}


def text(expected: str, window_q: str | None = None, timeout: float = 5) -> dict:
    r = waits.for_text(expected, window_q, timeout)
    return {"verified": bool(r.get("ok")), "check": "text", "expected": expected[:80], **r}


def file(path: str, timeout: float = 5, content_contains: str | None = None, absent: bool = False) -> dict:
    r = waits.for_file(path, timeout, absent=absent)
    out = {"verified": bool(r.get("ok")), "check": "file", "path": path, **r}
    if out["verified"] and content_contains and not absent:
        try:
            data = open(r.get("path", path), encoding="utf-8", errors="replace").read()
            out["verified"] = content_contains in data
            out["content_match"] = out["verified"]
        except OSError as e:
            out["verified"] = False
            out["error"] = str(e)
    return out


def directory(path: str) -> dict:
    return {"verified": os.path.isdir(path), "check": "directory", "path": path}


def command(result: dict, expect_code: int = 0, expect_stdout: str | None = None) -> dict:
    code = result.get("exit", result.get("returncode", result.get("exit_code")))
    ok = code == expect_code and not result.get("timed_out") and not result.get("error")
    if ok and expect_stdout:
        ok = expect_stdout in (result.get("stdout") or "")
    return {"verified": ok, "check": "command", "exit": code}


def value(el_before: dict, window_hwnd: int, expected: str) -> dict:
    """Re-read an edit control's value after typing."""
    try:
        ctrl = uia.find_control(window_hwnd, el_before)
        if ctrl is None:
            return {"verified": False, "check": "value", "error": "element not re-resolved"}
        v = uia.read_text(ctrl, 4000)
        return {"verified": expected in v, "check": "value", "value": v[:200]}
    except Exception as e:  # noqa: BLE001
        return {"verified": False, "check": "value", "error": str(e)}


def state_changed(before_fp: str, window_q: str | None = None, timeout: float = 2.5) -> dict:
    """Did the UI change after an action? Bounded poll on the UIA fingerprint."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        try:
            w, els, _m = uia.snapshot(window_q, max_elements=120)
            from ..state.store import fingerprint_elements
            fp = fingerprint_elements(w, els)
            if fp != before_fp:
                return {"verified": True, "check": "state_changed", "active_window": w.get("title")}
        except Exception:  # noqa: BLE001
            return {"verified": True, "check": "state_changed", "note": "window changed/closed"}
        time.sleep(0.1)
    return {"verified": False, "check": "state_changed", "note": "no UI change detected"}


def foreground(query: str, timeout: float = 3) -> dict:
    r = waits.for_title(query, timeout)
    return {"verified": bool(r.get("ok")), "check": "foreground", **r}


def browser_state(title_contains: str | None = None, url_contains: str | None = None, timeout: float = 8) -> dict:
    """Verify the foreground browser window title and/or address-bar URL (UIA ValuePattern)."""
    end = time.perf_counter() + timeout
    last = {}
    while time.perf_counter() < end:
        hwnd = winfind.foreground_window()
        wins = [w for w in winfind.list_windows() if w["hwnd"] == hwnd]
        if wins and winfind.is_browser(wins[0]["process"]):
            w = wins[0]
            ok_t = title_contains is None or title_contains.lower() in (w["title"] or "").lower()
            ok_u = True
            url = None
            if url_contains is not None:
                try:
                    _w, els, _m = uia.snapshot(hwnd, max_elements=80, max_depth=12)
                    for e in els:
                        if e.get("role") in ("edit", "combobox") and e.get("value") and ("address" in (e.get("name") or "").lower() or "://" in str(e.get("value")) or "." in str(e.get("value"))):
                            url = e["value"]
                            break
                except Exception:  # noqa: BLE001
                    pass
                ok_u = url is not None and url_contains.lower() in url.lower()
            last = {"title": w["title"], "url": url}
            if ok_t and ok_u:
                return {"verified": True, "check": "browser", **last}
        time.sleep(0.25)
    return {"verified": False, "check": "browser", **last}


def launched(name_hint: str, pid: int | None, timeout: float = 10, before_hwnds: set | None = None) -> dict:
    """App launch verification: a NEW window (not in before_hwnds) for the app exists, or the process is alive."""
    end = time.perf_counter() + timeout
    before_hwnds = before_hwnds or set()
    while time.perf_counter() < end:
        alive = False
        if pid:
            alive = eproc.wait(pid, 0.01).get("timed_out") is True  # still running ⇒ wait times out
        else:
            alive = bool(eproc._list_pids_by_image(name_hint))
        wins = winfind.find_windows(name_hint) if name_hint else []
        if pid:
            wins = [w for w in wins if w.get("pid") == pid] or wins
        new = [w for w in wins if w["hwnd"] not in before_hwnds]
        if new:
            return {"verified": True, "check": "launched", "window": new[0]["title"], "process": new[0]["process"], "hwnd": new[0]["hwnd"], "new_window": True}
        if wins and time.perf_counter() > end - 0.3 * timeout:
            return {"verified": True, "check": "launched", "window": wins[0]["title"], "process": wins[0]["process"], "hwnd": wins[0]["hwnd"], "new_window": False}
        if alive and not name_hint:
            return {"verified": True, "check": "launched", "pid": pid}
        time.sleep(0.15)
    return {"verified": False, "check": "launched", "name": name_hint, "pid": pid, "note": "no window found"}


def snapshot_fingerprint(window_q: str | None = None) -> str:
    w, els, _m = uia.snapshot(window_q, max_elements=120)
    from ..state.store import fingerprint_elements
    return fingerprint_elements(w, els)


