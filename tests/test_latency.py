"""Low-latency voice pipeline (Performance/UX 2026-09-22): adaptive endpointing, streaming TTS, barge-in, ack.

All offline and deterministic — no audio device, no network, no desktop. These cover the mechanisms; the live
full-duplex voice run is exercised separately when the user is away (see docs/PERFORMANCE_UX_2026-09-22.md).
"""
from __future__ import annotations

from jarvis.voice.ack import ack_for
from jarvis.voice.bargein import BargeInConfig, BargeInDetector
from jarvis.voice.endpoint import AdaptiveEndpointer, EndpointConfig, Turn, legacy_should_finalize
from jarvis.voice.tts import CONCISE_MAX_SENTENCES, SpeechChunker, StreamingSpeaker, clean_for_speech

FRAME = 0.03


def _run_endpoint(segments):
    """Feed a synthetic energy envelope; return (finalize_time, last_speech_time) or (None, ...)."""
    ep = AdaptiveEndpointer(EndpointConfig())
    t, last_speech, fin = 0.0, None, None
    for dur, speech in segments:
        for _ in range(int(round(dur / FRAME))):
            rms = 0.05 if speech else 0.001
            if rms > ep.cfg.energy_threshold:
                last_speech = t
            if ep.feed(rms, t) is Turn.FINALIZED and fin is None:
                fin = t
            t += FRAME
    return fin, last_speech


# ---------------------------------------------------------------- adaptive endpointing (§6)
def test_short_command_finalizes_fast():
    fin, last = _run_endpoint([(0.3, False), (1.1, True), (2.0, False)])
    assert fin is not None
    tail = fin - last
    assert 0.45 <= tail <= 0.75, tail          # ~fast_silence (0.55), well under the legacy 0.9 s


def test_adaptive_does_not_cut_a_hesitant_speaker():
    # first short gap establishes hesitation, then a 1.05 s gap legacy would cut through
    segs = [(0.3, False), (0.8, True), (0.5, False), (0.5, True), (1.05, False), (0.7, True), (2.0, False)]
    fin, last = _run_endpoint(segs)
    assert fin is not None
    # the last speech ends at 3.85 s; adaptive must not finalize before that
    assert fin >= 3.8, fin
    assert abs(fin - last) < 1.4               # finalizes shortly after the true end


def test_long_fluent_sentence_uses_base_not_max():
    fin, last = _run_endpoint([(0.3, False), (3.5, True), (2.0, False)])
    assert fin is not None
    assert (fin - last) <= 0.9                  # a fluent dictation stays on the snappy base bound, not max tolerance


def test_legacy_rule_cuts_the_hesitant_speaker():
    # documents WHY adaptive is needed: the old fixed rule finalizes during the 1.05 s gap
    segs = [(0.3, False), (0.8, True), (0.5, False), (0.5, True), (1.05, False), (0.7, True), (2.0, False)]
    t, spoke, last_voice, fin = 0.0, False, None, None
    for dur, speech in segs:
        for _ in range(int(round(dur / FRAME))):
            rms = 0.05 if speech else 0.001
            if rms > 0.012:
                spoke, last_voice = True, t
            if fin is None and legacy_should_finalize(spoke, t, last_voice, 0.0):
                fin = t
            t += FRAME
    assert fin is not None and fin < 3.8        # legacy cuts the speaker off (the bug adaptive fixes)


def test_falsy_zero_timestamp_regression():
    # the endpointer must finalize even when the very first frame is at t=0.0 (guards the `x or default` zero bug)
    fin, _ = _run_endpoint([(1.0, True), (1.0, False)])
    assert fin is not None


# ---------------------------------------------------------------- streaming TTS chunker (§12, §40)
def test_chunker_emits_sentences_and_flushes_remainder():
    c = SpeechChunker("conversational")
    out = []
    for d in ["Opening Chrome. ", "Loading the ", "results now"]:
        out += c.push(d)
    assert out == ["Opening Chrome."]           # first complete sentence emitted mid-stream
    out += c.flush()
    assert out[-1] == "Loading the results now."  # remainder flushed with a terminal period


def test_chunker_concise_caps_sentences():
    c = SpeechChunker("concise")
    out = []
    for d in ["One. ", "Two. ", "Three. ", "Four."]:
        out += c.push(d)
    out += c.flush()
    assert len(out) <= CONCISE_MAX_SENTENCES
    assert "Three" not in " ".join(out)


