"""Local batch execution: run a sequence of deterministic / UIA steps with verification checkpoints in ONE Claude turn.

Claude hands over a plan like:

    [{"app": "notepad.exe", "wait_window": "Notepad"},
     {"type": "hello", "target": {"role": "document"}, "clear": true},
     {"key": "ctrl+s"},
     {"wait": "window", "query": "Save as"},
     {"type": "D:\\x\\demo.txt", "target": {"name": "File name:", "role": "edit"}, "clear": true},
     {"click": "Save", "expect": {"window_gone": "Save as"}},
     {"expect": {"file": "D:\\x\\demo.txt"}}]

Each step executes through the same router (deterministic → UIA → JEV-if-ambiguous → OCR); JEV is never called for
deterministic or uniquely resolved targets. Execution stops at the first failed step or failed `expect`, returning the
step trace plus a fresh compact observation so Claude can take over precisely where it stopped. Steps are the same
vocabulary as the individual tools, so nothing is learned twice.

Step forms (one action key per step, optional `expect`, `window`, `timeout`):
  app / key / type / click / element / window_action / scroll / wait / file / shell / act / observe / sleep_ms
Expect forms: {"window": q} {"window_gone": q} {"element": name, "role"?} {"text": s} {"file": path} {"title": s}
              {"value_contains": s, "target": ...}
Safety: each step goes through the normal policy (confirm/blocked). A blocked step stops the batch with human_required.
"""
from __future__ import annotations

import json
import logging
import time

from ..safety import policy
from ..state import StaleStateError
from . import router, verify, waits
from .metrics import metrics

log = logging.getLogger("jarvis.batch")

MAX_STEPS = 25
ACTION_KEYS = ("app", "key", "type", "click", "element", "window_action", "scroll", "wait", "file", "shell", "act",
               "observe", "sleep_ms", "expect")


def _expect(exp: dict, window=None, timeout: float = 8) -> dict:
    if "window" in exp:
        return verify.window(exp["window"], timeout)
    if "window_gone" in exp:
        return verify.window_gone(exp["window_gone"], timeout)
    if "element" in exp:
        return verify.element(exp["element"], exp.get("role"), exp.get("window", window), timeout)
    if "element_gone" in exp:
        return verify.element(exp["element_gone"], exp.get("role"), exp.get("window", window), timeout, absent=True)
    if "text" in exp:
        return verify.text(exp["text"], exp.get("window", window), timeout)
    if "file" in exp:
        return verify.file(exp["file"], timeout, content_contains=exp.get("contains"))
    if "file_gone" in exp:
        return verify.file(exp["file_gone"], timeout, absent=True)
    if "title" in exp:
        return verify.foreground(exp["title"], timeout)
    if "browser" in exp:
        return verify.browser_state(url_contains=exp["browser"], timeout=timeout)
    return {"verified": False, "error": f"unknown expect {list(exp)}"}


