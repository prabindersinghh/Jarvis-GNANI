"""Local speech-to-text: faster-whisper (CTranslate2, CPU int8). Model files live under D:\\JARVIS\\models."""
from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

from .. import config
from . import _av_shim

log = logging.getLogger("jarvis.voice.whisper")
SAMPLE_RATE = 16000


class WhisperEngine:
    def __init__(self, model: str | None = None, compute: str | None = None, language: str | None = None):
        s = config.SETTINGS
        self.model_name = model or s["whisper_model"]
        self.compute = compute or s["whisper_compute"]
        self.language = language or s["whisper_language"] or None
        self.model_dir = config.MODELS / f"whisper-{self.model_name}"
        self._model = None
        self._lock = threading.Lock()
        self.load_ms = 0

    def ensure_downloaded(self) -> str:
        os.environ.setdefault("HF_HOME", str(config.MODELS / "hf"))
        _av_shim.install()
        from faster_whisper import download_model
        if (self.model_dir / "model.bin").exists():
            return str(self.model_dir)
        log.info("downloading whisper %s → %s", self.model_name, self.model_dir)
        return download_model(self.model_name, output_dir=str(self.model_dir))

    def load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            t0 = time.perf_counter()
            path = self.ensure_downloaded()
            from faster_whisper import WhisperModel
            self._model = WhisperModel(path, device="cpu", compute_type=self.compute, cpu_threads=max(2, (os.cpu_count() or 4) - 2))
            self.load_ms = round((time.perf_counter() - t0) * 1000)
            log.info("whisper %s loaded in %d ms", self.model_name, self.load_ms)
            # warm-up so the first real utterance is fast
            try:
                list(self._model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language=self.language, beam_size=1)[0])
            except Exception:  # noqa: BLE001
                pass
            return self._model

    def transcribe(self, audio: np.ndarray, vad: bool = True) -> dict:
        """audio: float32 mono 16 kHz in [-1, 1]. Returns {text, segments, voice_ms, language, no_speech}."""
        m = self.load()
        t0 = time.perf_counter()
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        segs, info = m.transcribe(audio, language=self.language, beam_size=1, best_of=1, vad_filter=vad,
                                  vad_parameters={"min_silence_duration_ms": 300, "threshold": float(config.SETTINGS["vad_aggressiveness"])},
                                  condition_on_previous_text=False, without_timestamps=True)
        parts = []
        no_speech = []
        for sg in segs:
            parts.append(sg.text.strip())
            no_speech.append(sg.no_speech_prob)
        text = " ".join(p for p in parts if p).strip()
        ms = round((time.perf_counter() - t0) * 1000)
        return {"text": text, "segments": len(parts), "voice_ms": ms, "language": getattr(info, "language", None),
                "no_speech": (min(no_speech) if no_speech else 1.0), "audio_s": round(len(audio) / SAMPLE_RATE, 2)}


_engine: WhisperEngine | None = None


def engine() -> WhisperEngine:
    global _engine
    if _engine is None:
        _engine = WhisperEngine()
    return _engine
