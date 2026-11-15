"""Shim for PyAV when Smart App Control blocks its unsigned DLLs.

faster-whisper imports `av` at module import time but only uses it to decode
audio *files*. JARVIS feeds 16 kHz float32 numpy arrays from the microphone, so
a stub module is sufficient. Try the real package first.
"""
from __future__ import annotations

import sys
import types


def install() -> str:
    try:
        import av  # noqa: F401
        return "real"
    except Exception:  # ImportError / DLL blocked
        pass
    av = types.ModuleType("av")

    def _blocked(*_a, **_k):
        raise RuntimeError("PyAV is unavailable (blocked by Application Control); pass numpy audio instead of files")

    av.open = _blocked
    audio = types.ModuleType("av.audio")
    resampler = types.ModuleType("av.audio.resampler")

    class AudioResampler:  # pragma: no cover - never used with numpy input
        def __init__(self, *a, **k):
            _blocked()

    resampler.AudioResampler = AudioResampler
    audio.resampler = resampler
    av.audio = audio
    av.error = types.SimpleNamespace(InvalidDataError=RuntimeError)
    sys.modules["av"] = av
    sys.modules["av.audio"] = audio
    sys.modules["av.audio.resampler"] = resampler
    return "shim"
