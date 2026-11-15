"""Local notifications (OpenMuse integration §21, §39).

Durable, local, deduplicated in-app notifications for the events worth surfacing: a task completed/failed, a watch
triggered, human input/confirmation required. NOT intrusive desktop popups (§39) and NOT device push (that is roadmap
in OpenMuse — not faked here): the orb signals subtly and the HUD lists them. Deduplication suppresses repeated
identical alerts within a window (§20/§21). Stored in SQLite alongside tasks.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid

from .. import config
from .events import EventType, bus

DEDUP_WINDOW_S = 3600.0    # an identical (kind, dedup_key) within this window is suppressed


class Notifications:
    def __init__(self, path=None, event_bus=None):
        self.path = str(path) if path else str(config.DATA / "tasks" / "notifications.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.bus = event_bus or bus()
        self._lock = threading.RLock()
        with self._lock, self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, kind TEXT, title TEXT, body TEXT, dedup_key TEXT,
                read INTEGER DEFAULT 0, ts REAL)""")

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def add(self, kind: str, title: str, body: str | None = None, dedup_key: str | None = None) -> dict | None:
        """Record a notification; returns it, or None if deduplicated. Emits a NOTIFICATION event for the orb/HUD."""
        now = time.time()
        key = dedup_key or f"{kind}:{title}"
        with self._lock, self._conn() as c:
            recent = c.execute("SELECT ts FROM notifications WHERE kind=? AND dedup_key=? ORDER BY ts DESC LIMIT 1",
                               (kind, key)).fetchone()
            if recent and now - recent["ts"] < DEDUP_WINDOW_S:
                return None                         # identical alert within the window → suppressed
            nid = uuid.uuid4().hex[:12]
            c.execute("INSERT INTO notifications (id, kind, title, body, dedup_key, read, ts) VALUES (?,?,?,?,?,0,?)",
                      (nid, kind, title, body, key, now))
        n = {"id": nid, "kind": kind, "title": title, "body": body, "ts": now, "read": False}
        self.bus.emit(EventType.NOTIFICATION, **n)
        return n

    def list(self, unread_only: bool = False, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM notifications" + (" WHERE read=0" if unread_only else "") + " ORDER BY ts DESC LIMIT ?"
        with self._lock, self._conn() as c:
            return [dict(r) for r in c.execute(q, (limit,)).fetchall()]

    def unread_count(self) -> int:
        with self._lock, self._conn() as c:
            return c.execute("SELECT COUNT(*) n FROM notifications WHERE read=0").fetchone()["n"]

    def mark_read(self, notification_id: str | None = None):
        with self._lock, self._conn() as c:
            if notification_id:
                c.execute("UPDATE notifications SET read=1 WHERE id=?", (notification_id,))
            else:
                c.execute("UPDATE notifications SET read=1")


_notify: Notifications | None = None


def notifications() -> Notifications:
    global _notify
    if _notify is None:
        _notify = Notifications()
    return _notify
