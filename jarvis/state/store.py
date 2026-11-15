"""Observation store: every observation gets an identity so stale actions are refused.

An Observation carries:
  capture_id   monotonically increasing id
  ts           wall-clock timestamp
  window       identity of the active/target window (hwnd, title, process)
  fingerprint  sha1 over the compact element list (role|name|bounds) + window
  elements     the compact UIA element list (id → element dict)

Stale-state rule (PRD §7 / MASTER §13): an action referencing capture_id N is
allowed only if N is the latest observation AND the live fingerprint of the same
window still matches. Otherwise raise StaleStateError; the caller re-observes,
regenerates candidates and decides again.
"""
from __future__ import annotations

import hashlib
import itertools
import threading
import time
from dataclasses import dataclass, field


class StaleStateError(RuntimeError):
    def __init__(self, capture_id, reason: str, fresh: "Observation | None" = None):
        super().__init__(f"stale state (capture_id={capture_id}): {reason}")
        self.capture_id = capture_id
        self.reason = reason
        self.fresh = fresh


@dataclass
class Observation:
    capture_id: int
    ts: float
    window: dict                      # {hwnd, title, process, rect}
    elements: list[dict] = field(default_factory=list)
    fingerprint: str = ""
    kind: str = "uia"                 # uia | screen | ocr
    extra: dict = field(default_factory=dict)

    def element(self, eid: int) -> dict | None:
        for e in self.elements:
            if e["id"] == eid:
                return e
        return None

    def compact(self, limit: int | None = None) -> dict:
        els = self.elements if limit is None else self.elements[:limit]
        return {
            "capture_id": self.capture_id,
            "ts": round(self.ts, 3),
            "active_window": {k: self.window.get(k) for k in ("title", "process", "hwnd")},
            "fingerprint": self.fingerprint[:12],
            "kind": self.kind,
            "elements": els,
            "element_count": len(self.elements),
            **self.extra,
        }


def fingerprint_elements(window: dict, elements: list[dict]) -> str:
    h = hashlib.sha1()
    h.update(f"{window.get('hwnd')}|{window.get('title')}|".encode("utf-8", "replace"))
    for e in elements:
        h.update(f"{e.get('role')}|{e.get('name')}|{e.get('bounds')}|{e.get('enabled')}|{e.get('value','')}\n"
                 .encode("utf-8", "replace"))
    return h.hexdigest()


class StateStore:
    def __init__(self, keep: int = 8):
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._history: list[Observation] = []
        self._keep = keep

    def new(self, window: dict, elements: list[dict], kind: str = "uia", **extra) -> Observation:
        with self._lock:
            obs = Observation(
                capture_id=next(self._ids), ts=time.time(), window=window,
                elements=elements, fingerprint=fingerprint_elements(window, elements),
                kind=kind, extra=extra,
            )
            self._history.append(obs)
            del self._history[:-self._keep]
            return obs

    def latest(self) -> Observation | None:
        with self._lock:
            return self._history[-1] if self._history else None

    def get(self, capture_id: int) -> Observation | None:
        with self._lock:
            for o in reversed(self._history):
                if o.capture_id == capture_id:
                    return o
        return None

    def check_fresh(self, capture_id: int | None, live_fingerprint: str | None = None) -> Observation:
        """Return the observation if it is the latest and (optionally) still matches live state."""
        if capture_id is None:
            latest = self.latest()
            if latest is None:
                raise StaleStateError(None, "no observation yet; call computer.observe first")
            return latest
        obs = self.get(capture_id)
        if obs is None:
            raise StaleStateError(capture_id, "unknown capture_id (expired); re-observe")
        latest = self.latest()
        if latest is not None and latest.capture_id != capture_id:
            raise StaleStateError(capture_id, f"superseded by capture_id {latest.capture_id}; re-observe", latest)
        if live_fingerprint is not None and live_fingerprint != obs.fingerprint:
            raise StaleStateError(capture_id, "UI changed since observation (fingerprint mismatch); re-observe")
        return obs


store = StateStore()
