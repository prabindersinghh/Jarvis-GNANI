"""Barge-in detection (Performance/UX directive §10, §13, §41).

The legacy recorder simply DROPPED every mic frame while JARVIS was speaking (mic.py `suppress`), which makes voice
barge-in impossible in hands-free mode — only the push-to-talk hotkey could interrupt. This detector is fed those same
frames instead of discarding them: when it sees *sustained* user speech above a raised threshold it fires once, and the
app stops TTS and starts listening. The raised threshold + sustain window are the software backstop against a cough, a
click, or residual self-echo triggering a false interruption even though the mic array also does hardware AEC.

Pure and deterministic: `feed(rms, now) -> bool`. Unit-tested offline; the recorder feeds it real frames.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import config


@dataclass
class BargeInConfig:
    threshold: float = 0.035    # RMS to count as the user speaking OVER JARVIS (above the 0.012 VAD floor AND TTS echo/ambient)
    sustain_s: float = 0.22     # speech must persist this long → not a cough/click (§41)
    reset_gap_s: float = 0.12   # a silence gap longer than this resets the run (speech must be continuous)

    @classmethod
    def from_settings(cls) -> "BargeInConfig":
        s = config.SETTINGS
        return cls(threshold=float(s.get("bargein_threshold", 0.035)),
                   sustain_s=float(s.get("bargein_sustain_s", 0.22)))


class BargeInDetector:
    def __init__(self, cfg: BargeInConfig | None = None):
        self.cfg = cfg or BargeInConfig()
        self._run_start: float | None = None    # start of the current above-threshold run
        self._last_loud: float | None = None
        self.fired = False

    def reset(self):
        self._run_start = None
        self._last_loud = None
        self.fired = False

    def feed(self, rms: float, now: float) -> bool:
        """Return True exactly once, when sustained over-speech is confirmed. Caller then stops TTS and listens."""
        if self.fired:
            return False
        c = self.cfg
        if rms >= c.threshold:
            if self._run_start is None or (self._last_loud is not None and now - self._last_loud > c.reset_gap_s):
                self._run_start = now            # (re)start the run
            self._last_loud = now
            if now - self._run_start >= c.sustain_s:
                self.fired = True
                return True
        elif self._last_loud is not None and now - self._last_loud > c.reset_gap_s:
            self._run_start = None               # gap too long → the blip was not speech
        return False
