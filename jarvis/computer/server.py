"""JARVIS MCP server — the compact `computer.*` surface Claude Code drives (stdio).

15 tools. Internally each routes through the JARVIS hierarchy
(deterministic → UIA → JEV → OCR → vision → human) with stale-state
protection, safety policy, verification, and latency metrics.

NEVER print to stdout (stdio transport). Log to stderr / logs/computer.log.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent

from .. import config
from ..computer import ocr_boxes, uia
from ..computer.engine import capture, clipboard, displays, images
from ..computer.engine import input as winput
from ..computer.engine import process as eproc
from ..computer.engine import targets, winfind
from ..computer.engine import windows as ewin
from ..memory import store as memory
from ..orchestrator import act as _act
from ..orchestrator import batch as _batch
from ..orchestrator import router, verify, waits
from ..orchestrator.metrics import Timer, metrics
from ..safety import confirm, policy, privacy
from ..state import StaleStateError, store

config.ensure_dirs()
logging.basicConfig(
    level=getattr(logging, str(config.SETTINGS.get("log_level", "INFO")).upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(config.LOGS / "computer.log", encoding="utf-8")],
)
log = logging.getLogger("jarvis.mcp")

mcp = MCPServer(
    "jarvis",
    instructions=(
        "JARVIS local Windows computer engine. Prefer: computer_app/computer_key/computer_shell/computer_file for "
        "deterministic work; computer_observe + computer_click/computer_type by element name for GUI; computer_act "
        "for multi-step UI goals (JEV reflex loop). Take computer_screen screenshots only when structured tools fail."
    ),
)

_T0 = time.time()


def _j(obj: Any) -> str:
    return json.dumps(privacy.scrub_result(obj), ensure_ascii=False, default=str)


def _err(e: Exception, **extra) -> str:
    if isinstance(e, StaleStateError):
        d = {"error": "stale_state", "detail": str(e), "capture_id": e.capture_id}
        if e.fresh is not None:
            d["fresh"] = e.fresh.compact(60)
        return _j({**d, **extra})
    if isinstance(e, router.NeedsVision):
        return _j({"error": "needs_vision", "detail": e.reason, **({"jev": e.info} if getattr(e, "info", None) else {}),
                   "hint": "call computer_screen(action='screenshot') and reason visually, or refine the target",
                   "candidates": [{k: c.get(k) for k in ("id", "role", "name", "bounds")} for c in e.candidates],
                   "capture_id": getattr(e.obs, "capture_id", None), **extra})
    if isinstance(e, router.Blocked):
        return _j({"error": "blocked", "detail": str(e), "human_required": True, **extra})
    if isinstance(e, router.WrongWindow):
        return _j({"error": "wrong_window", "detail": str(e),
                   "hint": "the named window is not in front (user switched away?) — nothing was clicked or typed", **extra})
    log.exception("tool error")
    return _j({"error": type(e).__name__, "detail": policy.redact(str(e)), **extra})


# ---------------------------------------------------------------------------------------------
@mcp.tool(name="computer_observe")
def computer_observe(window: str | None = None, max_elements: int = 100, interactive_only: bool = True,
                     with_text: bool = False) -> str:
    """Observe the current UI state via UI Automation (no screenshot). Returns capture_id, active_window and a compact
    element list [{id, role, name, enabled, bounds, value, patterns}]. Use element ids with computer_click/computer_type.
    window: None=foreground, or a title/process substring (e.g. 'Notepad', 'process:chrome'). with_text adds readable text."""
    try:
        with Timer("mcp_ms"):
            obs = router.observe(window, max_elements=max_elements, interactive_only=interactive_only)
            if privacy.window_blocked(obs.window.get("title")):
                return _j({"error": "blocked", "detail": "window is on the privacy block list", "human_required": True})
            out = obs.compact()
            privacy.scrub_elements(out["elements"])
            for e in out["elements"]:
                e.pop("rid", None)
                e.pop("visible", None)
            if with_text:
                out["text"] = privacy.cap_text(ewin.get_text(obs.window.get("hwnd") and str(obs.window["hwnd"]) or None, 8000).get("text", ""))
            return _j(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_click")
def computer_click(target: Any, capture_id: int | None = None, button: str = "left", double: bool = False,
                   goal: str | None = None, window: str | None = None, action: str = "auto") -> str:
    """Click/activate a UI target. target: element id (int) from computer_observe, a name string ('Save'), {name, role},
    {automation_id}, or {x, y} physical pixels. Unique UIA match → direct; ambiguous → JEV picks using `goal`; no UIA match →
    OCR. Refuses stale capture_ids. action: auto|invoke|select|toggle|expand|collapse|focus. Verifies the UI changed."""
    try:
        with Timer("mcp_ms"):
            r = router.execute_click(target, capture_id=capture_id, button=button, count=2 if double else 1, goal=goal,
                                     window=window, action=action)
            metrics.tier(r.get("tier", "?"))
            if r.get("tier") in ("jev", "ocr") or r.get("heuristic"):
                obs = store.latest()
                memory.learn_routing((obs.window.get("process") if obs else "") or "", str(target)[:60], r.get("tier", ""), r)
            return _j(r)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_type")
def computer_type(text: str, target: Any = None, capture_id: int | None = None, clear: bool = False, enter: bool = False,
                  window: str | None = None) -> str:
    """Type text. target=None types into the focused control; otherwise focuses the target first (id/name/{x,y}).
    clear=True selects-all first. enter=True presses Enter after. Long/multiline text is pasted via clipboard.
    Verifies the field value where possible."""
    try:
        with Timer("mcp_ms"):
            return _j(router.execute_type(text, target=target, capture_id=capture_id, clear=clear, enter=enter, window=window))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_key")
def computer_key(keys: str | list[str], hold_ms: int = 0, repeat: int = 1, window: str | None = None, settle_ms: int = 400) -> str:
    """Press keys/shortcuts deterministically (no models). keys: 'ctrl+l', 'enter', 'alt+f4', 'win+r', or a list of combos
    pressed in sequence. hold_ms holds the combo. If window is given it is focused first. After sending, waits (bounded by
    settle_ms) until the foreground window state stops changing so the next step sees the result."""
    try:
        with Timer("mcp_ms"):
            if window:
                if _focus_any(window) is False:
                    return _j({"error": f"no window matching {window!r}"})
            combos = [keys] if isinstance(keys, str) else list(keys)
            fg = winfind.foreground_window()
            v = policy.classify_window(*_title_proc(fg))
            if v.level == policy.BLOCKED:
                return _j({"error": "blocked", "detail": v.reason, "human_required": True})
            with Timer("input_ms"):
                for c in combos:
                    for _ in range(max(1, repeat)):
                        if hold_ms > 0:
                            winput.hold(c, hold_ms / 1000)
                        elif "+" in c and len(c) > 1:
                            winput.press(c)
                        else:
                            winput.tap(c)
            metrics.tier("deterministic")
            settled = _settle(max_ms=int(settle_ms))
            return _j({"ok": True, "keys": combos, "tier": "deterministic", "focused": _title_proc(winfind.foreground_window())[0],
                       "settled_ms": settled})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_scroll")
def computer_scroll(direction: str = "down", amount: int = 3, target: Any = None, x: int | None = None, y: int | None = None) -> str:
    """Scroll at a target element / point / current cursor. direction: up|down|left|right; amount in wheel notches."""
    try:
        with Timer("mcp_ms"):
            if target is not None:
                el, _tier, _i = router.resolve_target(target)
                px, py = el["point"] if "point" in el else uia.center(el)
                winput.move(px, py)
            elif x is not None and y is not None:
                winput.move(int(x), int(y))
            with Timer("input_ms"):
                if direction in ("left", "right"):
                    winput.scroll(amount if direction == "right" else -amount, horizontal=True)
                else:
                    winput.scroll(-amount if direction == "down" else amount)
            return _j({"ok": True, "direction": direction, "amount": amount, "tier": "deterministic"})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_drag")
def computer_drag(from_target: Any, to_target: Any, button: str = "left", steps: int = 24, capture_id: int | None = None) -> str:
    """Drag from one target to another (element ids/names or {x,y})."""
    try:
        with Timer("mcp_ms"):
            obs = store.check_fresh(capture_id) if capture_id is not None else None
            a, _t1, _i1 = router.resolve_target(from_target, obs=obs)
            b, _t2, _i2 = router.resolve_target(to_target, obs=obs or store.latest())
            p1 = a["point"] if "point" in a else uia.center(a)
            p2 = b["point"] if "point" in b else uia.center(b)
            with Timer("input_ms"):
                winput.drag(p1[0], p1[1], p2[0], p2[1], button=button, steps=steps)
            return _j({"ok": True, "from": p1, "to": p2})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_window")
def computer_window(action: str = "list", query: str | None = None, x: int | None = None, y: int | None = None,
                    width: int | None = None, height: int | None = None, timeout: float = 8, monitor: int | None = None) -> str:
    """Window management. action: list|focus|close|minimize|maximize|restore|move|resize|wait|wait_gone|info.
    query: title/process substring or 'process:name'. move/resize take x,y,width,height in physical pixels."""
    try:
        with Timer("mcp_ms"):
            if action == "list":
                return _j(ewin.list_windows(query, limit=30, details=True))
            if action == "wait":
                r = waits.for_window(query or "", timeout)
                return _j({"ok": not r.get("timed_out") and not r.get("error"), **r})
            if action == "wait_gone":
                return _j(waits.for_window_gone(query or "", timeout))
            m = winfind.find_windows(query or "") if query else None
            if action in ("focus", "close", "minimize", "maximize", "restore", "move", "resize", "info"):
                if not m:
                    return _j({"error": f"no window matching {query!r}"})
                w = m[0]
                vv = policy.classify_window(w["title"], w["process"])
                if vv.level == policy.BLOCKED and action != "info":
                    return _j({"error": "blocked", "detail": vv.reason, "human_required": True})
                import ctypes
                u32 = ctypes.windll.user32
                hwnd = w["hwnd"]
                if action == "info":
                    return _j(w)
                if action == "focus":
                    ok = winfind.foreground(hwnd)
                    ver = verify.foreground(w["title"][:30] or query, 2)
                    return _j({"ok": ok and ver["verified"], "window": w["title"], "verification": ver})
                if action == "close":
                    winfind.close_window(hwnd)
                    ver = verify.window_gone(w["title"], min(timeout, 3))
                    out = {"ok": ver["verified"], "closed": w["title"], "verification": ver}
                    if not ver["verified"]:
                        try:  # a confirmation dialog (e.g. "Save changes?") usually appeared — surface it
                            obs = router.observe(None, max_elements=40, interactive_only=True)
                            out["dialog"] = {"capture_id": obs.capture_id, "window": obs.window.get("title"),
                                             "elements": [{k: e.get(k) for k in ("id", "role", "name")} for e in obs.elements[:12]]}
                        except Exception:  # noqa: BLE001
                            pass
                    return _j(out)
                if action in ("minimize", "maximize", "restore"):
                    u32.ShowWindow(hwnd, {"minimize": 6, "maximize": 3, "restore": 9}[action])
                    time.sleep(0.15)
                    import ctypes.wintypes as wt
                    r = wt.RECT()
                    u32.GetWindowRect(hwnd, ctypes.byref(r))
                    return _j({"ok": True, "action": action, "rect": [r.left, r.top, r.right, r.bottom],
                               "iconic": bool(u32.IsIconic(hwnd)), "zoomed": bool(u32.IsZoomed(hwnd))})
                if action in ("move", "resize"):
                    import ctypes.wintypes as wt
                    r = wt.RECT()
                    u32.GetWindowRect(hwnd, ctypes.byref(r))
                    nx = r.left if x is None else int(x)
                    ny = r.top if y is None else int(y)
                    nw = (r.right - r.left) if width is None else int(width)
                    nh = (r.bottom - r.top) if height is None else int(height)
                    if monitor is not None:
                        mr = displays.monitor_rect(int(monitor))
                        nx, ny = mr["left"] + (0 if x is None else int(x)), mr["top"] + (0 if y is None else int(y))
                    u32.ShowWindow(hwnd, 9)
                    ok = bool(u32.MoveWindow(hwnd, nx, ny, nw, nh, True))
                    u32.GetWindowRect(hwnd, ctypes.byref(r))
                    got = [r.left, r.top, r.right, r.bottom]
                    return _j({"ok": ok, "requested": [nx, ny, nw, nh], "rect": got,
                               "verification": {"verified": abs(r.left - nx) <= 16 and abs(r.top - ny) <= 16}})
            return _j({"error": f"unknown action {action!r}"})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_app")
def computer_app(action: str = "launch", name: str | None = None, args: list[str] | None = None, cwd: str | None = None,
                 wait_window: str | None = None, timeout: float = 15, pid: int | None = None) -> str:
    """Applications/processes. action: launch|list|kill|wait|running.
    launch: name = exe/path/URL/'ms-settings:' (deterministic; verified by window/process). wait_window: title to wait for
    (defaults to name stem). kill needs pid or name and asks confirmation for forced kill."""
    try:
        with Timer("mcp_ms"):
            if action == "list" or action == "running":
                import psutil
                q = (name or "").lower()
                rows = []
                for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
                    n = (p.info["name"] or "")
                    if not q or q in n.lower():
                        rows.append({"pid": p.info["pid"], "name": n})
                return _j({"processes": rows[:80], "count": len(rows)})
            if action == "launch":
                if not name:
                    return _j({"error": "name required"})
                v = policy.classify_app(name)
                if v.level == policy.CONFIRM and not confirm.ask(f"Launch {name}", v.reason):
                    return _j({"ok": False, "denied": True, "reason": v.reason})
                exe = _resolve_app(name)
                use_shell = "://" in name or name.endswith(":") or (":" in name and not os.path.exists(exe))
                t0 = time.perf_counter()
                before = {w["hwnd"] for w in winfind.list_windows()}
                r = eproc.launch(exe, args=args, cwd=cwd, shell=use_shell or not os.path.exists(exe))
                if r.get("error"):
                    return _j({"ok": False, **r})
                hint = wait_window or Path(name).stem
                ver = verify.launched(hint, r.get("pid"), timeout, before_hwnds=before)
                if ver.get("hwnd"):
                    winfind.foreground(ver["hwnd"])
                metrics.tier("deterministic")
                return _j({"ok": ver["verified"], "launch": r, "verification": ver, "tier": "deterministic",
                           "total_ms": round((time.perf_counter() - t0) * 1000)})
            if action == "kill":
                tgt = pid if pid is not None else name
                if tgt is None:
                    return _j({"error": "pid or name required"})
                if not confirm.ask(f"Kill process {tgt}", "forced process termination", remember_key=f"kill:{tgt}"):
                    return _j({"ok": False, "denied": True})
                r = eproc.kill(tgt)
                ver = verify.process(name, 5, present=False) if name else {"verified": bool(r.get("killed") or r.get("ok"))}
                return _j({"ok": ver["verified"], **r, "verification": ver})
            if action == "wait":
                r = eproc.wait(pid if pid is not None else name, timeout)
                return _j({"ok": not r.get("timed_out"), **r})
            return _j({"error": f"unknown action {action!r}"})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_element")
def computer_element(action: str = "find", name: str | None = None, role: str | None = None, automation_id: str | None = None,
                     id: int | None = None, capture_id: int | None = None, value: str | None = None, window: str | None = None,
                     timeout: float = 8, exact: bool = False) -> str:
    """UI Automation element operations. action: find|get|invoke|select|toggle|expand|collapse|set_value|focus|wait|wait_gone|text.
    Locate by id (from observe) or name/role/automation_id. 'text' reads the element's text/value. 'wait' blocks until present."""
    try:
        with Timer("mcp_ms"):
            if action in ("wait", "wait_gone"):
                r = waits.for_element(name=name, role=role, window=window, timeout=timeout, automation_id=automation_id,
                                      absent=(action == "wait_gone"))
                if r.get("element"):
                    r["element"].pop("rid", None)
                return _j(r)
            obs = store.check_fresh(capture_id) if capture_id is not None else router.observe(window, max_elements=200)
            if id is not None:
                els = [obs.element(int(id))] if obs.element(int(id)) else []
            else:
                els = uia.find_elements(obs.elements, name=name, role=role, automation_id=automation_id, exact=exact, enabled_only=False)
            if action == "find":
                return _j({"capture_id": obs.capture_id, "count": len(els),
                           "elements": [{k: v for k, v in e.items() if k not in ("rid", "visible")} for e in els[:40]]})
            if not els:
                return _j({"error": "not_found", "capture_id": obs.capture_id, "name": name, "role": role})
            if len(els) > 1 and id is None:
                exact_hits = [e for e in els if (e.get("name") or "").lower() == (name or "").lower()]
                if len(exact_hits) == 1:
                    els = exact_hits
                else:
                    return _j({"error": "ambiguous", "count": len(els), "capture_id": obs.capture_id,
                               "candidates": [{k: e.get(k) for k in ("id", "role", "name", "bounds")} for e in els[:20]],
                               "hint": "use computer_click with goal= (JEV picks) or pass id="})
            el = els[0]
            if action == "get":
                return _j({k: v for k, v in el.items() if k != "rid"})
            ctrl = uia.find_control(obs.window["hwnd"], el)
            if ctrl is None:
                return _j({"error": "element vanished; re-observe", "capture_id": obs.capture_id})
            if action == "text":
                return _j({"id": el["id"], "name": el.get("name"), "text": uia.read_text(ctrl)})
            vv = policy.classify_click(el, obs.window)
            if vv.level == policy.BLOCKED:
                return _j({"error": "blocked", "detail": vv.reason, "human_required": True})
            if vv.level == policy.CONFIRM and not confirm.ask(f"{action} '{el.get('name')}'", vv.reason):
                return _j({"ok": False, "denied": True, "reason": vv.reason})
            before = obs.fingerprint
            with Timer("input_ms"):
                r = uia.act_on(ctrl, el, action=action, value=value, foreground_hwnd=obs.window.get("hwnd"))
            out = {"ok": True, "tier": "uia", "element": {k: el.get(k) for k in ("id", "role", "name")}, **r}
            if action == "set_value":
                out["verification"] = verify.value(el, obs.window["hwnd"], value or "")
            else:
                out["verification"] = verify.state_changed(before, obs.window["hwnd"], 1.5)
            metrics.tier("uia")
            return _j(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_screen")
def computer_screen(action: str = "screenshot", target: str = "desktop", region: list[int] | None = None,
                    max_dim: int = 1568, window: str | None = None) -> list:
    """Screen access (fallback tier). action: screenshot|ocr|displays|cursor.
    target: 'desktop' | 'display:0' | 'display:primary' | 'window:<title>' | 'window:foreground'. region=[l,t,w,h] physical px.
    screenshot returns a downscaled image + capture_id + geometry (image coords → use computer_click {x,y} after mapping, or
    prefer computer_observe ids). ocr returns text lines with screen bounds usable as {x,y} click targets."""
    try:
        with Timer("mcp_ms"):
            if action == "displays":
                return [TextContent(type="text", text=_j({"monitors": displays.list_monitors(), "virtual": displays.virtual_bounds()}))]
            if action == "cursor":
                x, y = displays.cursor_pos()
                return [TextContent(type="text", text=_j({"x": x, "y": y}))]
            tgt = f"window:{window}" if window else target
            if tgt in ("window:foreground", "window:active"):
                tgt = f"window:{winfind.foreground_window()}"
            fg_title = _title_proc(winfind.foreground_window())[0]
            if privacy.window_blocked(fg_title):
                return [TextContent(type="text", text=_j({"error": "blocked", "detail": "foreground window is on the privacy block list"}))]
            if action == "screenshot":
                pol = privacy.screenshot_policy()
                if pol == "never":
                    return [TextContent(type="text", text=_j({"error": "screenshots disabled by privacy settings; use action='ocr' or computer_observe"}))]
                if pol == "confirm" and not confirm.ask("Send a screenshot to Claude", f"window: {fg_title[:60]}", remember_key="screenshot"):
                    return [TextContent(type="text", text=_j({"ok": False, "denied": True, "hint": "use computer_observe / ocr instead"}))]
            t0 = time.perf_counter()
            if tgt.startswith("window:") and tgt[7:].isdigit():
                img, rect, _ok = capture.grab_window(int(tgt[7:]))
            elif tgt.startswith("window:"):
                m = winfind.find_windows(tgt[7:])
                if not m:
                    return [TextContent(type="text", text=_j({"error": f"no window {tgt[7:]!r}"}))]
                img, rect, _ok = capture.grab_window(m[0]["hwnd"])
            elif tgt.startswith("display:"):
                sel = tgt[8:]
                idx = 0 if sel == "primary" else int(sel)
                img, rect = capture.grab_monitor(idx)
            else:
                img, rect = capture.grab_virtual()
            if region:
                left, top, w, h = region
                img = img.crop((left - rect["left"], top - rect["top"], left - rect["left"] + w, top - rect["top"] + h))
                rect = {"left": left, "top": top, "width": w, "height": h}
            cap_ms = round((time.perf_counter() - t0) * 1000)
            if action == "ocr":
                r = ocr_boxes.ocr_boxes(img, origin=(rect["left"], rect["top"]))
                metrics.add("ocr_ms", r["ocr_ms"])
                metrics.tier("ocr")
                return [TextContent(type="text", text=_j({"lines": [{"text": ln["text"], "bounds": ln["bounds"]} for ln in r["lines"]],
                                                             "text": privacy.cap_text(r["text"]), "ocr_ms": r["ocr_ms"], "capture_ms": cap_ms}))]
            # JPEG under a hard byte budget so the base64 tool result never overruns the SDK's message buffer
            data, mime, scale, size = images.fit_encode(img, max_dim=max_dim)
            obs = store.new({"hwnd": winfind.foreground_window(), "title": _title_proc(winfind.foreground_window())[0]}, [],
                            kind="screen", geometry={**rect, "scale": scale, "image_size": list(size)})
            metrics.tier("vision")
            meta = {"capture_id": obs.capture_id, "geometry": rect, "image_size": list(size), "scale": round(scale, 4),
                    "note": "physical = geometry.left + image_x/scale, geometry.top + image_y/scale", "capture_ms": cap_ms}
            return [TextContent(type="text", text=_j(meta)),
                    ImageContent(type="image", data=base64.b64encode(data).decode(), mime_type=mime)]
    except Exception as e:  # noqa: BLE001
        return [TextContent(type="text", text=_err(e))]


@mcp.tool(name="computer_shell")
def computer_shell(command: str, shell: str = "powershell", cwd: str | None = None, timeout: float = 120,
                   expect_exit: int | None = 0) -> str:
    """Run a shell command (powershell|cmd) and return exit code, stdout, stderr (deterministic tier). Dangerous commands
    (recursive delete, destructive git, firewall/registry/credential changes, remote script exec) ask for confirmation."""
    try:
        with Timer("mcp_ms"):
            v = policy.classify_shell(command)
            if v.level == policy.BLOCKED:
                return _j({"error": "blocked", "detail": v.reason})
            if v.level == policy.CONFIRM and not confirm.ask("Run command", f"{v.reason}\n\n{policy.redact(command)[:500]}"):
                return _j({"ok": False, "denied": True, "reason": v.reason})
            with Timer("shell_ms") as t:
                r = eproc.shell(command, timeout=timeout, shell=shell, cwd=cwd)
            r["stdout"] = policy.redact((r.get("stdout") or "")[-12000:])
            r["stderr"] = policy.redact((r.get("stderr") or "")[-4000:])
            out = {"ok": (r.get("exit") == expect_exit) if expect_exit is not None else not r.get("timed_out"), **r,
                   "shell_ms": t.ms, "tier": "deterministic"}
            metrics.tier("deterministic")
            return _j(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_file")
def computer_file(action: str, path: str, dest: str | None = None, content: str | None = None, pattern: str | None = None,
                  recursive: bool = True, max_chars: int = 20000, append: bool = False) -> str:
    """Filesystem (deterministic, verified). action: read|write|append|copy|move|rename|delete|list|search|info|mkdir|archive|extract|exists.
    search: pattern glob (e.g. '*.pdf') under path. archive: zip `path` into `dest`. extract: unzip `path` to `dest`.
    delete asks for confirmation; protected system/credential paths are refused."""
    try:
        with Timer("mcp_ms"), Timer("file_ms"):
            p = Path(os.path.expandvars(os.path.expanduser(path)))
            d = Path(os.path.expandvars(os.path.expanduser(dest))) if dest else None
            v = policy.classify_file(action, str(p), str(d) if d else None)
            if v.level == policy.BLOCKED:
                return _j({"error": "blocked", "detail": v.reason})
            if v.level == policy.CONFIRM and not confirm.ask(f"File {action}", v.reason, remember_key=f"file:{action}:{p}"):
                return _j({"ok": False, "denied": True, "reason": v.reason})
            metrics.tier("deterministic")
            if action == "exists":
                return _j({"exists": p.exists(), "is_dir": p.is_dir(), "path": str(p)})
            if action == "read":
                data = p.read_text(encoding="utf-8", errors="replace")
                return _j({"path": str(p), "size": p.stat().st_size, "content": data[:max_chars], "truncated": len(data) > max_chars})
            if action in ("write", "append"):
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a" if (append or action == "append") else "w", encoding="utf-8") as f:
                    f.write(content or "")
                back = p.read_text(encoding="utf-8", errors="replace")
                return _j({"ok": (content or "") in back, "path": str(p), "size": p.stat().st_size,
                           "verification": {"verified": (content or "") in back, "check": "file"}})
            if action == "mkdir":
                p.mkdir(parents=True, exist_ok=True)
                return _j({"ok": p.is_dir(), "path": str(p)})
            if action == "list":
                items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                return _j({"path": str(p), "entries": [{"name": x.name, "dir": x.is_dir(),
                                                        "size": (x.stat().st_size if x.is_file() else None)} for x in items[:300]],
                           "count": len(items)})
            if action == "search":
                pat = pattern or "*"
                hits = list(p.rglob(pat) if recursive else p.glob(pat))
                hits.sort(key=lambda x: -x.stat().st_mtime if x.exists() else 0)
                return _j({"path": str(p), "pattern": pat, "count": len(hits), "matches": [str(x) for x in hits[:200]]})
            if action == "info":
                st = p.stat()
                return _j({"path": str(p), "size": st.st_size, "mtime": st.st_mtime, "is_dir": p.is_dir()})
            if action in ("copy", "move", "rename"):
                if d is None:
                    return _j({"error": "dest required"})
                if action == "rename" and not d.is_absolute():
                    d = p.parent / d
                d.parent.mkdir(parents=True, exist_ok=True)
                if action == "copy":
                    (shutil.copytree if p.is_dir() else shutil.copy2)(p, d)
                else:
                    shutil.move(str(p), str(d))
                ok = d.exists() and (action == "copy" or not p.exists())
                return _j({"ok": ok, "src": str(p), "dest": str(d), "verification": {"verified": ok, "check": "file"}})
            if action == "delete":
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
                return _j({"ok": not p.exists(), "path": str(p), "verification": {"verified": not p.exists()}})
            if action == "archive":
                if d is None:
                    return _j({"error": "dest required"})
                with zipfile.ZipFile(d, "w", zipfile.ZIP_DEFLATED) as z:
                    if p.is_dir():
                        for f in p.rglob("*"):
                            if f.is_file():
                                z.write(f, f.relative_to(p))
                    else:
                        z.write(p, p.name)
                return _j({"ok": d.exists(), "archive": str(d), "size": d.stat().st_size})
            if action == "extract":
                if d is None:
                    return _j({"error": "dest required"})
                with zipfile.ZipFile(p) as z:
                    z.extractall(d)
                    n = len(z.namelist())
                return _j({"ok": d.exists(), "dest": str(d), "entries": n})
            return _j({"error": f"unknown action {action!r}"})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_wait")
def computer_wait(for_: str, query: str | None = None, timeout: float = 10, window: str | None = None, role: str | None = None,
                  absent: bool = False) -> str:
    """Bounded state-based wait (never a blind sleep). for_: window|window_gone|process|process_gone|element|text|file|title|browser.
    query: title/process/element name/text/file path (glob ok)/url substring."""
    try:
        with Timer("mcp_ms"):
            q = query or ""
            if for_ == "window":
                r = waits.for_window(q, timeout)
                return _j({"ok": not r.get("timed_out") and not r.get("error"), **r})
            if for_ == "window_gone":
                return _j(waits.for_window_gone(q, timeout))
            if for_ == "process":
                return _j(waits.for_process(q, timeout, present=True))
            if for_ == "process_gone":
                return _j(waits.for_process(q, timeout, present=False))
            if for_ == "element":
                r = waits.for_element(name=q, role=role, window=window, timeout=timeout, absent=absent)
                if r.get("element"):
                    r["element"].pop("rid", None)
                return _j(r)
            if for_ == "text":
                return _j(waits.for_text(q, window, timeout, use_ocr=True))
            if for_ == "file":
                return _j(waits.for_file(q, timeout, absent=absent))
            if for_ == "title":
                return _j(waits.for_title(q, timeout))
            if for_ == "browser":
                return _j(verify.browser_state(title_contains=None, url_contains=q, timeout=timeout))
            return _j({"error": f"unknown for_ {for_!r}"})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_confirm")
def computer_confirm(action: str, detail: str = "", timeout: float = 90) -> str:
    """Ask the human to approve a high-impact action (destructive deletion, git force/push, security/registry/firewall
    changes, credentials, payments, publishing/sending). Returns approved true/false. Also use when a security boundary
    (UAC, login, CAPTCHA, MFA) needs the human to act — then wait for them."""
    try:
        ok = confirm.ask(action, detail, timeout_s=int(timeout))
        return _j({"approved": ok, "action": action})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_act")
def computer_act(goal: str, window: str | None = None, max_steps: int = 10, text_values: dict | None = None,
                 done_when: dict | None = None) -> str:
    """Run a multi-step UI goal locally with the JEV reflex loop (observe → JEV picks operation+target → execute → verify),
    without consulting Claude per click. Give a concrete, visible goal ('open the Downloads folder in the left pane',
    'click Save in the dialog'). text_values={'field name': 'text'} supplies any text to type. done_when={'element': name}|
    {'text': s}|{'window': title}|{'window_gone': title}|{'file': path} stops early when verified. Returns status:
    done|blocked|low_confidence|needs_text|stalled|jev_unavailable and the step trace. On non-done, take over with
    computer_observe/computer_click."""
    try:
        with Timer("mcp_ms"):
            return _j(_act.run(goal, window=window, max_steps=max_steps, text_values=text_values, done_when=done_when))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_run")
def computer_run(steps: list[dict], goal: str | None = None, window: str | None = None, stop_on_fail: bool = True) -> str:
    """Run a SEQUENCE of steps locally in one call with verification checkpoints - use this instead of many single tool
    calls whenever the next steps are predictable. Steps use the same vocabulary as the tools, one action key each:
      {"app": "notepad.exe", "wait_window": "Notepad"}   {"key": "ctrl+s"}   {"type": "text", "target": {"role":"document"}, "clear": true}
      {"click": "Save"}   {"click": {"name":"Submit","role":"button"}, "goal": "..."}   {"element": {"action":"select","name":"..."}}
      {"wait": "window", "query": "Save as"}   {"wait": "element"|"text"|"file"|"window_gone", "query": ...}
      {"window_action": {"action":"close","query":"Notepad"}}   {"scroll": "down"}   {"file": {"action":"read","path":...}}
      {"shell": "git status"}   {"act": {"goal": "...", "text_values": {...}}}   {"observe": true}   {"expect": {...}}
    Optional per step: "expect" {"window"|"window_gone"|"element"|"text"|"file"|"title"|"browser": ...}, "window", "timeout".
    Stops at the first failed step/expect (or blocked/denied) and returns the trace + a fresh observation so you can
    continue from exactly there. Deterministic and unique-UIA steps never touch JEV; ambiguous clicks with goal= do.
    Successful sequences are remembered (memory) for next time."""
    try:
        with Timer("mcp_ms"):
            r = _batch.run(steps, sys.modules[__name__], stop_on_fail=stop_on_fail, window=window)
            app_name = None
            obs = store.latest()
            if obs:
                app_name = (obs.window.get("process") or "").replace(".exe", "")
            memory.learn_batch(goal, app_name, r, steps)
            return _j(r)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_memory")
def computer_memory(action: str = "recall", query: str | None = None, kind: str | None = None, app: str | None = None,
                    note: str | None = None, key: str | None = None, value: str | None = None, limit: int = 6) -> str:
    """Persistent local experience memory (D:/JARVIS/data/memory). action: recall|remember|forget|preferences.
    recall: query (goal text) + optional app -> relevant preferences/workflows/corrections/failures/app_patterns/routing.
    remember: kind = preferences (key+value) | corrections | failures | app_patterns (note, app) - store what the user
    told you, what worked, what to avoid. Never store secrets, passwords or raw document text (redacted anyway).
    forget: kind + query substring."""
    try:
        if action == "recall":
            return _j({"memory": memory.recall(query or "", app, limit=limit), "preferences": memory.preferences()})
        if action == "preferences":
            return _j({"preferences": memory.preferences()})
        if action == "remember":
            if kind == "preferences" or key:
                return _j(memory.set_preference(key or (note or "note")[:60], value if value is not None else note))
            k = kind or "app_patterns"
            entry = {"app": app or "", "note": note or "", "goal": query or ""}
            if k == "app_patterns":
                entry["pattern"] = note or ""
            if k == "failures":
                entry["lesson"] = note or ""
            return _j(memory.remember(k, entry))
        if action == "forget":
            return _j(memory.forget(kind or "app_patterns", query or ""))
        return _j({"error": f"unknown action {action!r}"})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(name="computer_status")
def computer_status() -> str:
    """JARVIS engine status: DPI mode, monitors, JEV availability, latency metrics by tier, uptime."""
    from ..computer import engine as eng
    from ..jev import engine as jev_engine
    return _j({"dpi": eng.DPI_AWARENESS, "monitors": displays.list_monitors(), "jev_available": jev_engine().available,
               "jev": jev_engine().metrics.as_dict(), "jev_budget": __import__("jarvis.jev.budget", fromlist=["budget"]).budget().snapshot(),
               "learning": __import__("jarvis.learning", fromlist=["manager"]).manager().status(), "metrics": metrics.snapshot(), "uptime_s": round(time.time() - _T0),
               "root": str(config.ROOT), "clipboard_seq": clipboard.sequence_number()})


# ---------------------------------------------------------------------------------------------
_APP_ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe", "explorer": "explorer.exe",
    "file explorer": "explorer.exe", "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge",
    "vs code": "code", "vscode": "code", "visual studio code": "code", "code": "code", "terminal": "wt.exe",
    "windows terminal": "wt.exe", "powershell": "powershell.exe", "cmd": "cmd.exe", "command prompt": "cmd.exe",
    "paint": "mspaint.exe", "settings": "ms-settings:", "task manager": "taskmgr.exe", "cursor": "cursor",
    "antigravity": "antigravity",
}


def _resolve_app(name: str) -> str:
    key = name.strip().lower()
    cand = _APP_ALIASES.get(key, name)
    if os.path.exists(cand) or "://" in cand or cand.endswith(":"):
        return cand
    found = shutil.which(cand) or shutil.which(cand + ".exe") or shutil.which(cand + ".cmd")
    if found:
        return found
    # Start-menu / desktop shortcuts and common install dirs
    for base in (Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("USERPROFILE", "")) / "Desktop"):
        if base.exists():
            for lnk in base.rglob("*.lnk"):
                if key in lnk.stem.lower():
                    return str(lnk)
    for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs", Path(os.environ.get("ProgramFiles", "")),
                 Path(os.environ.get("ProgramFiles(x86)", ""))):
        if base.exists():
            for exe in base.glob(f"*{cand}*/*.exe"):
                return str(exe)
            for exe in base.glob(f"*/{cand}.exe"):
                return str(exe)
    return cand


