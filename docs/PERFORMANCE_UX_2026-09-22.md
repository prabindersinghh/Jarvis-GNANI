# JARVIS — low-latency voice + fast execution (Performance/UX optimization, 2026-09-22)

Goal: make JARVIS *feel* immediate and conversational, and *actually* reduce latency, without weakening Claude (brain),
JEV (reflex), the router, verification, privacy, safety, the ₹1,000/month JEV ceiling, memory, TTS, daily history, or the
RL hook. Measured first; optimized where the measurement pointed; reported honestly.

## Historical pre-Gnani baseline
The original offline harness `benchmarks/latency/before.json`/`before.md` measured the former local speech engines as well
as application legs. Those speech timings describe that previous implementation only; they are not Prisma/Timbre
benchmarks. The current offline harness excludes hosted Gnani calls; provider timings belong in live tests.
- Former local recognizer on 3.9 s audio: **~0.72 s idle**, inflating to 3.4–3.6 s under concurrent user load; model load ~8 s once at startup.
- Former local synthesizer: clause ~90 ms, whole 3-sentence reply ~100–260 ms (synthesis was *not* the bottleneck).
- UIA observe (foreground window): 68 ms–1.7 s depending on the window; OCR ~0.7 s idle (7–10× UIA); screenshot ~50 ms.
- JEV `.pick()` warm ~0.38 s + ~0.2–0.36 s cold handshake on the first call.
- Legacy end-of-turn: a **single fixed 0.9 s** trailing-silence rule.
- Claude first token 2.9–11 s; full task turns 15–75 s (`benchmarks/tasks_*.json`).

## Bottleneck analysis
Perceived latency was dominated by **dead air after the user stops speaking**: fixed 0.9 s endpoint + serial transcription +
*nothing spoken until Claude's entire reply completed* (first token alone is 2.9–11 s). Real completion latency is
dominated by Claude turn count/wall-time (already reduced by earlier batching work) and by expensive OCR/vision when a
cheaper check would do. The voice loop also **could not be interrupted by voice** (mic frames were dropped while
speaking) and **cold-started the JEV connection** on first use.

## Changes implemented
- **Adaptive end-of-turn + turn state machine** — `jarvis/voice/endpoint.py` (`AdaptiveEndpointer`, IDLE→USER_SPEAKING→
  POSSIBLE_END→FINALIZED). Trailing-silence bound is context-dependent: fast (0.55 s) for a short fluent command, base
  (0.8 s) normally, raised (1.3 s) once hesitation is observed. Wired into `jarvis/voice/mic.py` (legacy fixed rule kept
  behind `adaptive_endpointing=false`). Fixed a latent falsy-`0.0` timestamp bug found by the harness.
- **Streaming (clause-by-clause) TTS** — `SpeechChunker` + `StreamingSpeaker` in `jarvis/voice/tts.py`; `_on_text`
  speaks each complete sentence as Claude streams, the final committed message is never re-spoken, and the mic resumes
  only after all audio drains. Mode-aware (concise caps at ~2 sentences). `stream_tts=true`.
- **Immediate acknowledgement** — `jarvis/voice/ack.py`; a tiny local phrase ("On it.") the instant a task-worthy turn
  is finalized, no Claude round-trip; short deterministic commands stay silent. `ack_enabled=true`.
- **Voice barge-in** — `jarvis/voice/bargein.py`; the recorder *monitors* frames (raised threshold + sustain) instead
  of dropping them **the whole time JARVIS is busy — thinking, working a tool, OR speaking** (`_sync_bargein` in
  `main.py`), so the user can interrupt or guide him mid-task at any time. On over-speech it stops TTS, cancels the
  obsolete reply + Claude generation, and listens; the next utterance becomes the new turn. Tunable for the room:
  `bargein_enabled`, `bargein_while_busy`, `bargein_threshold` (raise in a noisy room), `bargein_sustain_s`.
