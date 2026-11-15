"""Durable task store (OpenMuse integration §5, §6, §10) — SQLite, local, atomic.

A *task* is a unit of long-running work with persistent state that survives app/UI restart, Claude reconnect, and (where
safe) a crash. It is DISTINCT from a conversation: one daily conversation may spawn many tasks (§5, §24). We reuse
SQLite (as `jarvis.history` does) rather than importing OpenMuse's Postgres/PGlite (§45, §47-49: one store, no dupes).

Recovery uses a lightweight **lease** (owner pid + expiry) instead of OpenMuse's Postgres SQL leases: on startup a task
still marked running/paused whose lease has expired is *orphaned* and surfaced for review — never blindly replayed,
because its side effects are uncertain (§10).
"""
from __future__ import annotations

import enum
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from .. import config


class TaskState(str, enum.Enum):
    CREATED = "created"
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING = "waiting"
    NEEDS_INPUT = "needs_input"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)

    @property
    def active(self) -> bool:
        return self in (TaskState.PLANNING, TaskState.RUNNING, TaskState.WAITING, TaskState.VERIFYING)


@dataclass
class Task:
    id: str
    goal: str
    state: str = TaskState.CREATED.value
    kind: str = "foreground"                    # foreground | background
    source: str = "text"                        # voice | text
    day: str = ""
    session_id: str | None = None
    plan: list = field(default_factory=list)     # ordered step descriptions
    current_step: int = 0
    completed_steps: list = field(default_factory=list)
    retry_count: int = 0
    failure_reason: str | None = None
    tools: dict = field(default_factory=dict)    # tool name -> count (from real runtime)
    verified: bool | None = None
    result: str | None = None
    receipt: dict | None = None
    queue_pos: int | None = None                 # ordering while state == queued
    lease_owner: int | None = None               # pid holding the task
    lease_expires: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.completed_at and self.created_at:
            return round((self.completed_at - self.created_at) * 1000)
        return None


_JSON = ("plan", "completed_steps", "tools", "receipt")


class TaskStore:
    def __init__(self, path=None):
        self.path = str(path) if path else str(config.DATA / "tasks" / "tasks.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)  # autocommit; we wrap writes in transactions
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def _init(self):
        with self._lock, self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, goal TEXT, state TEXT, kind TEXT, source TEXT, day TEXT, session_id TEXT,
                plan TEXT, current_step INTEGER, completed_steps TEXT, retry_count INTEGER, failure_reason TEXT,
                tools TEXT, verified INTEGER, result TEXT, receipt TEXT, queue_pos INTEGER,
                lease_owner INTEGER, lease_expires REAL, created_at REAL, updated_at REAL, completed_at REAL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_day ON tasks(day)")

    # -- row <-> Task --------------------------------------------------------------
    @staticmethod
    def _to_row(t: Task) -> dict:
        d = asdict(t)
        for k in _JSON:
            d[k] = json.dumps(d[k]) if d[k] is not None else None
        d["verified"] = None if t.verified is None else int(t.verified)
        return d

    @staticmethod
    def _from_row(r: sqlite3.Row) -> Task:
        d = dict(r)
        for k in _JSON:
            d[k] = json.loads(d[k]) if d[k] else ([] if k in ("plan", "completed_steps") else ({} if k == "tools" else None))
        d["verified"] = None if d["verified"] is None else bool(d["verified"])
        return Task(**d)

    # -- CRUD ----------------------------------------------------------------------
    def create(self, goal: str, *, kind="foreground", source="text", session_id=None,
               state=TaskState.CREATED, plan=None, queue_pos=None) -> Task:
        now = time.time()
        t = Task(id=uuid.uuid4().hex[:12], goal=goal, state=state.value if isinstance(state, TaskState) else state,
                 kind=kind, source=source, day=time.strftime("%Y-%m-%d"), session_id=session_id,
                 plan=plan or [], queue_pos=queue_pos, created_at=now, updated_at=now)
        row = self._to_row(t)
        cols = ",".join(row)
        with self._lock, self._conn() as c:
            c.execute(f"INSERT INTO tasks ({cols}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
        return t

    def get(self, task_id: str) -> Task | None:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._from_row(r) if r else None

    def update(self, task_id: str, **fields) -> Task | None:
        """Atomic partial update. JSON-encodes list/dict fields; always bumps updated_at."""
        fields["updated_at"] = time.time()
        enc = {}
        for k, v in fields.items():
            if k in _JSON:
                enc[k] = json.dumps(v) if v is not None else None
            elif k == "verified":
                enc[k] = None if v is None else int(v)
            elif k == "state":
                enc[k] = v.value if isinstance(v, TaskState) else v
            else:
                enc[k] = v
        sets = ",".join(f"{k}=?" for k in enc)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE tasks SET {sets} WHERE id=?", [*enc.values(), task_id])
            r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._from_row(r) if r else None

    def list(self, *, day=None, state=None, kind=None, limit=200) -> list[Task]:
        q, args = "SELECT * FROM tasks WHERE 1=1", []
        if day:
            q += " AND day=?"; args.append(day)
        if state:
            q += " AND state=?"; args.append(state.value if isinstance(state, TaskState) else state)
        if kind:
            q += " AND kind=?"; args.append(kind)
        q += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
        with self._lock, self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [self._from_row(r) for r in rows]

    def delete(self, task_id: str):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def queued(self) -> list[Task]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM tasks WHERE state=? ORDER BY queue_pos ASC, created_at ASC",
                             (TaskState.QUEUED.value,)).fetchall()
        return [self._from_row(r) for r in rows]

    def active(self) -> list[Task]:
        act = (TaskState.PLANNING.value, TaskState.RUNNING.value, TaskState.WAITING.value, TaskState.VERIFYING.value)
        with self._lock, self._conn() as c:
            rows = c.execute(f"SELECT * FROM tasks WHERE state IN ({','.join('?' * len(act))})", act).fetchall()
        return [self._from_row(r) for r in rows]

    def orphaned(self, now: float | None = None) -> list[Task]:
        """Tasks left running/paused by a dead worker whose lease has expired — candidates for recovery review (§10)."""
        now = now or time.time()
        keep = (TaskState.RUNNING.value, TaskState.PLANNING.value, TaskState.WAITING.value,
                TaskState.VERIFYING.value, TaskState.PAUSED.value)
        with self._lock, self._conn() as c:
            rows = c.execute(f"SELECT * FROM tasks WHERE state IN ({','.join('?' * len(keep))}) "
                             f"AND lease_expires < ? AND lease_expires > 0", [*keep, now]).fetchall()
        return [self._from_row(r) for r in rows]