def _settle(max_ms: int = 400, quiet_ms: int = 60) -> int:
    """Bounded wait until foreground window identity/title/rect are unchanged for `quiet_ms` (UI stopped changing)."""
    import ctypes
    import ctypes.wintypes as wt
    t0 = time.perf_counter()
    last, quiet_since = None, None
    u32 = ctypes.windll.user32
    while (time.perf_counter() - t0) * 1000 < max_ms:
        hwnd = u32.GetForegroundWindow()
        r = wt.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        cur = (hwnd, buf.value, r.left, r.top, r.right, r.bottom)
        now = time.perf_counter()
        if cur == last:
            if quiet_since is not None and (now - quiet_since) * 1000 >= quiet_ms:
                break
            quiet_since = quiet_since or now
        else:
            last, quiet_since = cur, now
        time.sleep(0.02)
    return round((time.perf_counter() - t0) * 1000)


def _focus_any(window) -> bool:
    """Focus by hwnd (int / digit string) or title/process query; wait briefly until it is foreground."""
    if isinstance(window, int) or (isinstance(window, str) and window.isdigit()):
        hwnd = int(window)
        ok = winfind.foreground(hwnd)
        end = time.perf_counter() + 1.5
        while time.perf_counter() < end and winfind.foreground_window() != hwnd:
            time.sleep(0.05)
        return ok
    m = winfind.find_windows(str(window))
    if not m:
        return False
    winfind.foreground(m[0]["hwnd"])
    waits.for_title(m[0]["title"][:40], 1.5)
    return True


def _title_proc(hwnd: int) -> tuple[str, str]:
    for w in winfind.list_windows():
        if w["hwnd"] == hwnd:
            return w["title"], w["process"]
    return "", ""


def _warm_jev():
    """Pre-open the HTTP/2 connection so the first real JEV decision is ~0.4 s instead of ~2 s."""
    try:
        from ..jev import engine as jev_engine
        e = jev_engine()
        if e.available:
            e._http().get("https://api.typesafe.ai/v1/models", headers={"Authorization": f"Bearer {e.api_key}"})
            log.info("JEV connection warmed")
    except Exception as ex:  # noqa: BLE001
        log.debug("JEV warm-up skipped: %s", ex)


def main() -> None:
    log.info("JARVIS MCP server starting (root=%s, jev=%s)", config.ROOT, bool(config.jev_api_key()))
    import threading
    threading.Thread(target=_warm_jev, daemon=True).start()
    try:
        mcp.run("stdio")
    finally:
        metrics.flush("server-exit")


if __name__ == "__main__":
    main()


_ = (io, targets)
