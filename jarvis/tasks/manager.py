"""Task manager — orchestration + presentation layer over JARVIS's existing brain (OpenMuse integration §4-11).

This is NOT a second agent. Claude + the Windows engine still do the work; the manager tracks durable task state,
generates receipts from real verification, runs the follow-up queue, supports pause/resume/cancel/retry, bounds
background concurrency, recovers orphaned tasks after a crash, and emits events on the bus. The app calls it from its
real runtime callbacks (turn start → task running, result → completed/verifying, error → failed).

One authoritative task engine (§48). It never touches memory / conversation / safety / budget / RL — those stay
authoritative and are only *called* by the app, never reimplemented here (§47-50).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .. import config
from .events import EventType, bus
from .receipts import build_receipt
from .store import Task, TaskState, TaskStore

log = logging.getLogger("jarvis.tasks")

LEASE_TTL = 60.0            # a running task's lease is renewed every heartbeat; if it lapses the task is orphaned
HEARTBEAT_S = 20.0
MAX_BACKGROUND = 2         # bounded concurrency for background tasks (§9: do NOT run many at once)

# allowed lifecycle transitions (defensive — an illegal transition is refused, not persisted)
_ALLOWED = {
    TaskState.CREATED: {TaskState.QUEUED, TaskState.RUNNING, TaskState.PLANNING, TaskState.CANCELLED},
    TaskState.QUEUED: {TaskState.RUNNING, TaskState.PLANNING, TaskState.CANCELLED, TaskState.PAUSED},
    TaskState.PLANNING: {TaskState.RUNNING, TaskState.WAITING, TaskState.PAUSED, TaskState.CANCELLING, TaskState.FAILED},
    TaskState.RUNNING: {TaskState.WAITING, TaskState.NEEDS_INPUT, TaskState.VERIFYING, TaskState.PAUSED,
                        TaskState.CANCELLING, TaskState.COMPLETED, TaskState.FAILED},
    TaskState.WAITING: {TaskState.RUNNING, TaskState.PAUSED, TaskState.CANCELLING, TaskState.FAILED, TaskState.COMPLETED},
    TaskState.NEEDS_INPUT: {TaskState.RUNNING, TaskState.CANCELLING, TaskState.PAUSED, TaskState.FAILED},
    TaskState.VERIFYING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.RUNNING},
    TaskState.PAUSED: {TaskState.RUNNING, TaskState.CANCELLING, TaskState.CANCELLED, TaskState.QUEUED},
    TaskState.CANCELLING: {TaskState.CANCELLED, TaskState.FAILED},
    TaskState.COMPLETED: set(), TaskState.FAILED: {TaskState.QUEUED, TaskState.RUNNING}, TaskState.CANCELLED: set(),
}


class TaskManager:
    def __init__(self, store: TaskStore | None = None, event_bus=None):
        self.store = store or TaskStore()
        self.bus = event_bus or bus()
        self.enabled = bool(config.SETTINGS.get("tasks_enabled", True))
        self.pid = os.getpid()
        self.current: str | None = None          # id of the foreground task in flight
        self._bg: set[str] = set()                # ids of running background tasks
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._hb: threading.Thread | None = None

    # -- lifecycle of the manager --------------------------------------------------
    def start(self):
        if not self.enabled:
            return
        self._recover_orphans()
        if self._hb is None:
            self._hb = threading.Thread(target=self._heartbeat_loop, name="task-heartbeat", daemon=True)
            self._hb.start()

    def stop(self):
        self._stop.set()

    def _heartbeat_loop(self):
        while not self._stop.wait(HEARTBEAT_S):
            try:
                with self._lock:
                    ids = [i for i in ([self.current] if self.current else []) + list(self._bg) if i]
                for tid in ids:
                    self.store.update(tid, lease_owner=self.pid, lease_expires=time.time() + LEASE_TTL)
            except Exception as e:  # noqa: BLE001
                log.debug("heartbeat: %s", e)

    def _recover_orphans(self):
        """A task left active by a dead worker (expired lease) is surfaced as needs_input for review — never replayed
        blindly, because its external side effects are uncertain (§10)."""
        for t in self.store.orphaned():
            self.store.update(t.id, state=TaskState.NEEDS_INPUT.value, failure_reason="orphaned (worker exited); review",
                              lease_owner=None)
            self.bus.emit(EventType.TASK_NEEDS_INPUT, task_id=t.id, goal=t.goal, reason="recovered after restart")
            log.info("recovered orphaned task %s (%s)", t.id, t.goal)

    # -- creating / running --------------------------------------------------------
    def _lease(self) -> dict:
        return {"lease_owner": self.pid, "lease_expires": time.time() + LEASE_TTL}

    def begin(self, goal: str, *, source="text", session_id=None, kind="foreground") -> Task:
        """Start a foreground task now (a normal turn). Returns the running Task."""
        t = self.store.create(goal, kind=kind, source=source, session_id=session_id, state=TaskState.RUNNING)
        self.store.update(t.id, **self._lease())
        with self._lock:
            if kind == "foreground":
                self.current = t.id
            else:
                self._bg.add(t.id)
        self.bus.emit(EventType.TASK_CREATED, task_id=t.id, goal=goal, kind=kind)
        self.bus.emit(EventType.TASK_STARTED, task_id=t.id, goal=goal, kind=kind)
        return self.store.get(t.id)

    def _transition(self, task_id: str, to: TaskState, **fields) -> Task | None:
        t = self.store.get(task_id)
        if t is None:
            return None
        cur = TaskState(t.state)
        if to != cur and to not in _ALLOWED.get(cur, set()):
            log.debug("illegal transition %s -> %s for %s", cur.value, to.value, task_id)
            return t
        return self.store.update(task_id, state=to.value, **fields)

    def progress(self, task_id: str, step: str, *, index: int | None = None):
        t = self.store.get(task_id)
        if t is None:
            return
        completed = t.completed_steps + [step]
        self.store.update(task_id, completed_steps=completed, current_step=(index if index is not None else t.current_step + 1))
        self.bus.emit(EventType.TASK_PROGRESS, task_id=task_id, step=step)

    def record_tools(self, task_id: str, tools: dict):
        if tools:
            self.store.update(task_id, tools=dict(tools))

    def complete(self, task_id: str, *, verified: bool | None = None, result: str | None = None) -> Task | None:
        t = self._transition(task_id, TaskState.COMPLETED, verified=verified, result=result,
                             completed_at=time.time(), lease_owner=None)
        if t is None:
            return None
        t = self.store.get(task_id)
        receipt = build_receipt(t)
        t = self.store.update(task_id, receipt=receipt)
        self._clear(task_id)
        self.bus.emit(EventType.TASK_COMPLETED, task_id=task_id, goal=t.goal, verified=verified, receipt=receipt)
        return t

    def fail(self, task_id: str, reason: str) -> Task | None:
        t = self._transition(task_id, TaskState.FAILED, failure_reason=reason, completed_at=time.time(), lease_owner=None)
        if t:
            t = self.store.update(task_id, receipt=build_receipt(self.store.get(task_id)))
            self._clear(task_id)
            self.bus.emit(EventType.TASK_FAILED, task_id=task_id, goal=t.goal, reason=reason)
        return t

    def _clear(self, task_id: str):
        with self._lock:
            if self.current == task_id:
                self.current = None
            self._bg.discard(task_id)

    # -- pause / resume / cancel / retry (§7) --------------------------------------
    def pause(self, task_id: str) -> Task | None:
        t = self._transition(task_id, TaskState.PAUSED)
        if t and t.state == TaskState.PAUSED.value:
            self.bus.emit(EventType.TASK_PAUSED, task_id=task_id, goal=t.goal)
        return t

    def resume(self, task_id: str) -> Task | None:
        # resume revalidates by re-entering RUNNING; the caller re-observes current state before repeating actions (§7)
        t = self._transition(task_id, TaskState.RUNNING, **self._lease())
        if t and t.state == TaskState.RUNNING.value:
            with self._lock:
                (self._bg.add if t.kind == "background" else (lambda x: setattr(self, "current", x)))(task_id)
            self.bus.emit(EventType.TASK_RESUMED, task_id=task_id, goal=t.goal)
        return t

    def cancel(self, task_id: str) -> Task | None:
        cur = self.store.get(task_id)
        if cur and TaskState(cur.state).terminal:
            return cur
        self.store.update(task_id, state=TaskState.CANCELLING.value)
        t = self.store.update(task_id, state=TaskState.CANCELLED.value, completed_at=time.time(), lease_owner=None)
        if t:
            t = self.store.update(task_id, receipt=build_receipt(self.store.get(task_id)))
            self._clear(task_id)
            self.bus.emit(EventType.TASK_CANCELLED, task_id=task_id, goal=t.goal)
        return t

    def retry(self, task_id: str) -> Task | None:
        """Re-queue a FAILED task with bounded retry. Deterministic failures are not retried past the cap (§7)."""
        t = self.store.get(task_id)
        if t is None or t.state != TaskState.FAILED.value:
            return t
        if t.retry_count >= int(config.SETTINGS.get("task_max_retries", 2)):
            return t
        t = self.store.update(task_id, state=TaskState.QUEUED.value, retry_count=t.retry_count + 1,
                              failure_reason=None, queue_pos=self._next_pos())
        self.bus.emit(EventType.TASK_QUEUED, task_id=task_id, goal=t.goal, retry=t.retry_count)
        self.bus.emit(EventType.QUEUE_CHANGED, queue=[q.goal for q in self.store.queued()])
        return t

    # -- follow-up queue (§8) ------------------------------------------------------
    def _next_pos(self) -> int:
        q = self.store.queued()
        return (max((t.queue_pos or 0) for t in q) + 1) if q else 0

    def enqueue(self, goal: str, *, source="text", session_id=None) -> Task:
        t = self.store.create(goal, source=source, session_id=session_id, state=TaskState.QUEUED, queue_pos=self._next_pos())
        self.bus.emit(EventType.TASK_QUEUED, task_id=t.id, goal=goal)
        self.bus.emit(EventType.QUEUE_CHANGED, queue=[q.goal for q in self.store.queued()])
        return t

    def queue(self) -> list[Task]:
        return self.store.queued()

    def reorder(self, task_id: str, pos: int):
        q = [t for t in self.store.queued() if t.id != task_id]
        q.insert(max(0, min(pos, len(q))), self.store.get(task_id))
        for i, t in enumerate(q):
            if t:
                self.store.update(t.id, queue_pos=i)
        self.bus.emit(EventType.QUEUE_CHANGED, queue=[t.goal for t in self.store.queued()])

    def remove(self, task_id: str):
        self.store.update(task_id, state=TaskState.CANCELLED.value, completed_at=time.time())
        self.bus.emit(EventType.QUEUE_CHANGED, queue=[t.goal for t in self.store.queued()])

    def pop_next(self) -> Task | None:
        """Peek the next queued follow-up (does not start it — the app decides when to run it)."""
        q = self.store.queued()
        return q[0] if q else None

    def dequeue(self, task_id: str):
        """Remove a queued placeholder because it is now being executed as a normal turn (its own task is created)."""
        self.store.delete(task_id)
        self.bus.emit(EventType.QUEUE_CHANGED, queue=[t.goal for t in self.store.queued()])

    def can_start_background(self) -> bool:
        with self._lock:
            return len(self._bg) < MAX_BACKGROUND

    # -- status snapshot (for the HUD / a future client) ---------------------------
    def status(self) -> dict:
        cur = self.store.get(self.current) if self.current else None
        return {
            "current": None if cur is None else {"id": cur.id, "goal": cur.goal, "state": cur.state,
                                                 "steps": cur.completed_steps, "kind": cur.kind},
            "queue": [{"id": t.id, "goal": t.goal, "retry": t.retry_count} for t in self.store.queued()],
            "background": len(self._bg),
            "today": len(self.store.list(day=time.strftime("%Y-%m-%d"))),
        }


_manager: TaskManager | None = None


def manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager
