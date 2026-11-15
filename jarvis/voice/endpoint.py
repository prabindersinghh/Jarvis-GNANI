"""Adaptive end-of-turn detection + turn state machine (Performance/UX directive §6, §15, §52).

The legacy recorder finalized a turn on one fixed rule: trailing silence > `silence_s` (0.9 s). That single constant
cannot serve both "Open Chrome." (wants a fast finalize) and "Open Chrome… and then… actually Edge." (must tolerate a
mid-thought pause). This module replaces it with an *adaptive* endpointer whose required trailing silence is a function
of what was just said, driven by a small explicit turn state machine.

Pure, deterministic, hardware-free: `feed(rms, now)` per audio frame → a `Turn` state. Unit-tested offline; the
`Recorder` feeds it real mic frames. No model, no network. `legacy_should_finalize` reproduces the old rule so the
latency harness can compare the two head-to-head on the same synthetic envelopes.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from .. import config


class Turn(enum.Enum):
    IDLE = "idle"                 # armed, no speech yet
    USER_SPEAKING = "speaking"    # energy above threshold
    POSSIBLE_END = "possible_end"  # speech stopped; trailing-silence timer running, not yet past the adaptive bound
    FINALIZED = "finalized"       # trailing silence exceeded the adaptive bound → the turn is the user's, transcribe it


@dataclass
class EndpointConfig:
    energy_threshold: float = 0.012   # RMS above this = speech (matches the legacy Recorder default)
    release_ratio: float = 0.6        # hysteresis: only count silence once RMS drops below threshold*ratio
    min_speech_s: float = 0.25        # ignore blips shorter than this
    fast_silence_s: float = 0.55      # trailing silence to finalize a short, fluent command (snappier than legacy 0.9)
    base_silence_s: float = 0.8       # default trailing silence (slightly under legacy 0.9, still safe)
    max_silence_s: float = 1.3        # trailing silence tolerated ONCE the speaker has shown hesitation
    short_utt_s: float = 1.4          # utterances shorter than this (and fluent) are "command-like" → fast finalize
    hesitation_pause_s: float = 0.35  # a mid-utterance gap at least this long, later bridged by speech, = hesitating

    @classmethod
    def from_settings(cls) -> "EndpointConfig":
        s = config.SETTINGS
        return cls(
            energy_threshold=float(s.get("vad_energy_threshold", 0.012)),
            fast_silence_s=float(s.get("endpoint_fast_silence_s", 0.55)),
            base_silence_s=float(s.get("endpoint_base_silence_s", 0.8)),
            max_silence_s=float(s.get("endpoint_max_silence_s", 1.3)),
            short_utt_s=float(s.get("endpoint_short_utt_s", 1.4)),
        )


def legacy_should_finalize(spoke: bool, now: float, last_voice: float | None, t0: float,
                           silence_s: float = 0.9, min_speech_s: float = 0.25) -> bool:
    """The exact pre-optimization rule (mic.py) — kept only for the latency harness's before/after comparison."""
    return bool(spoke and last_voice is not None
                and (now - last_voice) > silence_s
                and (now - t0) > min_speech_s + silence_s)


class AdaptiveEndpointer:
    """Frame-driven end-of-turn detector. Feed one RMS value per audio frame; read `.state` and `.finalized`.

    Adaptive bound: the trailing silence required to finalize depends on the utterance so far —
      * short and fluent (no hesitation pauses, < short_utt_s of speech) → `fast_silence_s`   (snappy commands)
      * hesitant (had a >= hesitation_pause_s gap) or long                → `max_silence_s`    (don't cut people off)
      * everything in between                                            → `base_silence_s`
    Hysteresis (`release_ratio`) stops a brief dip mid-word from starting the silence timer.
    """

    def __init__(self, cfg: EndpointConfig | None = None):
        self.cfg = cfg or EndpointConfig()
        self.state = Turn.IDLE
        self.started_at: float | None = None      # first frame ever fed (arming time)
        self.speech_start: float | None = None     # first speech frame
        self.last_voice: float | None = None        # last frame above threshold
        self.total_speech_s = 0.0
        self.longest_pause_s = 0.0
        self.hesitated = False
        self.spoke = False
        self._in_speech = False                      # hysteresis latch
        self._pause_start: float | None = None

    # -- introspection -------------------------------------------------------------
    @property
    def finalized(self) -> bool:
        return self.state is Turn.FINALIZED

    def required_silence_s(self) -> float:
        """The adaptive trailing-silence bound given everything heard so far.

        Only an *observed* hesitation (a mid-utterance gap that the speaker then bridged with more speech) raises the
        bound — a long but fluent dictation stays on the snappy base bound, and a short fluent command finalizes fast.
        """
        c = self.cfg
        if self.hesitated:
            return c.max_silence_s
        if self.spoke and self.total_speech_s <= c.short_utt_s:
            return c.fast_silence_s
        return c.base_silence_s

    # -- the state machine ---------------------------------------------------------
    def feed(self, rms: float, now: float) -> Turn:
        c = self.cfg
        if self.started_at is None:
            self.started_at = now
        if self.finalized:
            return self.state

        # hysteresis: enter speech above threshold, leave only below threshold*release_ratio
        if not self._in_speech and rms > c.energy_threshold:
            self._in_speech = True
        elif self._in_speech and rms < c.energy_threshold * c.release_ratio:
            self._in_speech = False

        if self._in_speech:
            # a gap that just ended → record it; a long one marks hesitation
            if self._pause_start is not None:
                gap = now - self._pause_start
                self.longest_pause_s = max(self.longest_pause_s, gap)
                if self.spoke and gap >= c.hesitation_pause_s:
                    self.hesitated = True
                self._pause_start = None
            if self.speech_start is None:
                self.speech_start = now
            if self.last_voice is not None:
                self.total_speech_s += min(now - self.last_voice, 0.1)  # bounded so one frame can't over-count
            self.spoke = True
            self.last_voice = now
            self.state = Turn.USER_SPEAKING
            return self.state

        # below threshold (silence)
        if not self.spoke:
            self.state = Turn.IDLE
            return self.state
        if self._pause_start is None:
            self._pause_start = self.last_voice if self.last_voice is not None else now
        # last_voice is not None here (self.spoke is True); avoid `or` so a legitimate 0.0 timestamp is not treated as unset
        trailing = now - (self.last_voice if self.last_voice is not None else now)
        if trailing < self.required_silence_s():
            self.state = Turn.POSSIBLE_END
            return self.state
        # enough trailing silence AND a real utterance happened
        started = self.started_at if self.started_at is not None else now
        if (now - started) > c.min_speech_s + self.required_silence_s():
            self.state = Turn.FINALIZED
        else:
            self.state = Turn.POSSIBLE_END
        return self.state
