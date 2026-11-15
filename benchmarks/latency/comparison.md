# JARVIS latency — before vs after (Performance/UX 2026-09-22)

Harness: `python -m benchmarks.latency.bench --label {before|after} [--claude]`. All legs offline/read-only (no mouse,
no audio played, no app launched) so they were safe to run while the machine was in normal use.

## Honesty note on the raw hardware legs
The absolute per-leg times below are **noisy**, and the noise is not the optimization. The measurements were taken while
the user was actively working on the laptop, so Whisper/OCR times swing with CPU contention (Whisper 3.9 s audio measured
at 718 ms on an idle pass and 3446–3630 ms under load) and UIA-observe swings with **which window happens to be in the
foreground** (68 ms for a light window, 1722 ms for a heavy one). These legs run the **same code** before and after — they
were never the target — so no before/after delta is claimed for them. The wins are architectural and were measured with
methods that do **not** depend on machine load.

## Measured wins (load-independent)

### 1. Adaptive end-of-turn detection (§6) — deterministic, reliable
| scenario | legacy (fixed 0.9 s) | adaptive | result |
|---|---|---|---|
| short command ("Open Chrome.") | 930 ms trailing | **570 ms** trailing | **−360 ms**, snappier finalize |
| hesitant correction ("Open Chrome… and… Edge") | **cuts the user off** | holds, no cut | correctness win |
| long fluent dictation | 900 ms | 810 ms | no premature cut |

The legacy fixed rule *cut off* the hesitant speaker (finalized during the pause, before they resumed). Adaptive raises
its tolerance once it has observed a bridged pause, so it does not. Verified by `tests/test_latency.py`.

### 2. Immediate local acknowledgement (§9) — the dominant *perceived*-latency win
Measured on this machine: the local ack synthesizes in **~90–220 ms**, whereas Claude's **first token** arrives at
**2.9 s (short reply) to 11.2 s**, and full task turns run **15–75 s** (`benchmarks/tasks_*.json`). Before, the user
heard nothing until the whole reply was spoken; now a task-worthy utterance gets "On it." within a fraction of a second —
**seconds to tens of seconds** sooner. Short deterministic commands ("Open Chrome.") stay silent and just act, so JARVIS
does not become chatty (§30).

### 3. Streaming TTS (§12) — real, but scales with reply length (reported honestly)
Measured first-speakable-clause vs full-turn on conversational prompts:

| reply | first clause ready | full turn done | speech starts earlier by |
|---|---|---|---|
| 1 sentence | (at flush) | 3810 ms | ~0 (one burst) |
| 2 sentences | 11176 ms | 11280 ms | ~100 ms |

For the short replies that concise voice mode targets, the gain is small because the reply streams in one tight burst at
the end. The gain grows with reply length and generation time (long narration / conversational mode). It never hurts and
never double-speaks (verified: the final committed message is not re-spoken).

### 4. JEV connection pre-warm (§22, §37)
First `.choose()` pays a DNS+TCP+TLS handshake; the pooled client then reuses it (`jev_client_setup` p50 = 0 ms once
warm, vs 199–846 ms cold observed). Pre-warming at startup moves that ~200–360 ms off the first real decision. **Token-
free — never touches the ₹1,000/month budget.**

### 5. Barge-in (§10, §13, §41) — mechanism verified offline, live timing deferred
The detector fires on ~0.18 s of sustained over-speech and ignores a 3-frame cough and quiet self-echo (verified in
tests). End-to-end interrupt latency needs a live audible session (it plays audio and would disturb the user), so it is
left for a session when the user is away.

## What did NOT change (by design)
Whisper model/accuracy, OCR, the router tiers, verification, safety, privacy, the JEV budget ceiling, memory, daily
conversation, and the RL hook are all unchanged. Optimization was confined to *when* and *how* those legs are
orchestrated, never to weakening them.
