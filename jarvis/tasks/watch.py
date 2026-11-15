"""Recurring watches (OpenMuse integration §20, §56).

Watch a source for a condition and notify when it triggers — "tell me when this page shows a result", "when this file
changes", "when this text appears". Local file/text conditions are first-class (offline, safe, testable, §56); a public
URL source is supported read-only. Bounded backoff on repeated failure; duplicate alerts for the same state are
suppressed via the notifications store. **Watch only** — never performs purchases/payments/reservations (§20).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass

from .. import config
from .events import EventType, bus
from .notify import notifications

MAX_FAILURES = 8
BACKOFF_CAP = 16          # next_check grows by interval * min(2**failures, cap)


@dataclass
class Watch:
    id: str
    name: str
    kind: str                 # file_change | text_present | text_change
    source: str               # file path or public URL
    target: str = ""          # text to look for (text_present)
    interval_s: float = 300.0
    last_state: str = ""
    status: str = "active"    # active | triggered | paused | error | cancelled
    failure_count: int = 0
    next_check: float = 0.0
    last_notified_state: str = ""
    created_at: float = 0.0


def _read_source(w: Watch) -> str:
    """Return the current textual content of the source (local file or public URL). Raises on error."""
    if w.source.startswith(("http://", "https://")):
        import urllib.request
        with urllib.request.urlopen(w.source, timeout=10) as r:   # read-only GET; public pages only
            return r.read(1_000_000).decode("utf-8", "ignore")
    with open(w.source, encoding="utf-8", errors="ignore") as f:
        return f.read()


def evaluate(w: Watch) -> tuple[bool, str, str]:
    """(triggered, new_state, note). Pure w.r.t. the watch object — the manager persists the returned state."""
    content = _read_source(w)
    if w.kind == "text_present":
        present = w.target.lower() in content.lower()
        state = "present" if present else "absent"
        return (present and w.last_state != "present"), state, (f"'{w.target}' appeared" if present else "")
    digest = hashlib.sha1(content.encode("utf-8", "ignore")).hexdigest()
    if w.kind == "file_change" or w.kind == "text_change":
        changed = bool(w.last_state) and w.last_state != digest
        return changed, digest, ("source changed" if changed else "")
    raise ValueError(f"unknown watch kind: {w.kind}")


class WatchManager:
    def __init__(self, path=None, event_bus=None, notifier=None):
        self.path = str(path) if path else str(config.DATA / "tasks" / "watches.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.bus = event_bus or bus()
        self.notify = notifier if notifier is not None else notifications()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        with self._lock, self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS watches (
                id TEXT PRIMARY KEY, name TEXT, kind TEXT, source TEXT, target TEXT, interval_s REAL,
                last_state TEXT, status TEXT, failure_count INTEGER, next_check REAL, last_notified_state TEXT,
                created_at REAL)""")

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # -- CRUD ----------------------------------------------------------------------
    def add(self, name: str, kind: str, source: str, *, target="", interval_s=300.0) -> Watch:
        w = Watch(id=uuid.uuid4().hex[:12], name=name, kind=kind, source=source, target=target,
                  interval_s=interval_s, next_check=time.time(), created_at=time.time())
        with self._lock, self._conn() as c:
            c.execute("""INSERT INTO watches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (w.id, w.name, w.kind, w.source, w.target, w.interval_s, w.last_state, w.status,
                       w.failure_count, w.next_check, w.last_notified_state, w.created_at))
        return w

    def _get(self, wid) -> Watch | None:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM watches WHERE id=?", (wid,)).fetchone()
        return Watch(**dict(r)) if r else None

    def list(self, active_only=False) -> list[Watch]:
        q = "SELECT * FROM watches" + (" WHERE status='active'" if active_only else "") + " ORDER BY created_at DESC"
        with self._lock, self._conn() as c:
            return [Watch(**dict(r)) for r in c.execute(q).fetchall()]

    def _save(self, w: Watch):
        with self._lock, self._conn() as c:
            c.execute("""UPDATE watches SET last_state=?, status=?, failure_count=?, next_check=?,
                         last_notified_state=? WHERE id=?""",
                      (w.last_state, w.status, w.failure_count, w.next_check, w.last_notified_state, w.id))

    def cancel(self, wid: str):
        w = self._get(wid)
        if w:
            w.status = "cancelled"
            self._save(w)

    # -- checking ------------------------------------------------------------------
    def check(self, w: Watch, now: float | None = None) -> bool:
        """Evaluate one watch, persist state, notify on trigger (deduped), apply bounded backoff on error. Returns
        True if it triggered a NEW notification."""
        now = now or time.time()
        try:
            triggered, state, note = evaluate(w)
            w.failure_count = 0
            prev, w.last_state = w.last_state, state
            w.next_check = now + w.interval_s
            fired = False
            if triggered and w.last_notified_state != state:
                w.last_notified_state = state
                w.status = "triggered"
                self.bus.emit(EventType.WATCH_TRIGGERED, watch_id=w.id, name=w.name, note=note)
                n = self.notify.add("watch_triggered", f"Watch: {w.name}", note or "condition met",
                                    dedup_key=f"watch:{w.id}:{state}")
                fired = n is not None
            else:
                w.status = "active"
            self._save(w)
            return fired
        except Exception as e:  # noqa: BLE001 — bounded backoff, never crash the scheduler
            w.failure_count += 1
            w.status = "error" if w.failure_count < MAX_FAILURES else "paused"
            w.next_check = now + w.interval_s * min(2 ** w.failure_count, BACKOFF_CAP)
            self._save(w)
            return False

    def tick(self, now: float | None = None) -> int:
        """Check every active watch whose next_check is due. Returns the number of NEW notifications fired."""
        now = now or time.time()
        fired = 0
        for w in self.list(active_only=False):
            if w.status in ("cancelled", "paused"):
                continue
            if w.next_check <= now:
                fired += int(self.check(w, now))
        return fired

    # -- background scheduler ------------------------------------------------------
    def start(self, period_s: float = 30.0):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, args=(period_s,), name="watches", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self, period_s):
        while not self._stop.wait(period_s):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                pass


_watch: WatchManager | None = None


def watches() -> WatchManager:
    global _watch
    if _watch is None:
        _watch = WatchManager()
    return _watch
