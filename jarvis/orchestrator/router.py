"""JARVIS router — the central speed architecture (MASTER §5).

    deterministic  →  UIA (unique)  →  JEV (ambiguous)  →  OCR/screen  →  Claude vision  →  human

`resolve_target()` turns a target spec into ONE concrete element (or a point)
and reports which tier resolved it. `execute()` runs the action with stale-state
protection, safety policy, and post-verification.

Target spec forms accepted from Claude:
  {"id": 7}                              element id from the last observe (deterministic)
  {"x": 100, "y": 200}                   physical pixel (deterministic; needs capture_id)
  {"name": "Download"}                   UIA lookup; unique → direct, many → JEV
  {"name": "Save", "role": "button"}     narrowed lookup
  {"automation_id": "SaveButton"}        exact
  {"text": "Downloads"}                  OCR fallback allowed (screen text)
  "Download"                             shorthand for {"name": "Download"}
"""
from __future__ import annotations

import logging
import time

from .. import config
from ..computer import ocr_boxes, uia
from ..computer.engine import capture
from ..computer.engine import input as winput
from ..computer.engine import winfind
from ..jev import JevUnavailable, engine as jev_engine
from ..safety import confirm, policy
from ..state import StaleStateError, store
from . import verify
from .metrics import metrics

log = logging.getLogger("jarvis.router")

# WinUI apps (Win11 Notepad etc.) corrupt batched KEYEVENTF_UNICODE input: pace one char per SendInput.
TYPE_INTERVAL = 0.004
# Observations younger than this are acted on without a second UIA walk (saves ~130 ms per click).
FRESH_S = 0.4

TIER_DETERMINISTIC, TIER_UIA, TIER_JEV, TIER_OCR, TIER_VISION, TIER_HUMAN = \
    "deterministic", "uia", "jev", "ocr", "vision", "human"


