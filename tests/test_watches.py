"""Notifications + recurring watches (OpenMuse integration §20, §21, §56). Offline, local-file conditions."""
from __future__ import annotations

import time

from jarvis.tasks.events import EventBus
from jarvis.tasks.notify import Notifications
from jarvis.tasks.watch import WatchManager


def _notif(tmp_path):
    return Notifications(path=tmp_path / "n.db", event_bus=EventBus())


def _wm(tmp_path):
    b = EventBus()
    return WatchManager(path=tmp_path / "w.db", event_bus=b, notifier=Notifications(path=tmp_path / "n.db", event_bus=b))


# ---------------------------------------------------------------- notifications (§21)
def test_notification_add_list_unread(tmp_path):
    n = _notif(tmp_path)
    assert n.add("task_completed", "Done", "opened notepad")
    assert n.unread_count() == 1
    n.mark_read()
    assert n.unread_count() == 0


def test_notification_dedup_suppresses_identical(tmp_path):
    n = _notif(tmp_path)
    assert n.add("watch_triggered", "Price drop", dedup_key="w1:below") is not None
    assert n.add("watch_triggered", "Price drop", dedup_key="w1:below") is None   # identical within window → suppressed
    assert n.unread_count() == 1


def test_notification_emits_event(tmp_path):
    b = EventBus()
    seen = []
    b.subscribe(lambda e: seen.append(e))
    Notifications(path=tmp_path / "n.db", event_bus=b).add("human_required", "Confirm delete")
    assert any(e.type.value == "notification" for e in seen)


# ---------------------------------------------------------------- watches (§20, §56) — the real E2E
def test_file_change_watch_detects_and_dedups(tmp_path):
    wm = _wm(tmp_path)
    page = tmp_path / "page.txt"
    page.write_text("status: pending", encoding="utf-8")
    w = wm.add("result page", "file_change", str(page), interval_s=0)
    # first tick establishes the baseline (no change yet) → no notification
    assert wm.tick(now=time.time()) == 0
    # change the fixture
    page.write_text("status: RESULT ANNOUNCED", encoding="utf-8")
    assert wm.tick(now=time.time() + 1) == 1                      # detected the change → 1 notification
    # nothing changed since → no duplicate alert
    assert wm.tick(now=time.time() + 2) == 0
    assert wm.notify.unread_count() == 1


def test_text_present_watch(tmp_path):
    wm = _wm(tmp_path)
    page = tmp_path / "p.txt"
    page.write_text("loading…", encoding="utf-8")
    wm.add("winner posted", "text_present", str(page), target="Winner", interval_s=0)
    t0 = time.time()
    assert wm.tick(now=t0) == 0
    page.write_text("The Winner is Acme", encoding="utf-8")
    assert wm.tick(now=t0 + 1) == 1
    assert wm.tick(now=t0 + 2) == 0                               # already notified for this state


def test_watch_backoff_on_error(tmp_path):
    wm = _wm(tmp_path)
    w = wm.add("missing", "file_change", str(tmp_path / "does_not_exist.txt"), interval_s=10)
    before = time.time()
    wm.check(w, now=before)
    got = wm._get(w.id)
    assert got.failure_count == 1 and got.status == "error"
    assert got.next_check > before + 10                          # backed off beyond the base interval


def test_watch_cancel(tmp_path):
    wm = _wm(tmp_path)
    page = tmp_path / "p.txt"
    page.write_text("x", encoding="utf-8")
    w = wm.add("c", "file_change", str(page), interval_s=0)
    wm.cancel(w.id)
    page.write_text("y", encoding="utf-8")
    assert wm.tick(now=time.time()) == 0                          # cancelled → not checked
