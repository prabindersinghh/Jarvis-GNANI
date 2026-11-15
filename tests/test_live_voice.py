"""Live voice-pipeline tests for the low-latency features (Performance/UX 2026-09-22), with REAL audio.

These PLAY TTS through the speakers and OPEN the real microphone, so they are marked `live` and should only be run when
the user is away:  python -m pytest tests/test_live_voice.py -q -m live
They validate the pieces that unit tests can only approximate: the adaptive endpointer on real recorded speech, real
Gnani Timbre streaming/ack playback, and — the key §13 check — that JARVIS speaking does NOT self-trigger barge-in.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "open_notepad.wav"


def _ready_tts():
    from jarvis import config
    from jarvis.voice.tts import TTS
    if not config.gnani_api_key():
        pytest.skip("GNANI_API_KEY is not configured")
    tts = TTS()
    for _ in range(50):
        if tts.available:
            return tts
        time.sleep(0.1)
    pytest.skip("Gnani Timbre or audio output unavailable")


def test_adaptive_endpointer_on_real_speech():
    """Feed the real recorded utterance frame-by-frame (as the recorder does) + trailing silence; it must finalize."""
    from jarvis.voice.endpoint import AdaptiveEndpointer, EndpointConfig, Turn
    from jarvis.voice.mic import load_wav
    if not FIXTURE.exists():
        pytest.skip("fixture missing")
    audio = load_wav(str(FIXTURE))               # 16 kHz mono float32
    ep = AdaptiveEndpointer(EndpointConfig())
    frame = int(16000 * 0.03)
    t, last_speech, fin = 0.0, None, None
    for i in range(0, len(audio), frame):
        c = audio[i:i + frame]
        rms = float(np.sqrt(np.mean(c * c))) if len(c) else 0.0
        if rms > ep.cfg.energy_threshold:
            last_speech = t
        if ep.feed(rms, t) is Turn.FINALIZED and fin is None:
            fin = t
        t += 0.03
    if fin is None:                               # the clip may end mid-word; emulate the mic's continuing silence
        for _ in range(80):
            if ep.feed(0.001, t) is Turn.FINALIZED:
                fin = t
                break
            t += 0.03
    assert last_speech is not None, "no speech detected in the fixture"
    assert fin is not None, "endpointer never finalized on real speech + trailing silence"
    assert fin >= last_speech


def test_streaming_tts_real_playback():
    """Stream a reply clause-by-clause through Gnani Timbre; verify ordered playback and one resume."""
    from jarvis.voice.tts import StreamingSpeaker
    tts = _ready_tts()
    resumes = []
    sp = StreamingSpeaker(tts, mode_getter=lambda: "conversational", resume_cb=lambda: resumes.append(1))
    tts.on_end = sp.on_speech_end                 # wire as the app does, so resume fires when audio drains
    sp.begin_turn()
    for d in ["Opening Chrome now. ", "The results are loading, ", "and I will read the first one."]:
        sp.on_text(d)
        time.sleep(0.2)
    assert sp.on_result("Opening Chrome now. The results are loading, and I will read the first one.", resume=True)
    deadline = time.time() + 20
    while tts.pending() and time.time() < deadline:
        time.sleep(0.1)
    assert not tts.pending(), "speech did not finish"
    assert resumes == [1], "must resume exactly once, at the end of all audio"
    assert "Opening Chrome" in sp.spoken_text and "read the first one" in sp.spoken_text
    tts.shutdown()


def test_ack_real_playback():
    """A task-worthy utterance produces a short ack that actually plays."""
    from jarvis.voice.ack import ack_for
    tts = _ready_tts()
    phrase = ack_for("search for the latest manufacturing news and organize the files")
    assert phrase, "expected an ack for a multi-step task"
    assert tts.speak(phrase)
    deadline = time.time() + 8
    while tts.pending() and time.time() < deadline:
        time.sleep(0.1)
    tts.shutdown()


def test_tts_playback_does_not_self_trigger_bargein():
    """§13 zero self-echo: JARVIS speaking aloud while the real mic monitors must NOT fire barge-in on its own voice."""
    from jarvis.voice.mic import Recorder
    tts = _ready_tts()
    fired = {"v": False}
    rec = Recorder(suppress=lambda: tts.speaking.is_set(), on_bargein=lambda: fired.__setitem__("v", True))
    try:
        rec.open()
    except Exception as e:  # noqa: BLE001
        tts.shutdown()
        pytest.skip(f"microphone unavailable: {e}")
    rec.set_monitor(True)
    tts.speak("This is a fairly long sentence that JARVIS speaks aloud to check that its own voice does not falsely "
              "trigger a barge-in interruption while the microphone is monitoring.")
    deadline = time.time() + 12
    while tts.pending() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.4)
    rec.set_monitor(False)
    rec.close()
    tts.shutdown()
    assert not fired["v"], "TTS playback self-triggered barge-in — self-echo not suppressed"
