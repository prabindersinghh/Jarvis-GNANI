"""Learned policy — a contextual bandit over the router's discrete candidate arms (§4 Phase A, §5, §19-21, §27).

Per context fingerprint we keep per-arm statistics (n, reward sum, verified successes). `pick` returns an arm only when
it has enough samples AND a high lower-confidence-bound on verified success — otherwise None (defer to JEV/normal
tiers). Two instances exist: PRODUCTION (serves live decisions, conservative) and CANDIDATE (learns online); the
manager promotes candidate→production only on measured improvement and rolls back on regression. Persistence is atomic
and versioned so a crash/partial write never corrupts the production policy (§23).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

MIN_N = 3            # arm needs at least this many trials before it can be chosen
MIN_LCB = 0.72       # lower-confidence-bound on verified success required to serve an arm
Z = 1.28             # ~80% one-sided confidence for the Wilson lower bound


def _wilson_lcb(succ: int, n: int, z: float = Z) -> float:
    if n <= 0:
        return 0.0
    p = succ / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


class BanditPolicy:
    def __init__(self, version: int = 0, parent: int | None = None):
        # stats[fp][sig] = [n, reward_sum, succ]
        self.stats: dict[str, dict[str, list]] = {}
        self.version = version
        self.parent = parent
        self.created = time.time()
        self.metrics: dict = {}

    # -- learning ------------------------------------------------------------------
    def update(self, fp: str, sig: str, reward: float, success: bool):
        arm = self.stats.setdefault(fp, {}).setdefault(sig, [0, 0.0, 0])
        arm[0] += 1
        arm[1] += float(reward)
        arm[2] += int(bool(success))

    def value(self, fp: str, sig: str) -> float:
        arm = self.stats.get(fp, {}).get(sig)
        return arm[1] / arm[0] if arm and arm[0] else 0.0

    def success_rate(self, fp: str, sig: str) -> float:
        arm = self.stats.get(fp, {}).get(sig)
        return arm[2] / arm[0] if arm and arm[0] else 0.0

    def n(self, fp: str, sig: str | None = None) -> int:
        if sig is None:
            return sum(a[0] for a in self.stats.get(fp, {}).values())
        arm = self.stats.get(fp, {}).get(sig)
        return arm[0] if arm else 0

    # -- serving -------------------------------------------------------------------
    def pick(self, fp: str, avail_sigs, min_n: int = MIN_N, min_lcb: float = MIN_LCB) -> tuple[str | None, float]:
        """Best available arm whose verified-success LCB clears the bar, else (None, 0)."""
        best_sig, best_lcb = None, min_lcb
        for sig in avail_sigs:
            arm = self.stats.get(fp, {}).get(sig)
            if not arm or arm[0] < min_n:
                continue
            lcb = _wilson_lcb(arm[2], arm[0])
            if lcb >= best_lcb and (arm[1] / arm[0]) > 0:   # positive mean reward and confident success
                best_sig, best_lcb = sig, lcb
        return best_sig, round(best_lcb, 3) if best_sig else 0.0

    # -- offline evaluation for promotion (§19-20) --------------------------------
    # what serving an arm LOCALLY (tier 'policy', no JEV/OCR/vision cost) is worth, decoupled from the tier that
    # happened to collect the data. Success rate → local reward; deferring → the actually-observed reward.
    LOCAL_SUCCESS = 1.35
    LOCAL_FAIL = -1.2

    def estimated_value(self, experiences: list[dict], min_n: int = MIN_N) -> tuple[float, int]:
        """Value of following this policy over recorded experiences:
          - it would DEFER (pick None) → credit the observed reward (the same tier outcome would occur), or
          - it would SERVE an arm → credit its verified-success rate mapped to a local reward (JEV/OCR cost avoided).
        This is what makes a reliable local pick score above deferring to a paid JEV call. Returns (mean, n)."""
        vals, used = [], 0
        for e in experiences:
            fp, chosen, rw = e.get("context_fingerprint"), e.get("chosen_action"), e.get("reward")
            if fp is None or rw is None:
                continue
            avail = set(self.stats.get(fp, {}).keys()) | ({chosen} if chosen else set())
            pick, _ = self.pick(fp, avail, min_n=min_n)
            if pick is None:
                vals.append(float(rw))
            else:
                sr = self.success_rate(fp, pick)
                vals.append(sr * self.LOCAL_SUCCESS + (1 - sr) * self.LOCAL_FAIL)
            used += 1
        return (sum(vals) / len(vals) if vals else 0.0), used

    # -- persistence (atomic, crash-safe) -----------------------------------------
    def to_dict(self) -> dict:
        return {"version": self.version, "parent": self.parent, "created": self.created,
                "metrics": self.metrics, "stats": self.stats}

    @classmethod
    def from_dict(cls, d: dict) -> "BanditPolicy":
        p = cls(int(d.get("version", 0)), d.get("parent"))
        p.created = d.get("created", time.time())
        p.metrics = d.get("metrics", {})
        p.stats = {fp: {sig: list(v) for sig, v in arms.items()} for fp, arms in (d.get("stats") or {}).items()}
        return p

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.to_dict(), ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)               # atomic on Windows/NTFS
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass

    @classmethod
    def load(cls, path: Path) -> "BanditPolicy | None":
        try:
            if not path.exists():
                return None
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — corrupt/partial file: caller falls back to parent/empty
            return None

    def copy(self) -> "BanditPolicy":
        return BanditPolicy.from_dict(json.loads(json.dumps(self.to_dict())))

    def summary(self) -> dict:
        arms = sum(len(a) for a in self.stats.values())
        return {"version": self.version, "parent": self.parent, "contexts": len(self.stats), "arms": arms}
