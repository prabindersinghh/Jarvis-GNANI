# JARVIS latency — before

Historical pre-Gnani local speech baseline; the voice measurements do not describe Prisma or Timbre.

Generated 2026-09-22 04:50 · legacy_local_stt=former_local_recognizer (legacy_local_quantization).

Offline-measurable legs only (no mouse / no audio / no app launch). The live end-to-end wall-clock chain and live barge-in timing are measured separately when the user is away — see the report.

## End-of-turn detection (legacy fixed 0.9 s vs adaptive)

| scenario | legacy tail ms | adaptive tail ms | legacy cut-off | adaptive cut-off |
|---|---|---|---|---|
| command_short | 930 | 570 | no | no |
| hesitation | 930 | 1320 | YES | no |
| long_sentence | 900 | 810 | no | no |

## Timed legs (ms)

| leg | n | p50 | p75 | p95 | min | max |
|---|---|---|---|---|---|---|
| context_redact | 50 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| context_memory_brief | 20 | 6.1 | 15.3 | 24.5 | 1.3 | 36.0 |
| legacy_local_stt_former_local_recognizer_load | 1 | 5512 | — | — | — | — |
| legacy_local_stt_former_local_recognizer_long_3.9s | 5 | 3630.3 | 3642.8 | 3798.0 | 3273.6 | 3836.7 |
| legacy_local_stt_former_local_recognizer_short_1.6s | 5 | 3177.7 | 3216.2 | 3274.9 | 2892.4 | 3289.6 |
| tts_cold_first_synth | 1 | 1017 | — | — | — | — |
| tts_synth_clause | 5 | 226.5 | 250.4 | 266.7 | 172.9 | 270.8 |
| tts_synth_full_reply | 5 | 337.7 | 337.9 | 368.6 | 314.2 | 376.3 |
| uia_observe_foreground | 6 | 1722.5 | 1864.3 | 2016.8 | 1344.9 | 2055.1 |
| screenshot_window_quarter | 6 | 115.8 | 127.3 | 148.9 | 81.8 | 155.3 |
| ocr_window_region | 4 | 3324.1 | 4109.6 | 4250.9 | 1051.6 | 4286.2 |
| jev_client_setup | 3 | 0.0 | 517.8 | 932.0 | 0.0 | 1035.5 |
| jev_pick_warm | 3 | 537.7 | 774.5 | 964.0 | 328.3 | 1011.3 |