class NeedsVision(Exception):
    """No tier below Claude vision could resolve the target; caller should take a screenshot."""

    def __init__(self, reason: str, candidates: list[dict] | None = None, obs=None, info: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.candidates = candidates or []
        self.obs = obs
        self.info = info


class Blocked(Exception):
    pass


class WrongWindow(Exception):
    """A step named a window, but that window could not be made foreground — the user is working elsewhere.
    Raw pixel clicks / focused typing are refused rather than landing in whatever is on top."""


def ensure_window(window: str | int | None) -> dict | None:
    """Bring `window` to the foreground (no-op if it already is) and confirm it. Returns the window dict or None
    when no window was named. Raises LookupError (no such window) / WrongWindow (could not take the foreground)."""
    if window is None or window == "":
        return None
    wins = winfind.find_windows(str(window))
    if not wins:
        raise LookupError(f"no window matching {window!r}")
    fg = winfind.foreground_window()
    hit = next((w for w in wins if w["hwnd"] == fg), None)
    if hit is not None:
        return hit
    target = wins[0]
    winfind.foreground(target["hwnd"])
    end = time.perf_counter() + 1.5
    while time.perf_counter() < end:
        fg = winfind.foreground_window()
        hit = next((w for w in wins if w["hwnd"] == fg), None)
        if hit is not None:
            return hit
        time.sleep(0.05)
    cur = next((w for w in winfind.list_windows() if w["hwnd"] == fg), {})
    raise WrongWindow(f"{window!r} is not the foreground window (foreground is {cur.get('title') or fg!r}); "
                      "refusing to act blind — the user may be working in another window")


def observe(window: str | int | None = None, max_elements: int | None = None, interactive_only: bool = False):
    """Fresh UIA observation → stored Observation (with capture_id/fingerprint)."""
    t0 = time.perf_counter()
    w, els, meta = uia.snapshot(window, max_elements=max_elements or config.SETTINGS["observe_max_elements"],
                                interactive_only=interactive_only)
    obs = store.new(w, els, kind="uia", uia_ms=meta["uia_ms"], truncated=meta["truncated"])
    metrics.add("uia_ms", meta["uia_ms"])
    metrics.add("observe_ms", round((time.perf_counter() - t0) * 1000))
    return obs


def _norm_spec(target) -> dict:
    if target is None:
        return {}
    if isinstance(target, int):
        return {"id": target}
    if isinstance(target, str):
        s = target.strip()
        if s.isdigit():
            return {"id": int(s)}
        return {"name": s}
    if isinstance(target, (list, tuple)) and len(target) == 2:
        return {"x": int(target[0]), "y": int(target[1])}
    return dict(target)


def _ocr_candidates(obs, text: str) -> tuple[list[dict], list[dict]]:
    """OCR the target window (or its region) and return (matching pseudo-elements, all lines)."""
    hwnd = obs.window.get("hwnd") or winfind.foreground_window()
    t0 = time.perf_counter()
    try:
        img, rect, _ok = capture.grab_window(hwnd)
        origin = (rect["left"], rect["top"])
    except Exception:
        img, rect = capture.grab_virtual()
        origin = (rect["left"], rect["top"])
    r = ocr_boxes.ocr_boxes(img, origin=origin)
    metrics.add("ocr_ms", round((time.perf_counter() - t0) * 1000))
    start = (max([e["id"] for e in obs.elements]) if obs.elements else 0) + 1000
    els = ocr_boxes.ocr_elements(r["lines"], start_id=start)
    q = text.lower()
    hits = [e for e in els if q in e["name"].lower()]
    hits.sort(key=lambda e: (0 if e["name"].lower() == q else 1, len(e["name"])))
    return hits, els


def resolve_target(target, goal: str | None = None, obs=None, allow_jev: bool = True, allow_ocr: bool = True,
                   window: str | int | None = None) -> tuple[dict | None, str, dict]:
    """→ (element_or_point, tier, info). Raises NeedsVision / StaleStateError."""
    spec = _norm_spec(target)
    info: dict = {}
    if "x" in spec and "y" in spec:
        return {"point": [int(spec["x"]), int(spec["y"])]}, TIER_DETERMINISTIC, info
    if obs is None:
        obs = store.latest() if "id" in spec else observe(window)
    if "id" in spec:
        el = obs.element(int(spec["id"])) if obs else None
        if el is None:
            raise StaleStateError(getattr(obs, "capture_id", None), f"element id {spec['id']} not in observation; re-observe")
        return el, TIER_DETERMINISTIC, info

    name, role, aid = spec.get("name") or spec.get("text"), spec.get("role"), spec.get("automation_id")
    # --- UIA lookup -----------------------------------------------------------
    cands = uia.find_elements(obs.elements, name=name, role=role, automation_id=aid, exact=bool(spec.get("exact")))
    clickable = [e for e in cands if e.get("role") in uia.CLICKABLE_ROLES or e.get("patterns")]
    pool = clickable or cands
    if len(pool) == 1:
        return pool[0], TIER_UIA, {"candidates": 1}
    if len(pool) > 1:
        exact = [e for e in pool if (e.get("name") or "").lower() == (name or "").lower()]
        if len(exact) == 1:
            return exact[0], TIER_UIA, {"candidates": len(pool), "exact": True}
        best = _heuristic_pick(exact or pool, role)
        if best is not None:
            return best, TIER_UIA, {"candidates": len(pool), "heuristic": True}
        # --- future RL hook (inactive unless a policy is registered; never consulted for unique targets) --------
        from . import hooks
        picked = hooks.before_decide(goal or f"Choose the element matching '{name}'", pool[:60], {"window": obs.window})
        if picked is not None and obs.element(picked) is not None:
            return obs.element(picked), "policy", {"candidates": len(pool), "policy": True}
        # --- JEV: ambiguous structured candidates ------------------------------
        if allow_jev:
            try:
                from ..jev.adapter import enrich
                d = jev_engine().pick(goal or f"Choose the element matching '{name}'", enrich(pool[:60], obs.elements), obs.window)
                metrics.add("jev_ms", d.latency_ms)
                info.update({"jev": d.as_dict(), "candidates": len(pool)})
                if d.target_id is not None and d.min_confidence >= float(config.SETTINGS["jev_min_confidence"]):
                    return obs.element(d.target_id), TIER_JEV, info
                info["jev_low_confidence"] = True
            except JevUnavailable as e:
                info["jev_unavailable"] = str(e)
            except Exception as e:  # noqa: BLE001
                info["jev_error"] = str(e)
        raise NeedsVision(f"{len(pool)} candidates match {name!r}; JEV could not disambiguate", pool[:20], obs, info)

    # --- OCR: nothing structured matched -------------------------------------
    if allow_ocr and name:
        hits, all_lines = _ocr_candidates(obs, name)
        info["ocr_lines"] = len(all_lines)
        if len(hits) == 1:
            return hits[0], TIER_OCR, info
        if len(hits) > 1 and allow_jev:
            try:
                d = jev_engine().pick(goal or f"Choose the screen text matching '{name}'", hits[:40], obs.window)
                metrics.add("jev_ms", d.latency_ms)
                info["jev"] = d.as_dict()
                if d.target_id is not None and d.min_confidence >= float(config.SETTINGS["jev_min_confidence"]):
                    return next(e for e in hits if e["id"] == d.target_id), TIER_OCR, info
            except JevUnavailable as e:
                info["jev_unavailable"] = str(e)
            except Exception as e:  # noqa: BLE001
                info["jev_error"] = str(e)
        if len(hits) > 1:
            return hits[0], TIER_OCR, {**info, "note": "first OCR hit used"}
    raise NeedsVision(f"no UIA element or screen text matched {name!r}", obs.elements[:30], obs)


_ROLE_PRIORITY = {"button": 0, "menuitem": 0, "tab": 0, "link": 1, "listitem": 1, "treeitem": 1, "checkbox": 1,
                  "radio": 1, "combobox": 2, "edit": 2, "dataitem": 2, "header": 3, "image": 4, "text": 5}


def _same_box(a, b, tol: int = 4) -> bool:
    """True if boxes are (nearly) identical or one is a text label inside the other."""
    if not a or not b:
        return False
    if all(abs(a[i] - b[i]) <= tol for i in range(4)):
        return True
    inner, outer = (a, b) if (a[2] - a[0]) * (a[3] - a[1]) <= (b[2] - b[0]) * (b[3] - b[1]) else (b, a)
    return inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol


def _heuristic_pick(cands: list[dict], role: str | None) -> dict | None:
    """Deterministic tie-breaker for same-name candidates: prefer actionable roles/patterns; unique winner only."""
    if role:
        return None
    # Collapse nested wrappers: candidates sharing (or fully containing) the same bounds are one control.
    distinct = []
    for e in cands:
        if not any(_same_box(e["bounds"], d["bounds"]) for d in distinct):
            distinct.append(e)
    if len(distinct) == 1:
        cands = [e for e in cands if _same_box(e["bounds"], distinct[0]["bounds"])]
    scored = []
    for e in cands:
        pats = set(e.get("patterns") or [])
        score = _ROLE_PRIORITY.get(e.get("role"), 6) * 10
        score -= 5 if ({"Invoke", "ExpandCollapse", "SelectionItem", "Toggle"} & pats) else 0
        scored.append((score, e))
    scored.sort(key=lambda t: t[0])
    if len(scored) == 1 or (len(scored) >= 2 and scored[0][0] < scored[1][0]):
        return scored[0][1]
    # same score: if they all overlap the same box, it is one control → take the first
    if all(_same_box(scored[0][1]["bounds"], e["bounds"]) for _s, e in scored[1:]):
        return scored[0][1]
    return None


def _click_point(x: int, y: int, button: str = "left", count: int = 1):
    t0 = time.perf_counter()
    winput.click(button, count, x, y)
    metrics.add("input_ms", round((time.perf_counter() - t0) * 1000))


def execute_click(target, capture_id: int | None = None, button: str = "left", count: int = 1, goal: str | None = None,
                  window: str | int | None = None, verify_change: bool = True, action: str = "auto") -> dict:
    """Route + execute a click/invoke with stale-state protection, policy and verification."""
    t_all = time.perf_counter()
    obs = None
    if capture_id is not None:
        obs = store.check_fresh(capture_id)
    el, tier, info = resolve_target(target, goal=goal, obs=obs, window=window)
    obs = obs or store.latest()
    win = obs.window if obs else {}
    # stale check against live UI when we act on a stored element (not a raw point)
    # A fresh observation (< FRESH_S old) is trusted as-is; older ones are re-checked against the live UI.
    if el and "point" not in el and el.get("role") != "ocr" and obs is not None and (time.time() - obs.ts) > FRESH_S:
        live_fp = verify.snapshot_fingerprint(win.get("hwnd"))
        if live_fp != obs.fingerprint:
            fresh = observe(win.get("hwnd"))
            # try to re-resolve the same element in the fresh state by role+name
            again = uia.find_elements(fresh.elements, name=el.get("name"), role=el.get("role"), exact=True)
            if len(again) == 1 and again[0]["bounds"] == el["bounds"]:
                el, obs, info = again[0], fresh, {**info, "refreshed": True}
            elif capture_id is None and "id" not in _norm_spec(target):
                # PRD §7: re-observe → regenerate candidates → re-decide. Done locally for name-based targets
                # (Claude's intent is unchanged); pinned ids/capture_ids are bounced back so Claude re-observes.
                el, tier, info = resolve_target(target, goal=goal, obs=fresh, window=window)
                obs, info = fresh, {**info, "re_decided": True}
            else:
                raise StaleStateError(obs.capture_id, "UI changed since observation; re-observe", fresh)
    v = policy.classify_click(el if "point" not in el else None, win)
    if v.level == policy.BLOCKED:
        raise Blocked(f"blocked by security policy: {v.reason}")
    if v.level == policy.CONFIRM and not confirm.ask(f"Click '{el.get('name')}'", v.reason):
        return {"ok": False, "denied": True, "reason": v.reason, "tier": tier}
    before_fp = obs.fingerprint if obs else None
    if "point" in el:
        # A pixel click goes to whatever is on top: when the caller named a window, make sure that is the one.
        w = ensure_window(window)
        if w is not None and w["hwnd"] != win.get("hwnd"):
            win = w  # verify against the named window, not whatever the last observation happened to be
            before_fp = verify.snapshot_fingerprint(w["hwnd"])
        x, y = el["point"]
        _click_point(x, y, button, count)
        method = {"method": "click@point", "point": [x, y]}
    elif el.get("role") == "ocr" or button != "left" or count != 1:
        x, y = uia.center(el)
        _click_point(x, y, button, count)
        method = {"method": "click@center", "point": [x, y]}
    else:
        t0 = time.perf_counter()
        ctrl = uia.find_control(win.get("hwnd"), el) if win.get("hwnd") else None
        if ctrl is None:
            x, y = uia.center(el)
            _click_point(x, y, button, count)
            method = {"method": "click@center(no-ctrl)", "point": [x, y]}
        else:
            method = uia.act_on(ctrl, el, action=action, foreground_hwnd=win.get("hwnd"))
        metrics.add("input_ms", round((time.perf_counter() - t0) * 1000))
    out = {"ok": True, "tier": tier, "target": {k: el.get(k) for k in ("id", "role", "name", "bounds") if k in el}, **method, **info}
    if verify_change and before_fp:
        t0 = time.perf_counter()
        out["verification"] = verify.state_changed(before_fp, win.get("hwnd"), timeout=1.5)
        metrics.add("verification_ms", round((time.perf_counter() - t0) * 1000))
    from . import hooks
    hooks.after_outcome(goal or "", el if "point" not in el else None, tier,
                        bool(out.get("verification", {}).get("verified", True)),
                        {"window": win, "retried": out.get("retried", False), "denied": out.get("denied", False),
                         "latency_ms": out.get("total_ms"), "method": out.get("method")})
    out["total_ms"] = round((time.perf_counter() - t_all) * 1000)
    return out


def _type_via(text: str, use_clipboard: bool) -> str:
    if use_clipboard:
        from ..computer.engine import clipboard
        old = clipboard.get_text()
        if clipboard.set_text(text):
            winput.press("ctrl+v")
            time.sleep(0.03 + min(0.2, len(text) / 5000))
            if old is not None:
                try:
                    clipboard.set_text(old, verify=False)
                except Exception:  # noqa: BLE001
                    pass
            return "paste"
    winput.type_text(text, chunk=1, interval=TYPE_INTERVAL)
    return "SendInput"


def execute_type(text: str, target=None, capture_id: int | None = None, clear: bool = False, enter: bool = False,
                 verify_value: bool = True, window: str | int | None = None, use_clipboard: bool | None = None) -> dict:
    t_all = time.perf_counter()
    el, tier, info, win = None, TIER_DETERMINISTIC, {}, {}
    if target is not None:
        obs = store.check_fresh(capture_id) if capture_id is not None else None
        el, tier, info = resolve_target(target, goal=f"type into the field for: {text[:40]}", obs=obs, window=window)
        obs = obs or store.latest()
        win = obs.window if obs else {}
        v = policy.classify_text_input(text, el.get("name"))
        if v.level == policy.CONFIRM and not confirm.ask(f"Type into '{el.get('name')}'", v.reason, remember_key="type:" + str(el.get("name"))):
            return {"ok": False, "denied": True, "reason": v.reason}
        if "point" in el:
            named = ensure_window(window)
            if named is not None:
                win = named
            _click_point(*el["point"])
        else:
            ctrl = uia.find_control(win.get("hwnd"), el) if win.get("hwnd") else None
            if ctrl is not None:
                try:
                    ctrl.SetFocus()
                except Exception:
                    _click_point(*uia.center(el))
            else:
                _click_point(*uia.center(el))
    else:
        # No target → text goes to the focused control. If the caller named a window, it must be the foreground one;
        # otherwise the keystrokes land in whatever the user switched to (WrongWindow instead of a blind paste).
        named = ensure_window(window)
        hwnd = named["hwnd"] if named else winfind.foreground_window()
        win = named or next((w for w in winfind.list_windows() if w["hwnd"] == hwnd), {"hwnd": hwnd})
        v = policy.classify_window(win.get("title"), win.get("process"))
        if v.level == policy.BLOCKED:
            raise Blocked(f"blocked by security policy: {v.reason}")
    t0 = time.perf_counter()
    if clear:
        winput.press("ctrl+a")
    # Paste-first: atomic, immune to WinUI key-repeat/ordering bugs and fast for long text. Verify when we can
    # read the field back; on mismatch fall back to paced SendInput (which handles fields that reject paste).
    if use_clipboard is None:
        use_clipboard = len(text) >= 4 or "\n" in text
    method = _type_via(text, use_clipboard)
    if enter:
        winput.tap("enter")
    metrics.add("input_ms", round((time.perf_counter() - t0) * 1000))
    out = {"ok": True, "tier": tier, "chars": len(text), "method": method, **info}
    if el and "point" not in el:
        out["target"] = {k: el.get(k) for k in ("id", "role", "name")}
    can_verify = verify_value and el and el.get("role") in ("edit", "combobox", "document") and win.get("hwnd") and not enter
    if can_verify:
        t0 = time.perf_counter()
        ver = verify.value(el, win["hwnd"], text)
        if not ver["verified"] and "value" in ver and use_clipboard:
            # retry with paced SendInput after restoring the field
            if clear:
                winput.press("ctrl+a")
            else:
                for _ in range(len(text)):
                    winput.tap("backspace")
            method = _type_via(text, False)
            ver = verify.value(el, win["hwnd"], text)
            out["method"] = method
            out["retried"] = True
        out["verification"] = ver
        out["ok"] = bool(ver["verified"]) if "value" in ver else True
        metrics.add("verification_ms", round((time.perf_counter() - t0) * 1000))
    out["total_ms"] = round((time.perf_counter() - t_all) * 1000)
    return out
