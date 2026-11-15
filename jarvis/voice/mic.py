"""Microphone capture with push-to-talk and energy VAD end-pointing.

Modes:
  hold   — record while the hotkey is held; stop on release.
  toggle — tap the hotkey to start; recording stops on the next tap or when
           trailing silence (energy VAD) exceeds `silence_s`.
Both are bounded by `max_utterance_s`.

Audio is captured with sounddevice at the device rate and resampled to 16 kHz
mono float32 for Gnani Prisma.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time

import numpy as np

from .. import config
from .bargein import BargeInDetector
from .endpoint import AdaptiveEndpointer, EndpointConfig, Turn

log = logging.getLogger("jarvis.voice.mic")
TARGET_SR = 16000


def _resample(x: np.ndarray, sr_in: int, sr_out: int = TARGET_SR) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    xp = np.linspace(0, len(x) - 1, n_out)
    return np.interp(xp, np.arange(len(x)), x).astype(np.float32)


class Recorder:
    def __init__(self, device=None, silence_s: float = 0.9, energy_threshold: float = 0.012, min_speech_s: float = 0.25,
                 suppress=None, on_bargein=None):
        s = config.SETTINGS
        self.suppress = suppress  # callable → True while JARVIS is speaking: frames are dropped (no self-listening)
        self.on_bargein = on_bargein  # called (once) from the audio thread when the user speaks over JARVIS (§10, §13)
        self.device = device if device is not None else s.get("mic_device")
        if isinstance(self.device, str) and self.device.strip().lstrip("-").isdigit():
            self.device = int(self.device)
        self.silence_s = silence_s
        self.energy_threshold = float(s.get("vad_energy_threshold", energy_threshold))
        self.min_speech_s = min_speech_s
        self.max_s = float(s.get("max_utterance_s", 30))
        self.adaptive = bool(s.get("adaptive_endpointing", True))   # §6 adaptive end-of-turn vs the legacy fixed rule
        self.bargein = BargeInDetector()
        self._monitor = False                                       # True while we listen for barge-in during TTS
        self._q: queue.Queue = queue.Queue()
        self._stream = None
        self._sr = TARGET_SR
        self._recording = False
        self._stop_evt = threading.Event()
        self.level = 0.0  # last RMS, for UI meters
        self.spoke = False  # did the last record() see any frame above the energy threshold?
        self.suppressed_frames = 0

    def set_monitor(self, on: bool):
        """Enable/disable barge-in monitoring. Armed whenever JARVIS is busy (speaking OR working a task) so the user
        can cut in by voice at any time; the audio callback feeds frames to the detector instead of dropping them.
        Resets the detector only on an OFF→ON transition, so calling this repeatedly while already monitoring never
        discards a partial detection run."""
        on = bool(on)
        if on != self._monitor:
            log.info("barge-in monitor %s", "ARMED (JARVIS busy — you can cut in)" if on else "off")
            if on:
                self.bargein.reset()
        self._monitor = on

    # -- device -------------------------------------------------------------------------
    def open(self):
        import sounddevice as sd
        if self._stream is not None:
            return
        info = sd.query_devices(self.device, "input") if self.device is not None else sd.query_devices(kind="input")
        self._sr = int(info["default_samplerate"]) or TARGET_SR
        self._stream = sd.InputStream(samplerate=self._sr, channels=1, dtype="float32", device=self.device,
                                      blocksize=int(self._sr * 0.03), callback=self._cb)
        self._stream.start()
        log.info("mic open: %s @ %d Hz", info["name"], self._sr)

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None

    def _cb(self, indata, frames, t, status):
        if status:
            log.debug("mic status: %s", status)
        mono = indata[:, 0]
        if self._recording:
            if self.suppress is not None and self.suppress():
                self.suppressed_frames += 1
                return
            self._q.put(mono.copy())
        elif self._monitor and self.on_bargein is not None:
            # not recording, but JARVIS is speaking in a hands-free conversation → watch for the user cutting in.
            rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
            if self.bargein.feed(rms, time.perf_counter()):
                cb, self._monitor = self.on_bargein, False
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    log.debug("bargein callback failed", exc_info=True)

    # -- recording ----------------------------------------------------------------------
    def start(self):
        """Arm the recorder. Cheap and synchronous (no device open) so a stop() that follows can never be lost;
        the stream is opened lazily inside record() (or pre-warmed via open())."""
        with self._q.mutex:
            self._q.queue.clear()
        self._stop_evt.clear()
        self._recording = True

    def stop(self):
        self._stop_evt.set()

    @property
    def recording(self) -> bool:
        return self._recording

    def record(self, auto_stop: bool = True, on_level=None) -> np.ndarray:
        """Block until stop() (or silence/max) and return 16 kHz float32 audio. Call start() first."""
        test_wav = os.environ.get("JARVIS_TEST_AUDIO")
        if test_wav:  # test hook: feed a WAV file through the exact same path instead of the microphone
            self._stop_evt.wait(1.0)
            self._recording = False
            self.spoke = True
            return load_wav(test_wav)
        self.open()
        chunks: list[np.ndarray] = []
        t0 = time.perf_counter()
        last_voice = None
        spoke = self.spoke = False
        ep = AdaptiveEndpointer(EndpointConfig.from_settings()) if self.adaptive else None
        try:
            while True:
                try:
                    c = self._q.get(timeout=0.05)
                    chunks.append(c)
                    rms = float(np.sqrt(np.mean(c * c))) if len(c) else 0.0
                    self.level = rms
                    if on_level:
                        on_level(rms)
                    now = time.perf_counter()
                    if rms > self.energy_threshold:
                        last_voice = now
                        spoke = self.spoke = True
                    if auto_stop:
                        if ep is not None:
                            if ep.feed(rms, now) is Turn.FINALIZED:
                                break
                        elif spoke and last_voice and (now - last_voice) > self.silence_s and (now - t0) > self.min_speech_s + self.silence_s:
                            break
                except queue.Empty:
                    pass
                if self._stop_evt.is_set() or (time.perf_counter() - t0) > self.max_s:
                    break
        finally:
            self._recording = False
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks)
        audio = _resample(audio, self._sr)
        # trim trailing silence tail beyond 0.3 s for latency
        return audio


def list_input_devices() -> list[dict]:
    import sounddevice as sd
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append({"index": i, "name": d["name"], "rate": d["default_samplerate"]})
    return out


def load_wav(path: str) -> np.ndarray:
    """Read a PCM WAV (any rate, mono/stereo, 16-bit) → 16 kHz float32 mono."""
    import wave
    with wave.open(path) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        raw = raw.reshape(-1, ch).mean(axis=1)
    return _resample(raw, sr)
