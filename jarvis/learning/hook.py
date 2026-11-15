"""The live DecisionHook that connects the learned policy to the router (§29, §42).

before_decide: consulted only for AMBIGUOUS candidate sets, after deterministic/unique/heuristic and BEFORE JEV. If the
production policy has a confident, safe, historically-verified arm for this context it returns that candidate id — so the
JEV call is avoided (a learned local decision replaces a paid reflex call). Otherwise it returns None and the router
proceeds to JEV exactly as before.

Safety and budget sit OUTSIDE this hook (§12, §28): the hook only ever returns an id from the router's existing safe
candidate pool, never invents an action, never calls JEV, and refuses to auto-serve a high-impact control (which still
goes through the confirm path). The learned policy cannot bypass a boundary because a blocked/denied action never
executes and therefore never earns positive reward.
"""
from __future__ import annotations

import logging

from ..safety import policy as safety_policy
from .manager import LearningManager

log = logging.getLogger("jarvis.learning.hook")


class LearningHook:
    def __init__(self, manager: LearningManager):
        self.m = manager

    def before_decide(self, goal: str, candidates: list[dict], context: dict) -> int | None:
        try:
            window = context.get("window") or {}
            fp, sig_map = self.m.record_decision(goal, candidates, window)
            el_id, conf, sig = self.m.serve(fp, sig_map)
            if el_id is None:
                return None
            el = next((c for c in candidates if int(c["id"]) == int(el_id)), None)
            if el is None:
                return None
            # never auto-serve a high-impact control: leave it to the normal path (which confirms with the human)
            if safety_policy.classify_click(el, window).level != safety_policy.SAFE:
                return None
            self.m.note_served(sig, conf)
            self.m.counters["jev_avoided"] += 1
            return int(el_id)
        except Exception as e:  # noqa: BLE001 — a hook must never break routing
            log.debug("before_decide: %s", e)
            return None

    def after_outcome(self, goal: str, chosen: dict | None, tier: str, verified: bool, context: dict) -> None:
        try:
            self.m.record_outcome(
                chosen, tier, verified,
                retry_count=1 if context.get("retried") else 0,
                denied=bool(context.get("denied")), blocked=bool(context.get("blocked")),
                latency_ms=context.get("latency_ms"),
                ocr_used=(tier == "ocr"), vision_used=(tier == "vision"),
                screenshots=int(bool(context.get("screenshot"))),
            )
        except Exception as e:  # noqa: BLE001
            log.debug("after_outcome: %s", e)
