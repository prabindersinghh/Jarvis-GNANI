"""Explicit reward model (§7, §9). Verification is the authority — 'input was sent' is never success.

reward ∈ [-2, 2]. Correctness/safety dominate; latency and cost are secondary and never override them.
A safety/security violation is never learned toward: a blocked/denied action yields a hard negative and is flagged so
the policy learns the boundary itself, not a way around it (§28).
"""
from __future__ import annotations

from dataclasses import dataclass

# tier cost (per-decision expense the policy should learn to minimise where safe)
_TIER_COST = {"deterministic": 0.0, "uia": 0.0, "policy": 0.0, "heuristic": 0.0, "jev": 0.06, "ocr": 0.04, "vision": 0.12, "human": 0.0}


@dataclass
class Reward:
    value: float
    verified: bool
    blocked: bool
    detail: dict


def compute(verified: bool, tier: str, latency_ms: float | None = None, baseline_ms: float | None = None,
            retry_count: int = 0, fallback_count: int = 0, jev_used: bool = False, ocr_used: bool = False,
            vision_used: bool = False, screenshots: int = 0, user_correction: bool = False,
            blocked: bool = False, denied: bool = False, recovered: bool = False,
            known_workflow: bool = False) -> Reward:
    detail: dict = {}
    if blocked or denied:
        # safety boundary hit: never a positive signal; strong negative, flagged (the action did not/should not execute)
        return Reward(-1.5, False, True, {"reason": "blocked/denied"})
    if user_correction:
        # the user overrode JARVIS — the strongest negative for the chosen action (§8)
        return Reward(-1.5, verified, False, {"reason": "user_correction"})

    r = 1.2 if verified else -1.2                         # VERIFIED_SUCCESS / VERIFICATION_FAILURE (authority: §9)
    if verified and retry_count == 0:
        r += 0.15; detail["first_attempt"] = True         # FIRST_ATTEMPT_SUCCESS bonus
    r -= 0.15 * max(0, retry_count)                       # RETRY_REQUIRED penalty
    r -= 0.10 * max(0, fallback_count)
    r -= _TIER_COST.get(tier, 0.0)                        # UNNECESSARY_MODEL/JEV/OCR/vision cost
    if jev_used:
        r -= 0.02
    if ocr_used:
        r -= 0.02
    if vision_used or screenshots:
        r -= 0.05 * (1 + int(screenshots))
    if verified and recovered:
        r += 0.15                                         # SUCCESSFUL_RECOVERY
    if verified and known_workflow:
        r += 0.10                                         # KNOWN_WORKFLOW_REUSE
    # latency vs baseline (bounded ±0.2); only meaningful when verified
    if verified and latency_ms is not None and baseline_ms:
        if latency_ms < baseline_ms:
            r += min(0.2, 0.2 * (baseline_ms - latency_ms) / max(1.0, baseline_ms))
        else:
            r -= min(0.15, 0.15 * (latency_ms - baseline_ms) / max(1.0, baseline_ms))
    r = max(-2.0, min(2.0, r))
    return Reward(round(r, 4), verified, False, detail)
