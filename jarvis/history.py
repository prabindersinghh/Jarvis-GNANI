"""Daily continuous conversation history — one conversation container per calendar date.

Storage: SQLite at D:\\JARVIS\\data\\memory\\conversations\\history.db (append-friendly, queryable).
The DATE is the primary container. Restarting JARVIS, reconnecting Claude, closing/reopening the HUD, or starting a
new voice/text interaction never creates a new conversation — they all append to today's date. Session boundaries are
MARKERS inside the day, not separate threads.

Tables
  days      (date PK, created_ts)
  sessions  (session_id PK, date, start_ts, end_ts, start_reason, end_reason)   -- markers within a day
  messages  (id PK, date, session_id, ts, role, content, source, msg_id, meta)  -- role in {user, jarvis}

A session auto-starts on the first interaction after idle and auto-ends after `idle_end_s` of no activity, on shutdown,
or explicitly. Content is passed through the privacy redactor before storage; secrets/credential values are never
stored. Reads are cheap (indexed by date); the full day is NEVER sent to Claude here — that stays the job of
jarvis.memory / the context policy.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from . import config

log = logging.getLogger("jarvis.history")

DB_DIR = config.DATA / "memory" / "conversations"
DB_PATH = DB_DIR / "history.db"
IDLE_END_S = 20 * 60          # a gap this long ends the current session; the next message opens a new marker
_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS days (date TEXT PRIMARY KEY, created_ts REAL);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, date TEXT, start_ts REAL, end_ts REAL,
    start_reason TEXT, end_reason TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, session_id TEXT, ts REAL,
    role TEXT, content TEXT, source TEXT, msg_id TEXT, meta TEXT);
CREATE INDEX IF NOT EXISTS ix_messages_date ON messages(date, id);
CREATE INDEX IF NOT EXISTS ix_sessions_date ON sessions(date, start_ts);
"""


