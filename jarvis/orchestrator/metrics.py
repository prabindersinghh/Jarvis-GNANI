"""Latency instrumentation (TRD §17): voice_ms claude_ms mcp_ms uia_ms jev_ms ocr_ms input_ms verification_ms total_ms.

Counters accumulate per process; `snapshot()` returns count/avg/max/last per key and
appends one JSON line to logs/metrics.jsonl when `flush()` is called.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict

from .. import config

KEYS = ("voice_ms", "claude_ms", "mcp_ms", "uia_ms", "observe_ms", "jev_ms", "ocr_ms", "input_ms",
        "verification_ms", "total_ms", "shell_ms", "file_ms")


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._n = defaultdict(int)
        self._sum = defaultdict(int)
        self._max = defaultdict(int)
        self._last = {}
        self.tier_counts = defaultdict(int)

    def add(self, key: str, ms: int | float) -> None:
        ms = int(ms)
        with self._lock:
            self._n[key] += 1
            self._sum[key] += ms
            self._max[key] = max(self._max[key], ms)
            self._last[key] = ms

    def tier(self, name: str) -> None:
        with self._lock:
            self.tier_counts[name] += 1

    def snapshot(self) -> dict:
        with self._lock:
            out = {k: {"n": self._n[k], "avg": round(self._sum[k] / self._n[k]), "max": self._max[k], "last": self._last[k]}
                   for k in self._n}
            out["tiers"] = dict(self.tier_counts)
            return out

    def flush(self, tag: str = "") -> None:
        try:
            config.LOGS.mkdir(parents=True, exist_ok=True)
            with open(config.LOGS / "metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "tag": tag, **self.snapshot()}) + "\n")
        except OSError:
            pass


class Timer:
    def __init__(self, key: str, m: "Metrics | None" = None):
        self.key, self.m = key, m or metrics

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.t0) * 1000)
        self.m.add(self.key, self.ms)


metrics = Metrics()