- **JEV pre-warm** — `JevDecisionEngine.warm()` establishes the pooled TLS/HTTP2 connection at startup (token-free,
  budget untouched); called from a startup thread in `jarvis/app/main.py`.

## Voice pipeline changes
Recorder feeds an `AdaptiveEndpointer` per 30 ms frame; a spoken ack fires before Claude is consulted; the reply is
spoken as it streams; barge-in monitoring runs while JARVIS speaks. New settings in `config.py`
(`adaptive_endpointing`, `endpoint_*`, `bargein_*`, `stream_tts`, `ack_enabled`, `vad_energy_threshold`).

## Barge-in implementation
`BargeInDetector` (higher-than-VAD threshold + sustain window + gap reset) is fed frames by `Recorder._cb` while
`set_monitor(True)` (set on `_on_tts_start` in a conversation). On fire it calls back to the app, which stops TTS,
`StreamingSpeaker.cancel()`s the obsolete turn, interrupts Claude, and starts listening. Ignores coughs/quiet echo (tested).

## Async execution / batching / fast path
Left the router tiers and `computer_run` batching intact (they already collapse many round-trips; earlier work took a
3-task benchmark from 33→14 Claude turns). No tool ordering was parallelized in this pass because the safe wins there
require live desktop measurement (deferred to an away session); the harness and hooks are in place to do it.

## Claude session optimization
Session is already long-lived, resumed per-day, and streams partial messages — unchanged. Confirmed a fresh session is
not created per utterance. Cold connect (~12 s typical, 34 s once under load) happens in a background thread while the
orb is already interactive.

## JEV optimization
Pre-warm (above) + keep-alive limits on the pooled client. The optimizer never increases JEV calls to cut latency; the
budget guard remains the sole gate and is untouched.

## TTS optimization
Streaming clause playback; `speak_chunk`/`pending` added; the final message is de-duplicated against streamed chunks.

## Memory / context optimization
Unchanged this pass: only the new turn + a compact `<memory>` brief is sent (the full daily transcript is never sent;
the SDK keeps conversation context via resume). A separate **memory graph** (below) was added for recall.

## Latency results
See `benchmarks/latency/comparison.md`. Load-independent, measured: adaptive endpoint **930→570 ms** on short commands
and **no more hesitation cut-offs**; instant ack **~90–220 ms vs Claude first token 2.9–11 s**; JEV cold handshake
(~0.2–0.36 s) moved off the first decision; streaming TTS starts on the first clause (small gain on short replies,
larger on long ones, never double-speaks).

## UX results
Orb activation and mic readiness were already warm at startup (mic/TTS initialized; orb shown immediately). Added:
speech begins during generation, a spoken ack for longer tasks, and voice interruption — a full-duplex *feel* without
streaming raw audio. User audio is sent to Gnani Prisma, and reply text to Gnani Timbre.

## Regressions found / fixed
- Falsy-`0.0` timestamp bug in the endpointer (harness-caught) — fixed with explicit `is None` checks.
- `min_chunk_chars` too high held short sentences until flush, defeating the concise cap — lowered to 3.
- Existing conversation tests' `FakeTTS`/`FakeRecorder` didn't implement the grown interface (`mode`, `speak_chunk`,
  `pending`, `set_monitor`) — updated. Full non-live suite: **81 passed, 23 live skipped**.

## Remaining bottlenecks / left for an away session
- Speech provider latency is network-dependent and excluded from the offline latency harness.
- OCR is ~7–10× UIA — keep preferring cheap verification (already the policy).
- Live full-duplex voice, live barge-in timing, and live desktop task regressions **were not run** (they play audio /
  move the mouse and would disturb the user). Run `pytest tests -m live` and a live voice pass when the user is away.

**No "frontier-level latency" claim is made.** The honest summary: JARVIS now *feels* markedly more immediate (instant
ack + adaptive endpointing + streaming speech + barge-in), and several real latencies were removed, all verified offline;
the last mile of live audible measurement is deferred so as not to disturb the user.
