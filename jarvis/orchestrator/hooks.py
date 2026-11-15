"""Decision hooks — the future RL integration point (NOT active; nothing is registered by default).

The router already exposes the two signals an RL layer needs:
  * a decision point with structured candidates (router.resolve_target → JEV / heuristic / OCR tiers), and
  * outcome records (memory.learn_batch / learn_routing, verification results, metrics tier counts).

A future policy plugs in by registering a `DecisionHook`; the router consults `before_decide` only for AMBIGUOUS
candidate sets (never for deterministic or unique targets) and reports `after_outcome` for every executed action.
Returning None from `before_decide` leaves the existing hierarchy untouched, so registering a hook cannot break routing.
"""
from __future__ import annotations

from typing import Protocol

_hooks: list["DecisionHook"] = []


class DecisionHook(Protocol):
    def before_decide(self, goal: str, candidates: list[dict], context: dict) -> int | None:
        """Return a candidate id to act on, or None to defer to the normal tiers (JEV → OCR → vision)."""

    def after_outcome(self, goal: str, chosen: dict | None, tier: str, verified: bool, context: dict) -> None:
        """Observe the result of an action (reward signal)."""


def register(hook: "DecisionHook") -> None:
    _hooks.append(hook)


def clear() -> None:
    _hooks.clear()


def before_decide(goal: str, candidates: list[dict], context: dict) -> int | None:
    for h in _hooks:
        try:
            r = h.before_decide(goal, candidates, context)
            if r is not None:
                return int(r)
        except Exception:  # noqa: BLE001 — a hook must never break routing
            continue
    return None


def after_outcome(goal: str, chosen: dict | None, tier: str, verified: bool, context: dict) -> None:
    for h in _hooks:
        try:
            h.after_outcome(goal, chosen, tier, verified, context)
        except Exception:  # noqa: BLE001
            continue
