"""JEV budget guard: ceiling, reserve, fail-closed pre-check, actual usage recording, persistence, month rollover."""
from __future__ import annotations

import json

import pytest

from jarvis.jev import budget as B


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setitem(B.config.SETTINGS, "jev_monthly_limit_inr", 1000.0)
    monkeypatch.setitem(B.config.SETTINGS, "jev_reserve_fraction", 0.10)
    monkeypatch.setitem(B.config.SETTINGS, "jev_usd_inr", 95.0)
    monkeypatch.setitem(B.config.SETTINGS, "jev_usd_per_mtok", 0.042)
    return tmp_path / "jev_budget.json"


def test_estimate_is_conservative_and_cost_math(ledger):
    body = {"model": "jev-latest", "state": "x" * 3000, "questions": {}}
    est = B.estimate_tokens(body)
    assert est >= 1000 + 200
    assert B.inr_for_tokens(1_000_000) == pytest.approx(0.042 * 95)


def test_check_record_persist_and_reject(ledger):
    b = B.Budget(ledger)
    body = {"state": "hello", "questions": {"q": {"type": "choice", "criteria": {"a": None}}}}
    est = b.check(body)
    assert est > 0
    charged = b.record(body, {"input_tokens": 600})
    assert charged == pytest.approx(B.inr_for_tokens(600))
    b2 = B.Budget(ledger)  # persisted across restarts
    assert b2.snapshot()["calls"] == 1 and b2.snapshot()["input_tokens"] == 600
    # push spend to just under the enforced line, then the next call must be rejected BEFORE sending
    b2.state["spend_inr"] = b2.usable_inr - 0.0001
    b2._save()
    with pytest.raises(B.BudgetExceeded):
        b2.check(body)
    assert b2.snapshot()["rejected"] == 1 and b2.snapshot()["exhausted"] is False
    b2.state["spend_inr"] = b2.usable_inr
    assert b2.snapshot()["exhausted"] is True and b2.snapshot()["remaining_inr"] == 0


def test_missing_usage_falls_back_to_estimate(ledger):
    b = B.Budget(ledger)
    body = {"state": "y" * 900}
    b.record(body, None)
    assert b.snapshot()["input_tokens"] == B.estimate_tokens(body)


def test_month_rollover_resets(ledger):
    b = B.Budget(ledger)
    b.state.update({"month": "2000-01", "spend_inr": 900.0, "calls": 5})
    b._save()
    b2 = B.Budget(ledger)
    s = b2.snapshot()
    assert s["spend_inr"] == 0 and s["calls"] == 0 and s["month"] != "2000-01"


def test_adapter_rejects_when_budget_exhausted(ledger, monkeypatch):
    from jarvis.jev import adapter
    b = B.Budget(ledger)
    b.state["spend_inr"] = b.usable_inr
    b._save()
    monkeypatch.setattr(B, "_budget", b)
    e = adapter.JevDecisionEngine(api_key="test")
    sent = {"n": 0}
    monkeypatch.setattr(e, "_post_raw", lambda body, cancel: sent.__setitem__("n", sent["n"] + 1) or ({}, 1))
    with pytest.raises(adapter.JevUnavailable):
        e.pick("x", [{"id": 1, "role": "button", "name": "A"}, {"id": 2, "role": "button", "name": "B"}])
    assert sent["n"] == 0  # nothing left the machine
    assert json.loads(ledger.read_text())["rejected"] == 1
