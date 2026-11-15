"""Learning manager — experience store + production/candidate/parent policies + background worker (§18-23, §34, §38).

Flow: the router's decision hook calls `record_decision` then `record_outcome`. Each outcome → reward (verification is
authority) → experience row → candidate policy update → enqueue. A low-priority background worker periodically evaluates
the candidate against the production policy on recent experience and PROMOTES on measured improvement, keeping the
previous policy as the rollback parent; it also monitors live production performance and ROLLS BACK on regression.
Everything persists locally and atomically; a crash/partial write cannot corrupt the production policy or any existing
store (memory / history / budget ledger / safety config are never touched here).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime

from .. import config
from . import features, reward as rewardmod
from .experience import ExperienceStore
from .policy import BanditPolicy

log = logging.getLogger("jarvis.learning")

POL_DIR = config.DATA / "policies"
MET_DIR = config.DATA / "metrics"
PROMOTE_MARGIN = 0.05      # candidate must beat production by this (estimated local value) to be promoted
MIN_EVAL = 6              # minimum experiences supporting an evaluation
MIN_ROLL = 6             # minimum served picks before a rollback can trigger
REG_MARGIN = 0.4         # production live value must fall this far below its promotion baseline to roll back
EVAL_EVERY = 20          # worker evaluates after this many new experiences (or on flush)


class LearningManager:
    def __init__(self, exp_dir=None, pol_dir=POL_DIR):
        self.pol_dir = pol_dir
        self.exp = ExperienceStore(**({"path": exp_dir} if exp_dir else {}))
        self.enabled = bool(config.SETTINGS.get("learning_enabled", True))
        # production serves; candidate learns; parent is the rollback target
        self.production = BanditPolicy.load(pol_dir / "production.json") or BanditPolicy(version=1)
        self.parent = BanditPolicy.load(pol_dir / "parent.json") or self.production.copy()
        self.candidate = BanditPolicy.load(pol_dir / "candidate.json") or self.production.copy()
        self._pending: dict[int, dict] = {}        # thread id → decision context awaiting its outcome
        self._roll: deque = deque(maxlen=40)        # recent rewards of PRODUCTION-served picks (rollback monitor)
        self._lock = threading.RLock()
        self._since_eval = 0
        self.learning = False                        # True while the worker is actively processing (orb state)
        self.counters = {"experiences": self.exp.count(), "jev_avoided": 0, "promotions": 0, "rollbacks": 0,
                         "served": 0, "corrections": 0}
        self.last_promotion = self.production.metrics.get("promoted_at")
        self.last_rollback = None
        self._baseline_v = self.production.metrics.get("baseline_v", 0.0)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._worker = None

    # -- lifecycle ----------------------------------------------------------------
    def start(self):
        if self._worker or not self.enabled:
            return
        self._worker = threading.Thread(target=self._run, name="learning", daemon=True)
        self._worker.start()
        log.info("learning worker started (prod v%s, %d contexts, %d experiences)",
                 self.production.version, len(self.production.stats), self.counters["experiences"])

    def stop(self):
        self._stop.set()
        self._wake.set()
        try:
            self._save_all()
        except Exception as e:  # noqa: BLE001
            log.warning("learning save on stop failed: %s", e)

    def _run(self):
        try:
            import os
            os.nice  # noqa
        except Exception:  # noqa: BLE001
            pass
        self._lower_priority()
        while not self._stop.is_set():
            self._wake.wait(timeout=15)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.learning = True
                self._maybe_evaluate()
                self._maybe_rollback()
                self._save_all()
            except Exception as e:  # noqa: BLE001
                log.debug("learning cycle: %s", e)
            finally:
                self.learning = False

    def _lower_priority(self):
        try:
            import ctypes
            THREAD_PRIORITY_BELOW_NORMAL = -1
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
        except Exception:  # noqa: BLE001
            pass

    # -- online recording (called from the router hook) ---------------------------
    def record_decision(self, goal: str, candidates: list[dict], window: dict) -> tuple[str, dict]:
        """Compute the context fingerprint and stash a pending decision for this thread. Returns (fp, sig->id map)."""
        fp = features.context_fingerprint(goal, candidates, window)
        sig_map = features.signatures(candidates)
        self._pending[threading.get_ident()] = {"fp": fp, "sig_map": sig_map, "t0": time.perf_counter(),
                                                 "goal": goal, "window": window, "n": len(candidates), "served": None}
        return fp, sig_map

    def note_served(self, sig: str, confidence: float):
        p = self._pending.get(threading.get_ident())
        if p is not None:
            p["served"] = sig
            p["confidence"] = confidence
            self.counters["served"] += 1

    def record_outcome(self, chosen: dict | None, tier: str, verified: bool, *, retry_count: int = 0,
                       fallback_count: int = 0, denied: bool = False, blocked: bool = False,
                       user_correction: bool = False, latency_ms: float | None = None,
                       ocr_used: bool = False, vision_used: bool = False, screenshots: int = 0):
        p = self._pending.pop(threading.get_ident(), None)
        if p is None or not self.enabled:
            return
        sig = features.candidate_signature(chosen) if chosen else (p.get("served") or "")
        jev_used = (tier == "jev")
        lat = latency_ms if latency_ms is not None else (time.perf_counter() - p["t0"]) * 1000
        r = rewardmod.compute(verified=verified, tier=tier, latency_ms=lat, baseline_ms=p.get("baseline_ms"),
                              retry_count=retry_count, fallback_count=fallback_count, jev_used=jev_used,
                              ocr_used=ocr_used, vision_used=vision_used, screenshots=screenshots,
                              user_correction=user_correction, blocked=blocked, denied=denied)
        with self._lock:
            # candidate always learns; a blocked/denied action teaches the boundary (negative), never a bypass (§28)
            if sig:
                self.candidate.update(p["fp"], sig, r.value, r.verified and not r.blocked)
            if p.get("served") is not None:              # production actually served this pick → rollback monitor
                self._roll.append(r.value)
            if user_correction:
                self.counters["corrections"] += 1
            self._record_experience(p, sig, tier, r, verified, retry_count, fallback_count, jev_used, ocr_used,
                                    vision_used, screenshots, denied or blocked)
            self._since_eval += 1
            self.counters["experiences"] += 1
        if self._since_eval >= EVAL_EVERY:
            self._wake.set()

    def record_correction(self, goal: str, wrong: dict | None, corrected: dict | None, window: dict, verified: bool = True):
        """Explicit user correction (§8): strong negative for the wrong arm, positive for the corrected one."""
        if not self.enabled:
            return
        fp = features.context_fingerprint(goal, [c for c in (wrong, corrected) if c], window)
        with self._lock:
            if wrong:
                self.candidate.update(fp, features.candidate_signature(wrong), rewardmod.compute(False, "policy", user_correction=True).value, False)
            if corrected:
                self.candidate.update(fp, features.candidate_signature(corrected), 1.2, True)
            self.counters["corrections"] += 1
        self._wake.set()

    def _record_experience(self, p, sig, tier, r, verified, retry, fallback, jev, ocr, vision, shots, blocked):
        try:
            from ..history import history
            sid = history().session_id
        except Exception:  # noqa: BLE001
            sid = None
        self.exp.record({
            "session_id": sid, "task_id": uuid.uuid4().hex[:10], "application": features.app_of(p["window"]),
            "window_context": (p["window"] or {}).get("title"), "action_type": "click", "routing_tier": tier,
            "policy_version": self.production.version, "candidate_count": p["n"], "chosen_action": sig,
            "confidence": p.get("confidence"), "jev_used": jev, "ocr_used": ocr, "vision_used": vision,
            "claude_used": False, "batch_used": False, "latency_ms": round((time.perf_counter() - p["t0"]) * 1000),
            "verification_result": verified, "success": verified and not blocked, "failure_reason": (r.detail or {}).get("reason"),
            "retry_count": retry, "fallback_count": fallback, "user_correction": (r.detail or {}).get("reason") == "user_correction",
            "safety_classification": "blocked" if blocked else "safe", "reward": r.value,
            "context_fingerprint": p["fp"], "source": "real_user",
        })

    # -- serving (production only) -------------------------------------------------
    def serve(self, fp: str, sig_map: dict) -> tuple[int | None, float, str | None]:
        """Production policy's pick for this context, or (None, 0, None) to defer to JEV/normal tiers."""
        if not self.enabled:
            return None, 0.0, None
        sig, conf = self.production.pick(fp, set(sig_map.keys()))
        if sig is None:
            return None, 0.0, None
        return sig_map.get(sig), conf, sig

    # -- promotion / rollback (§19-21) --------------------------------------------
    def _maybe_evaluate(self):
        recent = self.exp.recent(400)
        if len(recent) < MIN_EVAL:
            self._since_eval = 0
            return
        with self._lock:
            cand_v, n_c = self.candidate.estimated_value(recent)
            prod_v, n_p = self.production.estimated_value(recent)
            self._since_eval = 0
            if n_c >= MIN_EVAL and cand_v >= prod_v + PROMOTE_MARGIN and self.candidate.summary()["arms"] >= self.production.summary()["arms"]:
                self._promote(cand_v, prod_v)

    def _promote(self, cand_v: float, prod_v: float):
        self.parent = self.production.copy()             # last known good
        newv = self.production.version + 1
        self.candidate.version = newv
        self.candidate.parent = self.production.version
        self.candidate.metrics = {"promoted_at": datetime.now().isoformat(timespec="seconds"),
                                  "baseline_v": round(cand_v, 4), "prev_prod_v": round(prod_v, 4),
                                  "reason": f"candidate {cand_v:.3f} ≥ production {prod_v:.3f} + {PROMOTE_MARGIN}"}
        self.production = self.candidate.copy()
        self.candidate = self.production.copy()
        self._baseline_v = round(cand_v, 4)
        self._roll.clear()
        self.counters["promotions"] += 1
        self.last_promotion = self.production.metrics["promoted_at"]
        log.info("policy PROMOTED to v%s (cand %.3f ≥ prod %.3f)", newv, cand_v, prod_v)

    def _maybe_rollback(self):
        if len(self._roll) < MIN_ROLL:
            return
        live_v = sum(self._roll) / len(self._roll)
        if self.production.version > 1 and live_v < self._baseline_v - REG_MARGIN:
            with self._lock:
                self._rollback(live_v)

    def _rollback(self, live_v: float):
        restored = self.parent.copy()
        restored.metrics = {"rolled_back_from": self.production.version, "at": datetime.now().isoformat(timespec="seconds"),
                            "live_v": round(live_v, 4), "baseline_v": self._baseline_v,
                            "reason": f"live {live_v:.3f} < baseline {self._baseline_v:.3f} − {REG_MARGIN}"}
        self.production = restored
        self.candidate = self.production.copy()
        self._baseline_v = self.parent.metrics.get("baseline_v", 0.0)
        self._roll.clear()
        self.counters["rollbacks"] += 1
        self.last_rollback = restored.metrics["at"]
        log.warning("policy ROLLED BACK to v%s (live %.3f)", restored.version, live_v)

    def flush(self):
        """Force an evaluation/rollback cycle now (used by benchmarks/tests; the worker does this in the background)."""
        self._maybe_evaluate()
        self._maybe_rollback()
        self._save_all()

    # -- persistence ---------------------------------------------------------------
    def _save_all(self):
        with self._lock:
            self.production.save(self.pol_dir / "production.json")
            self.parent.save(self.pol_dir / "parent.json")
            self.candidate.save(self.pol_dir / "candidate.json")

    # -- observability (§38) -------------------------------------------------------
    def status(self) -> dict:
        live = (sum(self._roll) / len(self._roll)) if self._roll else None
        return {
            "enabled": self.enabled, "worker": ("running" if self._worker and self._worker.is_alive() else "stopped"),
            "learning_now": self.learning, "production_version": self.production.version,
            "candidate_version": self.candidate.version, "parent_version": getattr(self.parent, "version", None),
            "contexts": len(self.production.stats), "arms": self.production.summary()["arms"],
            "experiences": self.counters["experiences"], "served_by_policy": self.counters["served"],
            "jev_avoided": self.counters["jev_avoided"], "corrections": self.counters["corrections"],
            "promotions": self.counters["promotions"], "rollbacks": self.counters["rollbacks"],
            "last_promotion": self.last_promotion, "last_rollback": self.last_rollback,
            "baseline_reward": self._baseline_v, "live_reward": (round(live, 3) if live is not None else None),
        }

    def daily_summary(self, date: str | None = None) -> dict:
        date = date or datetime.now().strftime("%Y-%m-%d")
        rows = self.exp.day(date)
        n = len(rows)
        succ = sum(1 for r in rows if r.get("success"))
        jev = sum(1 for r in rows if r.get("jev_used"))
        pol = sum(1 for r in rows if r.get("routing_tier") == "policy")
        corr = sum(1 for r in rows if r.get("user_correction"))
        lat = [r["latency_ms"] for r in rows if r.get("latency_ms")]
        summary = {"date": date, "tasks": n, "success_rate": round(succ / n, 3) if n else None,
                   "corrections": corr, "jev_calls": jev, "jev_avoided": pol,
                   "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
                   "policy_version": self.production.version, "promotions": self.counters["promotions"],
                   "rollbacks": self.counters["rollbacks"]}
        try:
            MET_DIR.mkdir(parents=True, exist_ok=True)
            (MET_DIR / f"daily_{date}.json").write_text(__import__("json").dumps(summary, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return summary


_manager: LearningManager | None = None


def manager() -> LearningManager:
    global _manager
    if _manager is None:
        _manager = LearningManager()
    return _manager
