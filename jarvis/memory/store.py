"""Persistent local experience memory under D:\\JARVIS\\data\\memory\\.

Kinds (one JSONL file each, plus preferences.json):
  preferences   user preferences ("open PDFs in Edge", "AI research folder is D:\\Research")
  workflows     successful step sequences keyed by goal + app (from computer_run / computer_act)
  corrections   things the user corrected ("no, I meant the Downloads *folder*")
  failures      what failed and why (so the same dead end is not repeated)
  app_patterns  per-application interaction knowledge ("Notepad: File menu is a menuitem; Save-As filename edit is 'File name:'")
  routing       learned routing heuristics per app (which tier resolved which target; JEV confidence outcomes)

Everything written is passed through the secret redactor; credential/payment field values are never stored; raw
screenshots/audio/full document text are never stored — only names, roles, goals and outcomes.
Recall is a cheap token-overlap ranking (no model call); the top few entries are injected as a compact <memory> block.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from .. import config
from ..safety import policy

log = logging.getLogger("jarvis.memory")

MEMORY_DIR = config.DATA / "memory"
KINDS = ("workflows", "corrections", "failures", "app_patterns", "routing")
MAX_ENTRIES = {"workflows": 400, "corrections": 200, "failures": 300, "app_patterns": 300, "routing": 500}
_SENSITIVE_FIELD = re.compile(r"(?i)password|passcode|\bpin\b|otp|verification code|cvv|card number|secret|token|api.?key")
_lock = threading.Lock()


def _path(kind: str) -> Path:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_DIR / (f"{kind}.jsonl" if kind != "preferences" else "preferences.json")


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (s or "").lower()) if t not in _STOP}


_STOP = {"the", "and", "for", "with", "into", "then", "that", "this", "open", "file", "click", "type", "window", "please"}


def scrub(obj):
    """Redact secrets recursively; drop values of sensitive fields; cap string sizes."""
    if isinstance(obj, str):
        return policy.redact(obj)[:400]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("rid", "bounds", "image", "data", "audio", "screenshot"):
                continue
            if isinstance(v, str) and _SENSITIVE_FIELD.search(str(k)):
                out[k] = "***"
            elif k in ("value", "text", "content") and isinstance(v, str) and _SENSITIVE_FIELD.search(str(obj.get("name", ""))):
                out[k] = "***"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(obj, list):
        return [scrub(x) for x in obj[:40]]
    return obj


# ----------------------------------------------------------------------------- write
def remember(kind: str, entry: dict) -> dict:
    if kind == "preferences":
        return set_preference(entry.get("key") or entry.get("name") or "note", entry.get("value") or entry.get("text"))
    if kind not in KINDS:
        raise ValueError(f"unknown memory kind {kind!r}; use one of {KINDS + ('preferences',)}")
    e = scrub(dict(entry))
    e["ts"] = time.time()
    e.setdefault("app", "")
    e["_tokens"] = sorted(_tokens(" ".join(str(e.get(k, "")) for k in ("goal", "app", "note", "pattern", "target", "lesson", "text"))))
    p = _path(kind)
    with _lock:
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        # dedupe: same goal+app+note replaces the older entry
        sig = (e.get("goal", ""), e.get("app", ""), e.get("note", e.get("pattern", e.get("lesson", ""))))
        kept = []
        for ln in lines:
            try:
                d = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if (d.get("goal", ""), d.get("app", ""), d.get("note", d.get("pattern", d.get("lesson", "")))) == sig:
                e["uses"] = d.get("uses", 0) + 1
                continue
            kept.append(ln)
        kept.append(json.dumps(e, ensure_ascii=False))
        kept = kept[-MAX_ENTRIES[kind]:]
        p.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return {"ok": True, "kind": kind, "entries": len(kept)}


def set_preference(key: str, value) -> dict:
    p = _path("preferences")
    with _lock:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        d[policy.redact(str(key))[:80]] = scrub(value)
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "preferences": len(d)}


def preferences() -> dict:
    p = _path("preferences")
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def forget(kind: str, match: str) -> dict:
    if kind == "preferences":
        p = _path("preferences")
        d = preferences()
        n = len(d)
        d = {k: v for k, v in d.items() if match.lower() not in k.lower()}
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "removed": n - len(d)}
    p = _path(kind)
    if not p.exists():
        return {"ok": True, "removed": 0}
    lines = p.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if match.lower() not in ln.lower()]
    p.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return {"ok": True, "removed": len(lines) - len(kept)}


# ----------------------------------------------------------------------------- read
def _load(kind: str) -> list[dict]:
    p = _path(kind)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out


def recall(query: str, app: str | None = None, kinds=KINDS, limit: int = 6) -> list[dict]:
    q = _tokens(query)
    if app:
        q |= _tokens(app)
    scored = []
    for kind in kinds:
        for e in _load(kind):
            et = set(e.get("_tokens") or [])
            overlap = len(q & et)
            if app and e.get("app") and app.lower() in str(e["app"]).lower():
                overlap += 2
            if overlap <= 0:
                continue
            recency = 1.0 / (1 + (time.time() - e.get("ts", 0)) / 86400 / 30)
            score = overlap + 0.5 * min(3, e.get("uses", 0)) + recency + (1 if kind == "corrections" else 0)
            scored.append((score, kind, e))
    scored.sort(key=lambda t: -t[0])
    return [{"kind": k, **{kk: vv for kk, vv in e.items() if kk != "_tokens"}} for _s, k, e in scored[:limit]]


def brief(query: str, app: str | None = None, max_chars: int = 700) -> str:
    """Compact text block for injection into a Claude prompt (empty string when nothing relevant)."""
    parts = []
    prefs = preferences()
    if prefs:
        parts.append("preferences: " + "; ".join(f"{k}={v}" for k, v in list(prefs.items())[:8]))
    for e in recall(query, app):
        if e["kind"] == "workflows":
            parts.append(f"workflow[{e.get('app', '')}] {e.get('goal', '')}: {e.get('steps_brief', '')}")
        elif e["kind"] == "corrections":
            parts.append(f"correction: {e.get('note', '')}")
        elif e["kind"] == "failures":
            parts.append(f"avoid[{e.get('app', '')}]: {e.get('lesson', e.get('note', ''))}")
        elif e["kind"] == "app_patterns":
            parts.append(f"{e.get('app', '')}: {e.get('pattern', '')}")
        elif e["kind"] == "routing":
            parts.append(f"routing[{e.get('app', '')}]: {e.get('note', '')}")
    s = "\n".join(parts)
    return s[:max_chars]


# ----------------------------------------------------------------------------- automatic learning hooks
def learn_batch(goal: str | None, app: str | None, result: dict, steps: list[dict]) -> None:
    """Called by computer_run: store a successful sequence or the failure lesson."""
    try:
        app = app or _guess_app(result)
        if result.get("status") == "done" and steps:
            brief_steps = "; ".join(_step_brief(s) for s in steps[:12])
            remember("workflows", {"goal": goal or "batch", "app": app, "steps_brief": brief_steps, "steps": scrub(steps),
                                   "total_ms": result.get("total_ms")})
        elif result.get("trace"):
            last = result["trace"][-1]
            remember("failures", {"goal": goal or "batch", "app": app, "step": last.get("action"), "arg": last.get("arg"),
                                  "lesson": f"step {last.get('step')} {last.get('action')} {last.get('arg')} failed: {last.get('error') or last.get('detail') or last.get('expect')}"[:300]})
    except Exception as e:  # noqa: BLE001
        log.debug("learn_batch failed: %s", e)


def learn_routing(app: str, target: str, tier: str, extra: dict | None = None) -> None:
    try:
        if tier in ("jev", "ocr") or (extra or {}).get("heuristic"):
            remember("routing", {"app": app, "target": target, "tier": tier,
                                 "note": f"'{target}' resolved via {tier}" + (f" ({extra.get('candidates')} candidates)" if extra and extra.get("candidates") else "")})
    except Exception as e:  # noqa: BLE001
        log.debug("learn_routing failed: %s", e)


def _guess_app(result: dict) -> str:
    for rec in result.get("trace") or []:
        if rec.get("window"):
            return str(rec["window"]).split(" - ")[-1][:40]
    obs = result.get("observation") or {}
    return str((obs.get("active_window") or {}).get("process") or "")[:40]


def _step_brief(s: dict) -> str:
    for k in ("app", "key", "type", "click", "element", "window_action", "scroll", "wait", "file", "shell", "act", "expect"):
        if k in s:
            v = s[k]
            if isinstance(v, dict):
                v = v.get("name") or v.get("action") or v.get("goal") or json.dumps(v)[:30]
            return f"{k}:{policy.redact(str(v))[:30]}"
    return "?"
