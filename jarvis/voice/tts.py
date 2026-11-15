"""Gnani Timbre text-to-speech with queued playback, barge-in and microphone suppression.

- `speak(text)` queues synthesis and playback on a worker thread; `stop()` purges immediately.
- `speaking` is a threading.Event the recorder/PTT consult: while JARVIS talks, mic frames are dropped and a PTT
  press first silences JARVIS, then listens (barge-in).
- Text is cleaned for speech: markdown, code blocks, URLs, tool noise, "Insight" boxes removed; concise mode trims to
  the first sentences.
Response modes (settings "response_mode"): silent (no speech) | concise (short spoken summary) | conversational (full).
"""
from __future__ import annotations

import io
import logging
import queue
import re
import threading
import time
import wave

from .. import config

log = logging.getLogger("jarvis.voice.tts")
MODES = ("silent", "concise", "conversational")
TTS_ENDPOINT = "https://api.vachana.ai/api/v1/tts/inference"

_MD_CODE = re.compile(r"```.*?```", re.S)
_MD_INLINE = re.compile(r"`([^`]*)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+|file:///\S+")
_INSIGHT = re.compile(r"`?★.*?─{5,}.*?`?", re.S)
_MD_MARK = re.compile(r"[*_#>]+")
_PATH = re.compile(r"\b[A-Za-z]:\\[^\s,;]+")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _clean_markup(text: str) -> str:
    """Strip markdown / code / URLs / paths / insight boxes for speech. No sentence trimming, no forced trailing dot —
    safe to run on a single streamed chunk as well as a whole message."""
    t = text or ""
    t = _INSIGHT.sub(" ", t)
    t = _MD_CODE.sub(" (code omitted) ", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_INLINE.sub(r"\1", t)
    t = _URL.sub(" link ", t)
    t = _PATH.sub(lambda m: m.group(0).split("\\")[-1], t)  # say the file name, not the whole path
    t = _MD_MARK.sub(" ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n+\s*", ". ", t).strip(" .")
    t = re.sub(r"\.\s*\.", ".", t)
    return t.strip()


def clean_for_speech(text: str, mode: str = "conversational") -> str:
    t = _clean_markup(text)
    if t:
        t = t + "."
        t = re.sub(r"\.\s*\.", ".", t)
    if mode == "concise":
        sents = _SENT.split(t)
        t = " ".join(sents[:2])
        if len(t) > 280:
            t = t[:277].rsplit(" ", 1)[0] + "…"
    return t.strip()


# a sentence terminator followed by whitespace (streaming-safe: whitespace confirms a new word started, so "3.5" and
# "D:\path" do not split). Used to emit complete speakable chunks while Claude is still generating (§12, §40).
_BOUNDARY = re.compile(r'[.!?]+["\')\]]?(?=\s)')
_ALNUM = re.compile(r"[A-Za-z0-9]")
CONCISE_MAX_SENTENCES = 2
CONCISE_MAX_CHARS = 280


class SpeechChunker:
    """Turns streamed assistant text deltas into complete, speakable chunks at sentence boundaries so TTS can start on
    the first sentence while the rest is still being generated (§12). Mode-aware: `concise` stops after ~2 sentences /
    280 chars (the same cap `clean_for_speech` applies), `conversational` speaks it all, `silent` emits nothing.

    `push(delta)` returns a list of ready chunks (already cleaned for speech); `flush()` returns any final remainder.
    `spoken_text` is everything handed to TTS — used to avoid re-speaking the final committed message.
    """

    def __init__(self, mode: str = "conversational", min_chunk_chars: int = 3):
        self.mode = mode
        self.min_chunk_chars = min_chunk_chars
        self._buf = ""
        self._sentences = 0
        self._chars = 0
        self._capped = False           # concise cap reached → ignore the remainder
        self.spoken_text = ""

    @property
    def active(self) -> bool:
        return self.mode != "silent" and not self._capped

    def _emit(self, raw: str) -> str | None:
        if self.mode == "silent" or self._capped:
            return None
        chunk = _clean_markup(raw)
        if not chunk or not _ALNUM.search(chunk):
            return None
        chunk = chunk + ("." if chunk[-1] not in ".!?…" else "")
        if self.mode == "concise":
            if self._sentences >= CONCISE_MAX_SENTENCES or self._chars >= CONCISE_MAX_CHARS:
                self._capped = True
                return None
        self._sentences += 1
        self._chars += len(chunk)
        self.spoken_text += (" " if self.spoken_text else "") + chunk
        return chunk

    def push(self, delta: str) -> list[str]:
        if not self.active or not delta:
            return []
        self._buf += delta
        out: list[str] = []
        while True:
            m = None
            for m in _BOUNDARY.finditer(self._buf):
                break  # first boundary only
            if not m:
                break
            end = m.end()
            candidate, rest = self._buf[:end], self._buf[end:]
            # require enough real characters so we do not speak a lone "1." fragment
            if len(_ALNUM.findall(candidate)) < self.min_chunk_chars:
                # not enough yet; wait for more unless there is a lot buffered
                if len(self._buf) < 200:
                    break
            chunk = self._emit(candidate)
            self._buf = rest
            if chunk:
                out.append(chunk)
            if self._capped:
                self._buf = ""
                break
        return out

    def flush(self) -> list[str]:
        """Emit any trailing text that never hit a sentence boundary (the last clause of the reply)."""
        if not self.active:
            return []
        rest, self._buf = self._buf, ""
        chunk = self._emit(rest)
        return [chunk] if chunk else []


class TTS:
    def __init__(self, on_start=None, on_end=None):
        self.on_start, self.on_end = on_start, on_end
        self.speaking = threading.Event()
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._purge = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="tts", daemon=True)
        self.available = False
        self.last_error = None
        self._thread.start()

    @property
    def mode(self) -> str:
        m = str(config.SETTINGS.get("response_mode", "concise")).lower()
        return m if m in MODES else "concise"

    def set_mode(self, mode: str):
        if mode in MODES:
            config.SETTINGS["response_mode"] = mode
            config.save_settings(config.SETTINGS)
            if mode == "silent":
                self.stop()

    def speak(self, text: str, mode: str | None = None, interrupt: bool = True) -> bool:
        """Queue speech. Returns True if something was queued (on_end will fire), False if there is nothing to say."""
        mode = mode or self.mode
        if mode == "silent" or not text or not self.available:
            return False
        cleaned = clean_for_speech(text, mode)
        if not cleaned or cleaned == ".":
            return False
        if interrupt:
            self.stop()
        self._q.put(cleaned)
        return True

    def speak_chunk(self, text: str, first: bool = False) -> bool:
        """Enqueue an already-cleaned speech chunk (from SpeechChunker) without re-trimming. `first` purges any stale
        audio from a previous turn; subsequent chunks queue in order so the reply is spoken as one continuous stream."""
        if not text or not self.available or self.mode == "silent":
            return False
        if first:
            self.stop()
        self._q.put(text)
        return True

    def pending(self) -> bool:
        """True while there is speech playing or still queued — the app uses this to resume listening only at the end."""
        return self.speaking.is_set() or not self._q.empty()

    def stop(self):
        self._purge.set()
        with self._q.mutex:
            self._q.queue.clear()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:  # noqa: BLE001
            pass

    def shutdown(self):
        self._stop.set()
        self.stop()

    # ---------------------------------------------------------------- worker
    def _worker(self):
        try:
            if not config.gnani_api_key():
                raise RuntimeError("GNANI_API_KEY is not set in data/config/.env")
            import httpx
            import sounddevice
            self._client = httpx.Client(timeout=float(config.SETTINGS.get("gnani_timeout_s", 30)))
            self.available = True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.warning("TTS unavailable: %s", e)
            return
        while not self._stop.is_set():
            try:
                text = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._purge.clear()
            self.speaking.set()
            log.info("TTS speaking (%d chars, mode=%s)", len(text), self.mode)
            if self.on_start:
                try:
                    self.on_start()
                except Exception:  # noqa: BLE001
                    pass
            try:
                payload = {"text": text,
                           "model": config.SETTINGS.get("timbre_model", "timbre-v2.5"),
                           "voice": config.SETTINGS.get("timbre_voice", "Kaveri"),
                           "language": config.SETTINGS.get("timbre_language", "en-IN"),
                           "audio_config": {"encoding": "linear_pcm", "container": "wav", "num_channels": 1,
                                            "sample_rate": 48000, "sample_width": 2}}
                response = self._client.post(TTS_ENDPOINT, headers={"X-API-Key-ID": config.gnani_api_key()}, json=payload)
                if response.is_error:
                    raise RuntimeError(f"Gnani Timbre HTTP {response.status_code}: {response.text[:300]}")
                if self._purge.is_set() or self._stop.is_set():
                    continue
                import numpy as np
                import sounddevice as sd
                with wave.open(io.BytesIO(response.content), "rb") as audio:
                    rate, channels = audio.getframerate(), audio.getnchannels()
                    samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
                if channels > 1:
                    samples = samples.reshape(-1, channels)
                sd.play(samples, rate, blocking=True)
            except Exception as e:  # noqa: BLE001
                log.warning("TTS speak failed: %s", e)
            finally:
                time.sleep(0.15)  # let the room settle before re-enabling the mic
                self.speaking.clear()
                log.info("TTS done")
                if self.on_end:
                    try:
                        self.on_end()
                    except Exception:  # noqa: BLE001
                        pass


