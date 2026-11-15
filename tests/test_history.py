"""Daily conversation history: one date = one conversation, session markers, idle/rollover, restart continuity, redaction."""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from jarvis.history import History


@pytest.fixture
def db(tmp_path):
    return tmp_path / "h.db"


def _ts(day: str, hh: int, mm: int) -> float:
    return datetime.strptime(f"{day} {hh:02d}:{mm:02d}", "%Y-%m-%d %H:%M").timestamp()


def test_one_date_one_conversation_across_restarts(db):
    day = "2026-09-21"
    h1 = History(db, idle_end_s=1200)
    h1.start_session("startup", ts=_ts(day, 9, 2))
    h1.append("user", "Open Chrome", "voice", ts=_ts(day, 9, 2))
    h1.append("jarvis", "Chrome is open.", "voice", ts=_ts(day, 9, 3))
    h1.close()
    # "restart" JARVIS same day → new History object, resumes the SAME day's session (no new conversation)
    h2 = History(db, idle_end_s=1200)
    sid = h2.start_session("startup", ts=_ts(day, 9, 6))
    h2.append("user", "Now open Gmail", "text", ts=_ts(day, 9, 6))
    tl = h2.timeline(day)
    assert tl["count"] == 3
    starts = [i for i in tl["items"] if i["type"] == "session_start"]
    assert len(starts) == 1, "restart created a second session marker on the same day"
    # the resumed session is the same one started before restart
    assert h2._db.execute("SELECT COUNT(DISTINCT session_id) FROM messages WHERE date=?", (day,)).fetchone()[0] == 1
    assert sid  # noqa


def test_idle_gap_opens_new_session_marker_same_day(db):
    day = "2026-09-21"
    h = History(db, idle_end_s=600)  # 10 min idle threshold
    h.start_session("startup", ts=_ts(day, 9, 0))
    h.append("user", "one", ts=_ts(day, 9, 0))
    h.append("jarvis", "done", ts=_ts(day, 9, 1))
    # 40-minute gap → next append is a new session marker, still the same date
    h.append("user", "two", ts=_ts(day, 9, 41))
    tl = h.timeline(day)
    assert tl["count"] == 3
    kinds = [i["type"] for i in tl["items"]]
    assert kinds.count("session_start") == 2 and kinds.count("session_end") >= 1
    assert all(i.get("ts", 0) > 0 for i in tl["items"])
    assert tl["label"]  # date label present


def test_midnight_rollover_separates_dates(db):
    h = History(db, idle_end_s=3600)
    h.start_session("startup", ts=_ts("2026-09-21", 23, 55))
    h.append("user", "late night", ts=_ts("2026-09-21", 23, 56))
    h.append("jarvis", "ok", ts=_ts("2026-09-22", 0, 3))  # crosses midnight
    assert h.timeline("2026-09-21")["count"] == 1
    assert h.timeline("2026-09-22")["count"] == 1
    ds = {d["date"] for d in h.dates()}
    assert {"2026-09-21", "2026-09-22"} <= ds


def test_voice_and_text_share_the_day(db):
    day = "2026-09-21"
    h = History(db)
    h.start_session(ts=_ts(day, 9, 3))
    h.append("user", "Open Chrome.", "voice", ts=_ts(day, 9, 3))
    h.append("user", "Now open Gmail.", "text", ts=_ts(day, 9, 5))
    tl = h.timeline(day)
    sources = [i["source"] for i in tl["items"] if i["type"] == "message"]
    assert "voice" in sources and "text" in sources
    assert tl["count"] == 2


def test_redaction_of_secrets(db):
    h = History(db)
    h.start_session()
    mid = h.append("user", "my key is sk-ABCDEFGHIJKLMNOPQRSTUVWX and token ghp_ABCDEFGHIJKLMNOPQRSTUV")
    raw = h._db.execute("SELECT content FROM messages WHERE msg_id=?", (mid,)).fetchone()[0]
    assert "sk-ABCDEFGHIJKLMNOP" not in raw and "ghp_" not in raw
    assert mid


def test_timeline_ordering_and_labels(db):
    day = "2026-09-21"
    h = History(db)
    h.start_session(ts=_ts(day, 10, 0))
    for i in range(5):
        h.append("user" if i % 2 == 0 else "jarvis", f"m{i}", ts=_ts(day, 10, i))
    tl = h.timeline(day)
    msgs = [i for i in tl["items"] if i["type"] == "message"]
    assert [m["content"] for m in msgs] == [f"m{i}" for i in range(5)]  # chronological, oldest→newest
    assert msgs[0]["at"].count(":") == 1  # compact HH:MM timestamp