def test_chunker_silent_emits_nothing():
    c = SpeechChunker("silent")
    assert c.push("Anything at all. ") == []
    assert c.flush() == []
    assert not c.active


def test_chunker_does_not_split_decimals_or_paths():
    c = SpeechChunker("conversational")
    out = c.push("The file D:\\a\\b.txt is 3.5 MB")   # no terminator+space inside → no premature split
    assert out == []
    out += c.flush()
    assert len(out) == 1


def test_clean_for_speech_unchanged_contract():
    assert clean_for_speech("**Hello** `world`", "conversational") == "Hello world."
    assert clean_for_speech("One. Two. Three.", "concise").count(".") <= 2


# ---------------------------------------------------------------- StreamingSpeaker (dedupe / resume / cancel)
class _FakeTTS:
    def __init__(self, mode="conversational"):
        self.available, self.mode, self.q, self._speaking = True, mode, [], False

    def speak_chunk(self, text, first=False):
        if first:
            self.q = []
        self.q.append(("chunk", text))
        return True

    def speak(self, text, interrupt=True):
        if interrupt:
            self.q = []
        self.q.append(("full", text))
        return True

    def pending(self):
        return bool(self.q) or self._speaking

    def drain(self):
        self.q, self._speaking = [], False


def test_streaming_speaker_no_double_speak_and_resumes_once():
    resumes = []
    tts = _FakeTTS()
    sp = StreamingSpeaker(tts, lambda: "conversational", lambda: resumes.append(1))
    sp.begin_turn()
    for d in ["Opening Chrome. ", "Loading results"]:
        sp.on_text(d)
    spoke = sp.on_result("Opening Chrome. Loading results", resume=True)
    assert spoke
    assert ("full", "Opening Chrome. Loading results") not in tts.q   # final not re-spoken
    assert [k for k, _ in tts.q] == ["chunk", "chunk"]
    assert resumes == []                          # audio still pending → no resume yet
    tts.drain()
    sp.on_speech_end()
    assert resumes == [1]                          # exactly one resume, at the true end


def test_streaming_disabled_speaks_full_reply_once():
    tts = _FakeTTS()
    sp = StreamingSpeaker(tts, lambda: "conversational", lambda: None, enabled_getter=lambda: False)
    sp.begin_turn()
    sp.on_text("ignored while streaming off ")
    assert sp.on_result("The whole reply.", resume=False)
    assert tts.q == [("full", "The whole reply.")]


def test_bargein_cancel_stops_further_speech_and_resume():
    resumes = []
    tts = _FakeTTS()
    sp = StreamingSpeaker(tts, lambda: "conversational", lambda: resumes.append(1))
    sp.begin_turn()
    sp.on_text("First part. ")
    sp.cancel()
    sp.on_text("should be ignored. ")
    assert sp.on_result("First part. more", resume=True) is False
    assert tts.q == [("chunk", "First part.")]
    assert resumes == []


# ---------------------------------------------------------------- barge-in detector (§10, §13, §41)
def test_bargein_fires_on_sustained_speech():
    b = BargeInDetector(BargeInConfig())
    fired = [b.feed(0.05, i * FRAME) for i in range(12)]
    assert any(fired)
    assert sum(fired) == 1                          # fires exactly once


def test_bargein_ignores_a_cough():
    b = BargeInDetector(BargeInConfig())
    fired = [b.feed(0.05 if i < 3 else 0.001, i * FRAME) for i in range(20)]
    assert not any(fired)                           # 3 loud frames (~0.09 s) < sustain window


def test_bargein_ignores_quiet_self_echo():
    b = BargeInDetector(BargeInConfig())              # residual echo below the raised threshold never triggers
    fired = [b.feed(0.02, i * FRAME) for i in range(30)]
    assert not any(fired)


# ---------------------------------------------------------------- immediate acknowledgement (§9, §30)
def test_ack_silent_for_short_deterministic_command():
    assert ack_for("open chrome") is None
    assert ack_for("close the window") is None


def test_ack_speaks_for_multi_step_or_long_tasks():
    assert ack_for("search for the latest manufacturing AI news")
    assert ack_for("find the newest files in this folder and organize them by type")


def test_ack_empty_is_silent():
    assert ack_for("") is None
    assert ack_for("   ") is None
