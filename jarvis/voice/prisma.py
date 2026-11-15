"""Gnani Prisma speech-to-text adapter; retains the transcript result shape used by the app."""
from __future__ import annotations

import io
import logging
import time
import wave

import numpy as np

from .. import config

log = logging.getLogger("jarvis.voice.prisma")
SAMPLE_RATE = 16000
ENDPOINT = "https://api.vachana.ai/stt/v3"


class PrismaEngine:
    def __init__(self):
        self.model_name = str(config.SETTINGS.get("prisma_model", "prisma-v3.0"))
        self.language = str(config.SETTINGS.get("prisma_language", "indic-auto"))
        self._client = None
        self.load_ms = 0

    @property
    def available(self) -> bool:
        return bool(config.gnani_api_key())

    def load(self):
        if not self.available:
            raise RuntimeError("GNANI_API_KEY is not set in data/config/.env")
        return self

    def transcribe(self, audio: np.ndarray, vad: bool = True) -> dict:
        self.load()
        started = time.perf_counter()
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767).astype("<i2")
        wav = io.BytesIO()
        with wave.open(wav, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(pcm.tobytes())
        import httpx
        if self._client is None:
            self._client = httpx.Client(timeout=float(config.SETTINGS.get("gnani_timeout_s", 30)))
        model = self.model_name
        data = {"language_code": self.language, "format": "transcribe"}
        if model and model != "prisma-v2.5":
            data["model"] = model
        response = self._client.post(ENDPOINT, headers={"X-API-Key-ID": config.gnani_api_key()},
                                     data=data, files={"audio_file": ("utterance.wav", wav.getvalue(), "audio/wav")})
        if response.is_error:
            raise RuntimeError(f"Gnani Prisma HTTP {response.status_code}: {response.text[:300]}")
        result = response.json()
        if result.get("success") is False:
            raise RuntimeError(f"Gnani Prisma error: {result.get('error') or result.get('message')}")
        text = str(result.get("transcript") or "").strip()
        elapsed = round((time.perf_counter() - started) * 1000)
        return {"text": text, "segments": 1 if text else 0, "voice_ms": elapsed,
                "language": result.get("detected_language_code") or self.language,
                "no_speech": 0.0 if text else 1.0, "audio_s": round(len(audio) / SAMPLE_RATE, 2)}


_engine: PrismaEngine | None = None


def engine() -> PrismaEngine:
    global _engine
    if _engine is None:
        _engine = PrismaEngine()
    return _engine
