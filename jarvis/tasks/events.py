"""JARVIS event bus — one normalized runtime event stream (OpenMuse/AG-UI integration §13, §14).

A lightweight, thread-safe pub/sub that JARVIS's real runtime emits to: run/task lifecycle, tool activity, message
streaming, voice/TTS/JEV/verification, notifications. It becomes the single source for orb + HUD state (instead of
polling) and the read-only contract a future iPhone/web client can consume (§27, §29, §30) — WITHOUT moving the brain
off Windows and WITHOUT exposing anything: this is an in-process bus, not a network endpoint.

Event names are AG-UI-compatible where AG-UI defines an equivalent (RUN_STARTED, TOOL_CALL_START, STATE_DELTA, …) so the
same vocabulary maps cleanly onto an AG-UI SSE transport later; JARVIS-specific events (voice/tts/jev/task) extend it.
`to_agui()` renders an event in AG-UI's shape. A bounded ring buffer keeps recent events so a late subscriber (or a
future client on connect) can get a snapshot without replaying history.
"""
from __future__ import annotations

import enum
import threading
import time
from collections import deque
from dataclasses import dataclass, field


class EventType(str, enum.Enum):
    # --- AG-UI run lifecycle ---
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_ERROR = "run_error"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    # --- AG-UI message streaming ---
    TEXT_MESSAGE_START = "text_message_start"
    TEXT_MESSAGE_CONTENT = "text_message_content"
    TEXT_MESSAGE_END = "text_message_end"
    # --- AG-UI tool calls ---
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_ARGS = "tool_call_args"
    TOOL_CALL_END = "tool_call_end"
    TOOL_CALL_RESULT = "tool_call_result"
    # --- AG-UI state ---
    STATE_SNAPSHOT = "state_snapshot"
    STATE_DELTA = "state_delta"
    # --- JARVIS task lifecycle ---
    TASK_CREATED = "task_created"
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_CANCELLED = "task_cancelled"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_NEEDS_INPUT = "task_needs_input"
    QUEUE_CHANGED = "queue_changed"
    # --- JARVIS voice / tts / jev / verification ---
    VOICE_LISTENING = "voice_listening"
    VOICE_TRANSCRIPT_PARTIAL = "voice_transcript_partial"
    VOICE_TRANSCRIPT_FINAL = "voice_transcript_final"
    TTS_STARTED = "tts_started"
    TTS_FINISHED = "tts_finished"
    TTS_INTERRUPTED = "tts_interrupted"
    JEV_STARTED = "jev_started"
    JEV_FINISHED = "jev_finished"
    JEV_REJECTED_BUDGET = "jev_rejected_budget"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    # --- notifications / takeover / watches ---
    NOTIFICATION = "notification"
    TAKEOVER_STARTED = "takeover_started"
    TAKEOVER_ENDED = "takeover_ended"
    WATCH_TRIGGERED = "watch_triggered"
    CUSTOM = "custom"


# AG-UI's canonical UPPER_SNAKE names for the events it defines (others pass through as-is, as CUSTOM-ish)
_AGUI = {
    EventType.RUN_STARTED: "RUN_STARTED", EventType.RUN_FINISHED: "RUN_FINISHED", EventType.RUN_ERROR: "RUN_ERROR",
    EventType.STEP_STARTED: "STEP_STARTED", EventType.STEP_FINISHED: "STEP_FINISHED",
    EventType.TEXT_MESSAGE_START: "TEXT_MESSAGE_START", EventType.TEXT_MESSAGE_CONTENT: "TEXT_MESSAGE_CONTENT",
    EventType.TEXT_MESSAGE_END: "TEXT_MESSAGE_END",
    EventType.TOOL_CALL_START: "TOOL_CALL_START", EventType.TOOL_CALL_ARGS: "TOOL_CALL_ARGS",
    EventType.TOOL_CALL_END: "TOOL_CALL_END", EventType.TOOL_CALL_RESULT: "TOOL_CALL_RESULT",
    EventType.STATE_SNAPSHOT: "STATE_SNAPSHOT", EventType.STATE_DELTA: "STATE_DELTA",
}


@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    run_id: str | None = None
    task_id: str | None = None

    def as_dict(self) -> dict:
        return {"type": self.type.value, "ts": self.ts, "run_id": self.run_id, "task_id": self.task_id, "data": self.data}

    def to_agui(self) -> dict:
        """AG-UI-compatible rendering: canonical UPPER_SNAKE type for events AG-UI defines, else the JARVIS name."""
        name = _AGUI.get(self.type, self.type.value.upper())
        out = {"type": name, "timestamp": int(self.ts * 1000)}
        if self.run_id:
            out["threadId"] = self.run_id
        if self.task_id:
            out["taskId"] = self.task_id
        out.update(self.data)
        return out


class EventBus:
    def __init__(self, buffer: int = 500):
        self._subs: list = []
        self._lock = threading.RLock()
        self._recent: deque[Event] = deque(maxlen=buffer)

    def subscribe(self, cb):
        """Register a callback(Event). Returns an unsubscribe callable. Callbacks must not raise (they are guarded)."""
        with self._lock:
            self._subs.append(cb)

        def _off():
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return _off

    def emit(self, type: EventType, *, run_id: str | None = None, task_id: str | None = None, **data) -> Event:
        ev = Event(type=type, data=data, run_id=run_id, task_id=task_id)
        with self._lock:
            self._recent.append(ev)
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(ev)
            except Exception:  # noqa: BLE001 — a bad subscriber must never break the runtime
                pass
        return ev

    def recent(self, n: int | None = None) -> list[Event]:
        with self._lock:
            evs = list(self._recent)
        return evs[-n:] if n else evs

    def snapshot(self, n: int = 100) -> list[dict]:
        """AG-UI-shaped recent events — what a future read-only client receives on connect (§30)."""
        return [e.to_agui() for e in self.recent(n)]


_bus: EventBus | None = None


def bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