def _today(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m-%d")


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


class History:
    def __init__(self, path: Path = DB_PATH, idle_end_s: int = IDLE_END_S):
        self.path = path
        self.idle_end_s = idle_end_s
        self.session_id: str | None = None
        self._last_ts = 0.0
        self._cur_date: str | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # -- redaction -----------------------------------------------------------------
    @staticmethod
    def _clean(text: str) -> str:
        try:
            from .safety import privacy
            return privacy.redact_text(str(text))
        except Exception:  # noqa: BLE001
            return str(text)

    def _ensure_day(self, d: str):
        self._db.execute("INSERT OR IGNORE INTO days(date, created_ts) VALUES (?, ?)", (d, time.time()))

    # -- sessions ------------------------------------------------------------------
    def start_session(self, reason: str = "startup", ts: float | None = None) -> str:
        """Open a session marker for today. If a session is already open for today and the last activity is recent,
        reuse it (a restart mid-day resumes the same session rather than fragmenting the day)."""
        with _lock:
            ts = ts if ts is not None else time.time()
            d = _today(ts)
            self._ensure_day(d)
            # reuse an open, recent session for today
            row = self._db.execute(
                "SELECT session_id, start_ts, end_ts FROM sessions WHERE date=? ORDER BY start_ts DESC LIMIT 1", (d,)
            ).fetchone()
            if row and row[2] is None and (ts - (self._last_ts or row[1])) < self.idle_end_s:
                self.session_id, self._cur_date, self._last_ts = row[0], d, ts
                return self.session_id
            # close any dangling open session first
            if row and row[2] is None:
                self._db.execute("UPDATE sessions SET end_ts=?, end_reason=? WHERE session_id=?",
                                 (self._last_ts or row[1], "gap", row[0]))
            sid = uuid.uuid4().hex[:12]
            self._db.execute("INSERT INTO sessions(session_id, date, start_ts, start_reason) VALUES (?, ?, ?, ?)",
                             (sid, d, ts, reason))
            self._db.commit()
            self.session_id, self._cur_date, self._last_ts = sid, d, ts
            log.info("history session %s started (%s) on %s", sid, reason, d)
            return sid

    def end_session(self, reason: str = "shutdown", ts: float | None = None):
        with _lock:
            if not self.session_id:
                return
            ts = ts if ts is not None else time.time()
            self._db.execute("UPDATE sessions SET end_ts=?, end_reason=? WHERE session_id=? AND end_ts IS NULL",
                             (ts, reason, self.session_id))
            self._db.commit()
            log.info("history session %s ended (%s)", self.session_id, reason)
            self.session_id = None

    def _rollover_and_session(self, ts: float) -> str:
        """Ensure there is an appropriate open session for `ts`, handling midnight rollover and idle gaps."""
        d = _today(ts)
        if self._cur_date and d != self._cur_date:
            self.end_session("day_rollover", ts=self._last_ts or ts)  # close yesterday
            return self.start_session("new_day", ts=ts)
        if not self.session_id:
            return self.start_session("resume", ts=ts)
        if (ts - self._last_ts) > self.idle_end_s:
            self.end_session("idle", ts=self._last_ts)
            return self.start_session("resume_after_idle", ts=ts)
        return self.session_id

    # -- messages ------------------------------------------------------------------
    def append(self, role: str, content: str, source: str = "text", meta: dict | None = None,
               ts: float | None = None) -> str:
        """Append a message to today's conversation. role in {'user','jarvis'}. Returns a msg_id."""
        with _lock:
            ts = ts if ts is not None else time.time()
            sid = self._rollover_and_session(ts)
            d = _today(ts)
            self._ensure_day(d)
            msg_id = uuid.uuid4().hex[:12]
            self._db.execute(
                "INSERT INTO messages(date, session_id, ts, role, content, source, msg_id, meta) VALUES (?,?,?,?,?,?,?,?)",
                (d, sid, ts, role, self._clean(content), source, msg_id,
                 json.dumps(meta, ensure_ascii=False) if meta else None))
            self._db.commit()
            self._last_ts = ts
            return msg_id

    # -- reads ---------------------------------------------------------------------
    def timeline(self, d: str | None = None, limit: int = 400) -> dict:
        """Today's (or a given date's) conversation as an ordered timeline of session-start / message / session-end
        items, most-recent last. Suitable to render directly in the HUD."""
        with _lock:
            d = d or _today()
            msgs = self._db.execute(
                "SELECT session_id, ts, role, content, source, msg_id FROM messages WHERE date=? ORDER BY id DESC LIMIT ?",
                (d, limit)).fetchall()
            msgs = list(reversed(msgs))
            sess = self._db.execute(
                "SELECT session_id, start_ts, end_ts, start_reason, end_reason FROM sessions WHERE date=? ORDER BY start_ts",
                (d,)).fetchall()
            starts = {s[0]: s for s in sess}
            items: list[dict] = []
            seen_start: set[str] = set()
            last_sid = None
            for sid, ts, role, content, source, msg_id in msgs:
                if sid != last_sid and sid in starts and sid not in seen_start:
                    seen_start.add(sid)
                    items.append({"type": "session_start", "at": _hhmm(starts[sid][1]), "ts": starts[sid][1]})
                    last_sid = sid
                items.append({"type": "message", "role": role, "content": content, "source": source,
                              "msg_id": msg_id, "at": _hhmm(ts), "ts": ts})
            # append end markers for closed sessions that had visible messages
            for s in sess:
                if s[2] is not None and s[0] in seen_start:
                    items.append({"type": "session_end", "at": _hhmm(s[2]), "ts": s[2], "reason": s[4]})
            items.sort(key=lambda x: x["ts"])
            return {"date": d, "label": self._date_label(d), "items": items,
                    "count": sum(1 for i in items if i["type"] == "message")}

    def dates(self, limit: int = 60) -> list[dict]:
        with _lock:
            rows = self._db.execute(
                "SELECT m.date, COUNT(*) FROM messages m GROUP BY m.date ORDER BY m.date DESC LIMIT ?", (limit,)).fetchall()
            return [{"date": r[0], "label": self._date_label(r[0]), "messages": r[1]} for r in rows]

    @staticmethod
    def _date_label(d: str) -> str:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return d
        today = date.today()
        if dt == today:
            return "Today"
        if (today - dt).days == 1:
            return "Yesterday"
        return dt.strftime("%d %b %Y")

    def close(self):
        with _lock:
            try:
                self._db.close()
            except Exception:  # noqa: BLE001
                pass


_history: History | None = None


def history() -> History:
    global _history
    if _history is None:
        _history = History()
    return _history