class StreamingSpeaker:
    """Per-turn orchestration of streaming (clause-by-clause) TTS + when to resume listening (§12, §40).

    Decoupled from the app and the Claude SDK so the tricky parts — speaking the reply as it streams, never
    re-speaking the final committed message (dedupe), and resuming the mic only when ALL audio has finished — are
    unit-testable with a fake TTS. Thread-safe: `on_result` (asyncio thread) and `on_speech_end` (TTS thread) race to
    trigger the resume; whichever observes the last audio drained wins, and the flag-clearing makes it idempotent.
    """

    def __init__(self, tts, mode_getter, resume_cb, enabled_getter=lambda: True):
        self.tts = tts
        self.mode_getter = mode_getter          # () -> "silent"|"concise"|"conversational"
        self.resume_cb = resume_cb              # () -> None, called once when speech ends and a resume was requested
        self.enabled_getter = enabled_getter    # () -> bool (settings: stream_tts)
        self._chunker: SpeechChunker | None = None
        self._started = False                    # any chunk handed to TTS this turn (or a preamble ack is playing)
        self._turn_done = False                  # on_result has run (all chunks flushed)
        self._resume = False
        self._cancelled = False                  # barge-in / stop cut this turn's speech short
        self._keep_prefix = False                # a local ack is playing → do not purge it on the first chunk
        self._lock = threading.Lock()

    def begin_turn(self, keep_prefix: bool = False):
        """Start a new assistant turn. `keep_prefix` = a local ack is already queued/playing, so the first streamed
        chunk must NOT purge it (it queues behind the ack instead)."""
        with self._lock:
            self._chunker = SpeechChunker(self.mode_getter()) if self.enabled_getter() else None
            self._started = self._keep_prefix = keep_prefix
            self._turn_done = self._resume = self._cancelled = False

    def cancel(self):
        """Barge-in / Stop: discard the rest of this turn's speech and do not auto-resume from it."""
        with self._lock:
            self._chunker = None
            self._cancelled = True
            self._resume = False

    def on_text(self, delta: str):
        if self.tts is None or not getattr(self.tts, "available", False) or self._chunker is None or self._cancelled:
            return
        for chunk in self._chunker.push(delta):
            if self.tts.speak_chunk(chunk, first=not self._started):
                self._started = True

    def on_result(self, final_text: str, resume: bool) -> bool:
        """Flush the remainder and decide resume. Returns True if anything was (or is being) spoken."""
        spoke = False
        with self._lock:
            if self._cancelled:
                self._turn_done = True
                return False
            if self._chunker is not None:
                for chunk in self._chunker.flush():
                    if self.tts is not None and self.tts.speak_chunk(chunk, first=not self._started):
                        self._started = True
                spoke = self._started
            if not spoke and final_text and self.tts is not None:
                # streaming off / nothing streamed → speak the whole reply once (queue behind any ack, don't purge it)
                spoke = self.tts.speak(final_text, interrupt=not self._keep_prefix)
            self._turn_done = True
            self._resume = bool(resume and spoke)
        self._maybe_resume()
        return spoke

    def on_speech_end(self):
        self._maybe_resume()

    def _maybe_resume(self):
        with self._lock:
            go = (self._resume and self._turn_done and self.tts is not None and not self.tts.pending())
            if go:
                self._resume = self._turn_done = False
        if go:
            self.resume_cb()

    @property
    def spoken_text(self) -> str:
        return self._chunker.spoken_text if self._chunker else ""


_tts: TTS | None = None


def engine() -> TTS:
    global _tts
    if _tts is None:
        _tts = TTS()
    return _tts
