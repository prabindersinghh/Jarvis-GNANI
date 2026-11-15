# JARVIS latency — after

Generated 2026-09-22 04:53 · whisper=base.en (int8).

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
| context_memory_brief | 20 | 2.7 | 5.6 | 15.9 | 1.4 | 43.3 |
| whisper_base.en_load | 1 | 5547 | — | — | — | — |
| whisper_base.en_long_3.9s | 5 | 3446.7 | 3757.3 | 4059.3 | 3240.2 | 4134.8 |
| whisper_base.en_short_1.6s | 5 | 3061.7 | 3140.9 | 3430.3 | 2753.2 | 3502.7 |
| tts_cold_first_synth | 1 | 476 | — | — | — | — |
| tts_synth_clause | 5 | 219.4 | 241.7 | 246.4 | 137.2 | 247.6 |
| tts_synth_full_reply | 5 | 263.9 | 280.1 | 296.4 | 205.1 | 300.5 |
| uia_observe_foreground | 6 | 68.0 | 69.0 | 76.9 | 64.5 | 79.4 |
| screenshot_window_quarter | 6 | 57.9 | 60.1 | 66.0 | 51.7 | 67.8 |
| ocr_window_region | 4 | 3673.9 | 3865.4 | 4037.0 | 3418.5 | 4079.9 |
| jev_client_setup | 3 | 0.0 | 423.1 | 761.5 | 0.0 | 846.1 |
| jev_pick_warm | 3 | 384.7 | 389.5 | 393.4 | 368.4 | 394.4 |
| claude_connect_cold | 1 | 34453 | — | — | — | — |
| claude_first_clause | 1 | 11176.0 | 11176.0 | 11176.0 | 11176 | 11176 |
| claude_full_turn | 2 | 7545.0 | 9412.5 | 10906.5 | 3810 | 11280 |
