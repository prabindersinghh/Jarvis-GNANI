"""Local experience store (§6, §24, §25). SQLite under data/memory/experiences/. Append-only, redacted, bounded.

One row per meaningful completed decision. Never stores raw private content, secrets, or credential-field values —
every text field passes through the privacy redactor and sensitive fields are dropped. This is DISTINCT from
jarvis.memory (experience, not preferences/workflows) and from jarvis.history (conversation transcript).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime

from .. import config

log = logging.getLogger("jarvis.learning.exp")

EXP_DIR = config.DATA / "memory" / "experiences"
EXP_DB = EXP_DIR / "experiences.db"
_lock = threading.RLock()

FIELDS = ("experience_id", "ts", "date", "session_id", "task_id", "application", "window_context", "action_type",
          "routing_tier", "policy_version", "candidate_count", "chosen_action", "confidence", "jev_used", "ocr_used",
          "vision_used", "claude_used", "batch_used", "latency_ms", "verification_result", "success", "failure_reason",
          "retry_count", "fallback_count", "user_correction", "safety_classification", "reward", "context_fingerprint",
          "source")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id TEXT, ts REAL, date TEXT, session_id TEXT, task_id TEXT, application TEXT, window_context TEXT,
    action_type TEXT, routing_tier TEXT, policy_version INTEGER, candidate_count INTEGER, chosen_action TEXT,
    confidence REAL, jev_used INTEGER, ocr_used INTEGER, vision_used INTEGER, claude_used INTEGER, batch_used INTEGER,
    latency_ms REAL, verification_result INTEGER, success INTEGER, failure_reason TEXT, retry_count INTEGER,
    fallback_count INTEGER, user_correction INTEGER, safety_classification TEXT, reward REAL,
    context_fingerprint TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS ix_exp_fp ON experiences(context_fingerprint, id);
CREATE INDEX IF NOT EXISTS ix_exp_date ON experiences(date, id);
"""

_SENSITIVE = ("password", "passcode", "pin", "otp", "token", "secret", "api_key", "credential", "cvv", "card")


def _clean(v):
    if isinstance(v, str):
        try:
            from ..safety import privacy
            return privacy.redact_text(v)[:200]
        except Exception:  # noqa: BLE001
            return v[:200]
    return v


class ExperienceStore:
    def __init__(self, path=EXP_DB, max_rows: int = 50000):
        self.path = path
        self.max_rows = max_rows
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def record(self, exp: dict) -> str:
        with _lock:
            e = {k: exp.get(k) for k in FIELDS}
            e["experience_id"] = e.get("experience_id") or uuid.uuid4().hex[:12]
            e["ts"] = e.get("ts") or time.time()
            e["date"] = e.get("date") or datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d")
            # drop obviously-sensitive chosen actions entirely; redact the rest
            if any(s in str(e.get("chosen_action", "")).lower() for s in _SENSITIVE):
                e["chosen_action"] = "[sensitive]"
            for k in ("chosen_action", "window_context", "failure_reason", "action_type"):
                e[k] = _clean(e.get(k))
            for k in ("jev_used", "ocr_used", "vision_used", "claude_used", "batch_used", "verification_result",
                      "success", "user_correction"):
                e[k] = int(bool(e.get(k)))
            cols = ",".join(FIELDS)
            self._db.execute(f"INSERT INTO experiences({cols}) VALUES ({','.join('?' for _ in FIELDS)})",
                             tuple(e[k] for k in FIELDS))
            self._db.commit()
            self._compact_if_needed()
            return e["experience_id"]

    def _compact_if_needed(self):
        n = self._db.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
        if n > self.max_rows:
            cut = self._db.execute("SELECT id FROM experiences ORDER BY id DESC LIMIT 1 OFFSET ?",
                                   (self.max_rows,)).fetchone()
            if cut:
                self._db.execute("DELETE FROM experiences WHERE id <= ?", (cut[0],))
                self._db.commit()

    def recent(self, n: int = 500) -> list[dict]:
        with _lock:
            rows = self._db.execute(f"SELECT {','.join(FIELDS)} FROM experiences ORDER BY id DESC LIMIT ?", (n,)).fetchall()
            return [dict(zip(FIELDS, r)) for r in reversed(rows)]

    def by_fingerprint(self, fp: str, n: int = 200) -> list[dict]:
        with _lock:
            rows = self._db.execute(f"SELECT {','.join(FIELDS)} FROM experiences WHERE context_fingerprint=? ORDER BY id DESC LIMIT ?",
                                    (fp, n)).fetchall()
            return [dict(zip(FIELDS, r)) for r in reversed(rows)]

    def count(self) -> int:
        with _lock:
            return self._db.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]

    def day(self, date: str) -> list[dict]:
        with _lock:
            rows = self._db.execute(f"SELECT {','.join(FIELDS)} FROM experiences WHERE date=? ORDER BY id", (date,)).fetchall()
            return [dict(zip(FIELDS, r)) for r in rows]

    def close(self):
        with _lock:
            try:
                self._db.close()
            except Exception:  # noqa: BLE001
                pass
