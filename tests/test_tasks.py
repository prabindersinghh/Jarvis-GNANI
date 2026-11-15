"""Durable task engine, event bus, follow-up queue, receipts, lease recovery, intent (OpenMuse integration §52).

Offline and deterministic — a temp SQLite DB, no app, no network, no desktop.
"""
from __future__ import annotations

import time

from jarvis.tasks.events import EventBus, EventType
from jarvis.tasks.intent import Intent, classify
from jarvis.tasks.manager import TaskManager
from jarvis.tasks.receipts import build_receipt, format_receipt
from jarvis.tasks.store import TaskState, TaskStore


def _mgr(tmp_path):
    return TaskManager(store=TaskStore(path=tmp_path / "t.db"), event_bus=EventBus())


# ---------------------------------------------------------------- store / persistence (§5, §6)
def test_task_created_persists_and_survives_reload(tmp_path):
    s = TaskStore(path=tmp_path / "t.db")
    t = s.create("open notepad and type hello", source="voice")
    assert t.state == TaskState.CREATED.value
    s2 = TaskStore(path=tmp_path / "t.db")
    got = s2.get(t.id)
    assert got is not None and got.goal == "open notepad and type hello" and got.source == "voice"


def test_lifecycle_states_and_transitions(tmp_path):
    m = _mgr(tmp_path)
    t = m.begin("do the thing", source="text")
    assert t.state == TaskState.RUNNING.value and m.current == t.id
    m.progress(t.id, "opened app")
    done = m.complete(t.id, verified=True, result="done and verified")
    assert done.state == TaskState.COMPLETED.value
    assert done.completed_steps == ["opened app"]
    assert done.receipt and done.receipt["verified"] is True
    assert m.current is None  # cleared


def test_pause_resume_cancel(tmp_path):
    m = _mgr(tmp_path)
    t = m.begin("long task")
    assert m.pause(t.id).state == TaskState.PAUSED.value
    assert m.resume(t.id).state == TaskState.RUNNING.value
    c = m.cancel(t.id)
    assert c.state == TaskState.CANCELLED.value
    assert c.receipt is not None                      # cancellation still produces a receipt (§7)


def test_retry_is_bounded(tmp_path):
    m = _mgr(tmp_path)
    t = m.begin("flaky task")
    m.fail(t.id, "transient network")
    r1 = m.retry(t.id)
    assert r1.state == TaskState.QUEUED.value and r1.retry_count == 1
    # drive it to failed again, retry to the cap (default 2), then no more
    m.resume(r1.id) if False else None
    m.store.update(t.id, state=TaskState.FAILED.value)
    m.retry(t.id)  # retry_count -> 2
    m.store.update(t.id, state=TaskState.FAILED.value)
    capped = m.retry(t.id)                              # at cap → stays FAILED
    assert capped.state == TaskState.FAILED.value and capped.retry_count == 2


def test_illegal_transition_is_refused(tmp_path):
    m = _mgr(tmp_path)
    t = m.begin("x")
    m.complete(t.id, verified=True)
    # completed is terminal → cannot pause
    again = m.pause(t.id)
    assert again.state == TaskState.COMPLETED.value


# ---------------------------------------------------------------- follow-up queue (§8)
def test_queue_enqueue_reorder_remove_next(tmp_path):
    m = _mgr(tmp_path)
    a = m.enqueue("build summary")
    b = m.enqueue("save report")
    c = m.enqueue("open gmail")
    assert [t.goal for t in m.queue()] == ["build summary", "save report", "open gmail"]
    m.reorder(c.id, 0)
    assert [t.goal for t in m.queue()][0] == "open gmail"
    m.remove(b.id)
    goals = [t.goal for t in m.queue()]
    assert "save report" not in goals
    assert m.pop_next().goal == "open gmail"


# ---------------------------------------------------------------- receipts (§11)
def test_receipt_reflects_real_tools_and_verification(tmp_path):
    m = _mgr(tmp_path)
    t = m.begin("open chrome and go to a page")
    m.record_tools(t.id, {"mcp__jarvis__computer_app": 1, "computer_observe": 2})
    done = m.complete(t.id, verified=True, result="opened and verified")
    r = done.receipt
    assert r["action_total"] == 3 and r["verified"] is True
    text = format_receipt(done)
    assert "TASK COMPLETE" in text and "verified" in text.lower()


def test_receipt_does_not_fabricate_verification(tmp_path):
    m = _mgr(tmp_path)
    t = m.begin("answer a question")           # no tools, no verification
    done = m.complete(t.id, verified=None, result="here is the answer")
    assert done.receipt["verified"] is None
    assert "no computer actions" in format_receipt(done).lower()


# ---------------------------------------------------------------- lease / recovery (§10)
def test_orphaned_task_recovered_not_replayed(tmp_path):
    s = TaskStore(path=tmp_path / "t.db")
    t = s.create("half-done external write", state=TaskState.RUNNING)
    s.update(t.id, lease_owner=99999, lease_expires=time.time() - 5)   # a dead worker's expired lease
    m = TaskManager(store=s, event_bus=EventBus())
    m._recover_orphans()
    got = s.get(t.id)
    assert got.state == TaskState.NEEDS_INPUT.value    # surfaced for review, NOT auto-restarted/replayed
    assert "orphan" in (got.failure_reason or "")


# ---------------------------------------------------------------- event bus (§13, §14)
def test_event_bus_pubsub_and_agui_shape():
    b = EventBus()
    seen = []
    off = b.subscribe(lambda e: seen.append(e))
    b.emit(EventType.RUN_STARTED, run_id="r1")
    b.emit(EventType.TOOL_CALL_START, task_id="t1", name="computer_app")
    assert [e.type for e in seen] == [EventType.RUN_STARTED, EventType.TOOL_CALL_START]
    agui = seen[0].to_agui()
    assert agui["type"] == "RUN_STARTED" and agui["threadId"] == "r1"
    off()
    b.emit(EventType.RUN_FINISHED)
    assert len(seen) == 2                               # unsubscribed → no more delivered


def test_event_bus_snapshot_for_future_client():
    b = EventBus(buffer=10)
    for i in range(15):
        b.emit(EventType.TASK_PROGRESS, task_id="t", step=f"s{i}")
    snap = b.snapshot(5)
    assert len(snap) == 5 and all("type" in e for e in snap)


def test_bad_subscriber_never_breaks_emit():
    b = EventBus()
    b.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    ok = []
    b.subscribe(lambda e: ok.append(1))
    b.emit(EventType.NOTIFICATION, text="hi")           # must not raise
    assert ok == [1]


# ---------------------------------------------------------------- intent (§8)
def test_intent_classifies_followup_vs_interrupt_vs_new():
    assert classify("after that, open Gmail") is Intent.FOLLOWUP
    assert classify("then create a summary") is Intent.FOLLOWUP
    assert classify("actually, stop and search manufacturing AI instead") is Intent.INTERRUPT
    assert classify("no wait, do this first") is Intent.INTERRUPT
    assert classify("open chrome") is Intent.NEW


# ---------------------------------------------------------------- status snapshot
def test_status_snapshot(tmp_path):
    m = _mgr(tmp_path)
    t = m.begin("current work")
    m.enqueue("later work")
    st = m.status()
    assert st["current"]["goal"] == "current work"
    assert st["queue"] and st["queue"][0]["goal"] == "later work"
    assert st["today"] >= 1