def run(steps: list[dict], tools, stop_on_fail: bool = True, window: str | None = None) -> dict:
    """`tools` is the server module (so steps reuse the exact tool implementations)."""
    t_all = time.perf_counter()
    trace: list[dict] = []
    status = "done"
    current_window = window
    steps = list(steps or [])[:MAX_STEPS]
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            trace.append({"step": i + 1, "error": "step must be an object"})
            status = "invalid_step"
            break
        step = dict(step)
        exp = step.pop("expect", None)
        timeout = float(step.pop("timeout", 8))
        win = step.pop("window", current_window)
        key = next((k for k in ACTION_KEYS if k in step), None)
        if key is None and exp is not None:  # a bare verification checkpoint
            step["expect"], exp, key = exp, None, "expect"
        rec: dict = {"step": i + 1, "action": key, "arg": _brief(step.get(key)) if key else None}
        t0 = time.perf_counter()
        try:
            r = _exec(key, step, tools, win, timeout)
            if key == "app" and isinstance(r, dict) and r.get("verification", {}).get("window"):
                current_window = r["verification"].get("hwnd") or current_window
                rec["window"] = r["verification"].get("window")
            rec["ok"] = bool(r.get("ok", not r.get("error"))) if isinstance(r, dict) else True
            for k in ("tier", "method", "error", "detail", "denied", "human_required", "verification", "retried", "text", "content", "stdout", "exit",
                      "capture_id", "active_window", "elements", "count", "matches", "entries", "lines"):
                if isinstance(r, dict) and k in r and r[k] not in (None, ""):
                    if k in ("stdout", "content", "text"):
                        rec[k] = str(r[k])[:1200]
                    elif k == "elements":  # observe / element-find results: compact element rows
                        rec[k] = [{kk: e.get(kk) for kk in ("id", "role", "name", "value", "enabled") if e.get(kk) not in (None, "", True)} | {"id": e.get("id")}
                                  for e in r[k][:40]]
                    elif k in ("matches", "entries", "lines"):
                        rec[k] = r[k][:60]
                    else:
                        rec[k] = r[k]
            if isinstance(r, dict) and r.get("error") in ("stale_state", "needs_vision"):
                rec["ok"] = False
                rec["candidates"] = r.get("candidates")
        except StaleStateError as e:
            rec.update(ok=False, error="stale_state", detail=str(e))
        except router.Blocked as e:
            rec.update(ok=False, error="blocked", detail=str(e), human_required=True)
        except router.WrongWindow as e:
            rec.update(ok=False, error="wrong_window", detail=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("batch step failed")
            rec.update(ok=False, error=type(e).__name__, detail=policy.redact(str(e))[:300])
        if rec.get("ok") and exp:
            v = _expect(exp, win, timeout)
            rec["expect"] = v
            rec["ok"] = bool(v.get("verified"))
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        trace.append(rec)
        if not rec.get("ok") and stop_on_fail:
            status = "human_required" if rec.get("human_required") else ("denied" if rec.get("denied") else "failed")
            break
    out = {"status": status, "steps_run": len(trace), "steps_total": len(steps), "trace": trace,
           "total_ms": round((time.perf_counter() - t_all) * 1000)}
    if status != "done":
        try:
            obs = router.observe(None, max_elements=40, interactive_only=True)
            out["observation"] = obs.compact(40)
            for e in out["observation"]["elements"]:
                e.pop("rid", None)
        except Exception:  # noqa: BLE001
            pass
    metrics.add("total_ms", out["total_ms"])
    return out


def _brief(v):
    s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
    return policy.redact(s)[:80]


def _exec(key: str | None, step: dict, t, win, timeout: float) -> dict:
    J = json.loads
    if key is None:
        return {"error": f"no action in step (keys: {list(step)})"}
    if key == "app":
        a = step["app"]
        if isinstance(a, dict):
            return J(t.computer_app(a.get("action", "launch"), a.get("name"), a.get("args"), a.get("cwd"), a.get("wait_window"), timeout, a.get("pid")))
        return J(t.computer_app("launch", a, step.get("args"), step.get("cwd"), step.get("wait_window"), timeout))
    if key == "key":
        return J(t.computer_key(step["key"], step.get("hold_ms", 0), step.get("repeat", 1), win))
    if key == "type":
        return J(t.computer_type(step["type"], step.get("target"), step.get("capture_id"), step.get("clear", False), step.get("enter", False), win))
    if key == "click":
        return J(t.computer_click(step["click"], step.get("capture_id"), step.get("button", "left"), step.get("double", False),
                                  step.get("goal"), win, step.get("action", "auto")))
    if key == "element":
        e = step["element"]
        return J(t.computer_element(e.get("action", "invoke"), e.get("name"), e.get("role"), e.get("automation_id"), e.get("id"),
                                    None, e.get("value"), win, timeout, e.get("exact", False)))
    if key == "window_action":
        w = step["window_action"]
        if isinstance(w, str):
            w = {"action": w}
        return J(t.computer_window(w.get("action", "focus"), w.get("query", win), w.get("x"), w.get("y"), w.get("width"), w.get("height"), timeout, w.get("monitor")))
    if key == "scroll":
        sc = step["scroll"]
        if isinstance(sc, str):
            sc = {"direction": sc}
        return J(t.computer_scroll(sc.get("direction", "down"), sc.get("amount", 3), sc.get("target"), sc.get("x"), sc.get("y")))
    if key == "wait":
        return J(t.computer_wait(step["wait"], step.get("query"), timeout, win, step.get("role"), step.get("absent", False)))
    if key == "file":
        f = step["file"]
        return J(t.computer_file(f["action"], f["path"], f.get("dest"), f.get("content"), f.get("pattern"), f.get("recursive", True), f.get("max_chars", 4000), f.get("append", False)))
    if key == "shell":
        sh = step["shell"]
        if isinstance(sh, str):
            sh = {"command": sh}
        return J(t.computer_shell(sh["command"], sh.get("shell", "powershell"), sh.get("cwd"), sh.get("timeout", 120), sh.get("expect_exit", 0)))
    if key == "act":
        a = step["act"]
        if isinstance(a, str):
            a = {"goal": a}
        return J(t.computer_act(a["goal"], a.get("window", win), a.get("max_steps", 8), a.get("text_values"), a.get("done_when")))
    if key == "observe":
        return J(t.computer_observe(win, step.get("max_elements", 40), True, False))
    if key == "sleep_ms":
        time.sleep(min(2000, int(step["sleep_ms"])) / 1000)  # bounded; prefer `wait`
        return {"ok": True}
    if key == "expect":
        v = _expect(step["expect"], win, timeout)
        return {"ok": bool(v.get("verified")), "verification": v}
    return {"error": f"unknown step {key}"}


_ = waits
