"""Lightweight, privacy-safe context features for the learned policy (§26).

A *context fingerprint* is a stable hash over structured local state — application, a coarse candidate signature set,
goal keywords, candidate count bucket. No screenshots, no raw private content; control names (e.g. "Save") are UI
labels, but they are still passed through the redactor before hashing to honour §24. The fingerprint is what the
contextual-bandit policy keys on; the *candidate signature* (role|name) is the arm.
"""
from __future__ import annotations

import hashlib
import re

_STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "click", "open", "select", "choose",
         "please", "this", "that", "it", "my", "me", "into", "then"}


def _redact(s: str) -> str:
    try:
        from ..safety import privacy
        return privacy.redact_text(s)
    except Exception:  # noqa: BLE001
        return s


def app_of(window: dict | None) -> str:
    p = ((window or {}).get("process") or "").lower()
    return p[:-4] if p.endswith(".exe") else p


def goal_tokens(goal: str, k: int = 4) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]{3,}", (goal or "").lower()) if t not in _STOP]
    return sorted(dict.fromkeys(toks))[:k]


def candidate_signature(el: dict) -> str:
    """Stable arm id for one candidate control: role + normalised name (redacted, truncated)."""
    role = str(el.get("role") or "?")
    name = _redact(str(el.get("name") or "")).strip().lower()[:60]
    return f"{role}|{name}"


def _n_bucket(n: int) -> str:
    return "2" if n <= 2 else ("3-4" if n <= 4 else ("5-8" if n <= 8 else "9+"))


def context_fingerprint(goal: str, candidates: list[dict], window: dict | None) -> str:
    """Deterministic 16-hex fingerprint of the decision context (app + goal keywords + candidate set shape)."""
    sigs = sorted(candidate_signature(c) for c in candidates)
    payload = "|".join([app_of(window), ",".join(goal_tokens(goal)), _n_bucket(len(candidates)), ";".join(sigs)])
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:16]


def signatures(candidates: list[dict]) -> dict[str, int]:
    """Map candidate signature → element id for the current observation (arm ↔ concrete control)."""
    out: dict[str, int] = {}
    for c in candidates:
        out.setdefault(candidate_signature(c), int(c["id"]))
    return out
