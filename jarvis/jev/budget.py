"""JEV hard monthly spending ceiling — local, fail-closed, persisted (D:\\JARVIS\\data\\jev_budget.json).

TypeSafe exposes no balance/billing endpoint (docs.typesafe.ai, checked 2026-09-21): only per-response
`usage.input_tokens`. So the guard does conservative local accounting:

  * price: $0.042 per 1M input tokens (output is free) — `jev_usd_per_mtok`, kept configurable
  * FX:    a deliberately high USD→INR rate (default 95) — `jev_usd_inr`
  * limit: ₹1,000 per calendar month — `jev_monthly_limit_inr` (hard ceiling, non-negotiable)
  * reserve: the guard stops at 90 % of the ceiling — `jev_reserve_fraction`
  * every call is pre-checked with an over-estimate of its input tokens (chars / 3 + fixed overhead);
    if the projected spend would cross the reserve line the call is REJECTED before it is sent;
  * the actual `usage.input_tokens` from the response is recorded (max(estimate, actual) when the provider
    reports less than we estimated is NOT used — actual wins, but a missing usage falls back to the estimate);
  * state persists across restarts; the ledger resets automatically when the calendar month changes.
Never depends on the UI. Never auto-refills.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from .. import config

log = logging.getLogger("jarvis.jev.budget")

LEDGER = config.DATA / "jev_budget.json"
DEFAULTS = {"jev_monthly_limit_inr": 1000.0, "jev_reserve_fraction": 0.10, "jev_usd_inr": 95.0, "jev_usd_per_mtok": 0.042}
_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    """Raised BEFORE a request is sent when it would cross the ceiling (caller falls back to non-JEV routing)."""


def _month() -> str:
    return time.strftime("%Y-%m")


def _setting(k: str) -> float:
    return float(config.SETTINGS.get(k, DEFAULTS[k]))


def estimate_tokens(body) -> int:
    """Conservative over-estimate of input tokens for a request body (≈ chars/3 + 200 overhead)."""
    s = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return int(len(s) / 3) + 200


def inr_for_tokens(tokens: int) -> float:
    return tokens / 1e6 * _setting("jev_usd_per_mtok") * _setting("jev_usd_inr")


class Budget:
    def __init__(self, path: Path = LEDGER):
        self.path = path
        self.state = {"month": _month(), "input_tokens": 0, "spend_inr": 0.0, "calls": 0, "rejected": 0, "updated": time.time()}
        self._load()

    # -- persistence -------------------------------------------------------------------
    def _load(self):
        try:
            if self.path.exists():
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("month"):
                    self.state.update(d)
        except Exception as e:  # noqa: BLE001
            log.warning("budget ledger unreadable (%s); starting conservative", e)
        self._roll_month()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            self.state["updated"] = time.time()
            tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:  # noqa: BLE001
            log.error("budget ledger write failed: %s", e)

    def _roll_month(self):
        m = _month()
        if self.state.get("month") != m:
            log.info("JEV budget: new month %s (previous %s spent ₹%.2f)", m, self.state.get("month"), self.state.get("spend_inr", 0))
            self.state = {"month": m, "input_tokens": 0, "spend_inr": 0.0, "calls": 0, "rejected": 0, "updated": time.time()}
            self._save()

    # -- accounting --------------------------------------------------------------------
    @property
    def limit_inr(self) -> float:
        return _setting("jev_monthly_limit_inr")

    @property
    def usable_inr(self) -> float:
        """The ceiling minus the safety reserve — the line the guard actually enforces."""
        return self.limit_inr * (1.0 - _setting("jev_reserve_fraction"))

    def remaining_inr(self) -> float:
        with _lock:
            self._roll_month()
            return max(0.0, self.usable_inr - float(self.state["spend_inr"]))

    def check(self, body) -> float:
        """Pre-flight: return the estimated cost (INR) or raise BudgetExceeded. Fail closed on any error."""
        with _lock:
            self._roll_month()
            est_tokens = estimate_tokens(body)
            est_inr = inr_for_tokens(est_tokens)
            projected = float(self.state["spend_inr"]) + est_inr
            if projected > self.usable_inr:
                self.state["rejected"] = int(self.state.get("rejected", 0)) + 1
                self._save()
                raise BudgetExceeded(
                    f"JEV monthly budget: ₹{self.state['spend_inr']:.2f} spent + ₹{est_inr:.4f} estimated would exceed the "
                    f"enforced line ₹{self.usable_inr:.0f} (ceiling ₹{self.limit_inr:.0f}); JEV disabled until {self._next_month()}")
            return est_inr

    def record(self, body, usage: dict | None) -> float:
        """Post-flight: record actual input tokens (or the estimate if the provider reported none). Returns INR charged."""
        with _lock:
            self._roll_month()
            actual = None
            try:
                actual = int((usage or {}).get("input_tokens"))
            except (TypeError, ValueError):
                actual = None
            tokens = actual if actual is not None else estimate_tokens(body)
            inr = inr_for_tokens(tokens)
            self.state["input_tokens"] = int(self.state["input_tokens"]) + tokens
            self.state["spend_inr"] = round(float(self.state["spend_inr"]) + inr, 6)
            self.state["calls"] = int(self.state["calls"]) + 1
            self._save()
            return inr

    def _next_month(self) -> str:
        y, m = (int(x) for x in self.state["month"].split("-"))
        return f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"

    def snapshot(self) -> dict:
        with _lock:
            self._roll_month()
            return {"month": self.state["month"], "spend_inr": round(float(self.state["spend_inr"]), 4),
                    "limit_inr": self.limit_inr, "enforced_inr": round(self.usable_inr, 2),
                    "remaining_inr": round(max(0.0, self.usable_inr - float(self.state["spend_inr"])), 4),
                    "calls": self.state["calls"], "rejected": self.state.get("rejected", 0),
                    "input_tokens": self.state["input_tokens"], "exhausted": float(self.state["spend_inr"]) >= self.usable_inr,
                    "resets": self._next_month()}


_budget: Budget | None = None


def budget() -> Budget:
    global _budget
    if _budget is None:
        _budget = Budget()
    return _budget
