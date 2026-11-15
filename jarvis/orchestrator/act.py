"""The local micro-loop: Claude hands over a *meaningful* UI goal, JARVIS runs

    observe → candidates → (deterministic | UIA-unique | JEV) → execute → verify → repeat

until JEV says DONE/BLOCKED, the step budget is exhausted, or nothing changes.
Claude is not consulted per click. Every step is stale-state protected.
"""
from __future__ import annotations

import logging
import time

from .. import config
from ..computer import uia
from ..computer.engine import input as winput
from ..jev import JevUnavailable, engine as jev_engine
from ..safety import confirm, policy
from ..state import StaleStateError
from . import router, verify
from .metrics import metrics

log = logging.getLogger("jarvis.act")


def run(goal: str, window: str | int | None = None, max_steps: int | None = None, text_values: dict | None = None,
        done_when: dict | None = None) -> dict:
    """Execute `goal` on the target window with the JEV reflex loop.

    text_values: {"<field name substring>": "value"} — Claude supplies any text to type (JEV never invents text).
    done_when:   optional verifier {"element": name} | {"text": str} | {"window": title} | {"window_gone": title} | {"file": path}
    """
    t_all = time.perf_counter()
    steps: list[dict] = []
    history: list[dict] = []
    max_steps = int(max_steps or config.SETTINGS["act_max_steps"])
    jev = jev_engine()
    status = "max_steps"
    last_fp = None
    unchanged = 0
    for i in range(max_steps):
        try:
            obs = router.observe(window, interactive_only=True)
        except LookupError as e:
            # the target window is gone — that may be exactly the goal (dialog dismissed, app closed)
            if done_when and _check_done(done_when, None):
                status = "done"
            else:
                status = f"blocked: {e}"
            break
        v = policy.classify_window(obs.window.get("title"), obs.window.get("process"))
        if v.level == policy.BLOCKED:
            status = f"human: {v.reason}"
            break
        if done_when and _check_done(done_when, obs):
            status = "done"
            break
        if obs.fingerprint == last_fp:
            unchanged += 1
            if unchanged >= 2:
                status = "stalled: UI unchanged after action"
                break
        else:
            unchanged = 0
        last_fp = obs.fingerprint
        cands = [e for e in obs.elements if e.get("enabled", True)][:80]
        if not cands:
            status = "blocked: no interactive elements"
            break
        step = {"step": i + 1, "capture_id": obs.capture_id, "window": obs.window.get("title")}
        try:
            d = jev.choose(goal, cands, obs.window, history=history)
        except JevUnavailable as e:
            status = f"jev_unavailable: {e}"
            step["error"] = str(e)
            steps.append(step)
            break
        except Exception as e:  # noqa: BLE001
            status = f"jev_error: {e}"
            step["error"] = str(e)
            steps.append(step)
            break
        metrics.add("jev_ms", d.latency_ms)
        metrics.tier("jev")
        step["decision"] = d.as_dict()
        op = d.operation
        if op == "DONE":
            status = "done"
            steps.append(step)
            break
        if op == "BLOCKED":
            status = "blocked: JEV found no useful target"
            steps.append(step)
            break
        if d.min_confidence < float(config.SETTINGS["jev_min_confidence"]):
            status = f"low_confidence: {d.min_confidence:.2f} on {op}"
            step["note"] = "needs Claude"
            steps.append(step)
            break
        try:
            if op == "WAIT":
                time.sleep(0.4)
                step["action"] = "wait"
            elif op in ("SCROLL_DOWN", "SCROLL_UP"):
                el = cands[0]
                x, y = uia.center(el)
                winput.move(x, y)
                winput.scroll(-3 if op == "SCROLL_DOWN" else 3)
                step["action"] = op.lower()
            elif op == "CLICK":
                r = router.execute_click({"id": d.target_id}, capture_id=obs.capture_id, goal=goal, window=obs.window.get("hwnd"))
                step["action"] = "click"
                step["result"] = {k: r.get(k) for k in ("ok", "tier", "target", "method", "verification", "denied")}
                if r.get("denied"):
                    status = "denied by user"
                    steps.append(step)
                    break
            elif op == "TYPE_TEXT":
                el = obs.element(d.target_id)
                value = _text_for(el, text_values)
                if value is None:
                    status = f"needs_text: no value supplied for field '{el.get('name')}'"
                    step["action"] = "type_text"
                    step["field"] = el.get("name")
                    steps.append(step)
                    break
                r = router.execute_type(value, target={"id": d.target_id}, capture_id=obs.capture_id, clear=True, window=obs.window.get("hwnd"))
                step["action"] = "type_text"
                step["result"] = {k: r.get(k) for k in ("ok", "target", "verification", "denied")}
        except StaleStateError as e:
            step["stale"] = str(e)
            history.append({"action": op, "target": d.target_id, "result": "stale; re-observed"})
            steps.append(step)
            continue
        except router.Blocked as e:
            status = f"human: {e}"
            steps.append(step)
            break
        el = obs.element(d.target_id) if d.target_id else None
        history.append({"action": op, "target": (el or {}).get("name"), "role": (el or {}).get("role")})
        steps.append(step)
    out = {"status": status, "goal": goal, "steps": steps, "step_count": len(steps),
           "total_ms": round((time.perf_counter() - t_all) * 1000), "jev": jev.metrics.as_dict()}
    if done_when:
        try:
            out["done_check"] = _check_done(done_when, None, evaluate=True)
        except Exception as e:  # noqa: BLE001
            out["done_check"] = {"verified": status == "done", "note": str(e)[:120]}
    metrics.add("total_ms", out["total_ms"])
    return out


def _text_for(el: dict, text_values: dict | None) -> str | None:
    if not text_values:
        return None
    name = (el.get("name") or "").lower()
    for k, v in text_values.items():
        if k.lower() in name or name in k.lower() or k == "*":
            return str(v)
    if len(text_values) == 1:
        return str(next(iter(text_values.values())))
    return None


def _check_done(done_when: dict, obs, evaluate: bool = False):
    if "element" in done_when:
        r = verify.element(done_when["element"], timeout=0.1 if not evaluate else 3)
    elif "text" in done_when:
        r = verify.text(done_when["text"], timeout=0.1 if not evaluate else 3)
    elif "window" in done_when:
        r = verify.window(done_when["window"], timeout=0.1 if not evaluate else 3)
    elif "window_gone" in done_when:
        r = verify.window_gone(done_when["window_gone"], timeout=0.1 if not evaluate else 3)
    elif "file" in done_when:
        r = verify.file(done_when["file"], timeout=0.1 if not evaluate else 3)
    else:
        return {"verified": False, "error": "unknown done_when"} if evaluate else False
    return r if evaluate else bool(r.get("verified"))


_ = confirm
