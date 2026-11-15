# JARVIS — acceptance record (2026-09-21)

Evidence gathered on the build machine (Windows 11 26200, Intel Core Ultra 7, 1920×1200 @150 % DPI, Smart App Control ON).
Numbers: `benchmarks/results.md`. Test suites: `tests/test_unit.py` (24 pass), `tests/test_live.py` (12 pass, real desktop).

## Launch
- [x] `D:\JARVIS` contains the product (single `jarvis/` package + launcher/tests/benchmarks/docs/models/data/logs/dist/third_party).
- [x] `%USERPROFILE%\Desktop\JARVIS.lnk` exists → `D:\JARVIS\dist\JARVIS.exe`, cwd `D:\JARVIS`, icon `launcher\jarvis.ico`.
- [x] Icon: generated ring icon embedded in the exe and set on the shortcut; visible in the tray overflow ("JARVIS — idle").
- [x] Clicking it starts JARVIS: `Start-Process JARVIS.lnk` → console window + tray + Claude connected + Whisper loaded
      (`logs/jarvis.log`). Process ancestry: `pythonw.exe` detached, no `Code.exe`/terminal parent.
- [x] VS Code not required: launcher = PyInstaller exe → venv `pythonw.exe`; Claude CLI = native `~/.local/bin/claude.exe`
      (copy in `dist/claude/`). *Not exercised with VS Code fully closed because this build session runs inside it — see "Left for the user".*

## Voice
- [x] Local Whisper works: `base.en` int8 on CPU, 3.9 s utterance → 0.8–1.2 s; transcript of synthesized speech exact
      ("Jarvis, open Notepad and type hello world"). `small` available via settings (≈3 s, same accuracy on the fixture).
- [x] Microphone works: `Microphone Array on SoundWire Device` opens at 44.1 kHz → 16 kHz; live capture returned samples.
- [x] Transcript reaches the agent: PTT hotkey (Ctrl+Alt+J) in the running app → recorder → Whisper → Claude → Notepad
      opened with "Hello World" typed and read back via UIA. (Audio injected via `JARVIS_TEST_AUDIO` because the laptop's
      mic array applies echo cancellation, which strips speaker playback; the physical mic path was verified separately.)

## Claude
- [x] Claude Code uses the JARVIS MCP (16 `computer_*` tools) over stdio from `.mcp.json` and from the app (agent SDK).
- [x] Complex tasks are planned: Notepad save/reopen/verify (16 turns), Calculator 12×34 (5 turns), Edge form fill (9 turns).

## Computer
- [x] keyboard (SendInput scan codes + paced Unicode; paste-first typing with read-back verification)
- [x] mouse (move/click/double/right/drag/scroll via SendInput, physical pixels)
- [x] UIA (element model, invoke/select/toggle/expand/set_value/text/wait; Notepad, Save-As dialog, Calculator, Edge DOM)
- [x] screenshots (desktop / display:N / window via PrintWindow / region; downscale + capture_id)
- [x] windows (list/focus/close/min/max/restore/move/resize/wait/wait_gone — verified)
- [x] processes (launch verified by *new* window, list, kill with confirm, wait)
- [x] shell (PowerShell/cmd, cwd, exit codes, timeout, secret redaction)
- [x] files (read/write/copy/move/rename/delete/list/search/info/mkdir/archive/extract — verified)
- [x] browser (Edge local page: type by accessible name, click Submit, verify text + URL) — DOM via UIA
- [x] multi-monitor/DPI: per-monitor-v2 awareness, monitor enumeration with DPI/scale, physical-pixel coordinates
      (this machine reports 1920×1200 @1.5 where a non-aware process sees 1280×800). Only one monitor attached to test.
- [x] OCR: Windows.Media.Ocr with word/line bounding boxes (≈0.5 s per window).

## JEV
- [x] Adapter implements the verified `/v1/systemone` schema (operation + speculative click/type target heads).
- [x] Structured candidate selection, timeout, retry (503/429 backoff), cancellation, invalid-answer rejection, metrics —
      covered by unit tests against a mock server. **Live JEV calls not exercised: no `TYPESAFE_API_KEY` on this machine.**
      Put the key in `data/config/.env` and run `python -m benchmarks.run --jev`.
- [x] No unnecessary JEV calls: deterministic and unique-UIA paths never touch it; same-bounds wrappers and role priority
      resolve most duplicates locally; without a key the router falls through to OCR / `needs_vision`.

## Routing
- [x] deterministic actions bypass models (`tier: deterministic` on app/key/shell/file/window/type)
- [x] unique UIA targets bypass JEV (`tier: uia`)
- [x] JEV handles ambiguous structured UI (adapter + `computer_act` loop; live run pending key)
- [x] OCR/screen fallback works (`computer_screen ocr`, OCR candidates in router)
- [x] Claude vision is the fallback (`needs_vision` error + screenshot tool; never used in the three E2E runs)

## Verification / stale state
- [x] app launch → new window; click → UI fingerprint change; type → value read-back; file ops → filesystem; shell → exit
      code; browser → title/URL; close → window gone (and the Save-changes dialog is surfaced when it blocks).
- [x] stale UI detected: superseded `capture_id` and live-fingerprint mismatch both refuse the action and return a fresh
      observation (unit + live tests).

## Safety
- [x] confirmations: recursive delete, destructive git, firewall/registry/credential/security changes, remote script
      execution, forced kills, payments/publishing clicks, credential fields → topmost Yes/No dialog (console-hosted when
      the app runs), remembered 2 min, default deny on timeout.
- [x] boundaries: UAC (`consent.exe`), LogonUI, CredentialUIBroker, CAPTCHA/MFA/Windows Hello window titles → blocked,
      `human_required` returned to Claude. Secrets redacted from shell output/logs; `.env` denied to Claude's Read tool.

## Performance (medians, this machine)
- native key/mouse 0–53 ms · UIA observe 101 ms · routed UIA click ≈400 ms incl. verification · type 20 chars 212 ms
  verified · OCR 474 ms · screenshot 33 ms · MCP round-trip 2 ms (status) / 102 ms (observe) · Whisper 1.2 s / 3.9 s audio
  · Claude connect 12 s (once per app start) · Claude turn 4–6 s · launch→observe→type→verify chain 2.2 s.
- Optimizations applied from measurement: paste-first typing (garbled SendInput in WinUI), physical click instead of
  synchronous Invoke for visible controls (1019 → 400 ms), skip second UIA walk for observations < 400 ms old.

## Packaging
- [x] packaged launcher works (`dist/JARVIS.exe`, runs under Smart App Control); `launcher/install.ps1` rebuilds everything.
- [x] desktop icon works; D: drive respected (models/data/logs/venv/dist all under `D:\JARVIS`).

## Upgrade 2026-09-21 (see docs/UPGRADE_2026-09-21.md)
- [x] JEV live with the real key: correct picks, ~0.4 s warm, candidate enrichment separates identical names; never
      called for deterministic/unique targets (0 JEV calls on unambiguous benchmark tasks).
- [x] `computer_run` batches: Claude turns 33 → 14, tool calls 27 → 7 on the 3-task benchmark; completion 3/3.
- [x] Local memory (`data/memory`), TTS with modes + mic suppression + barge-in, privacy redaction/screenshot policy.
- [x] Real mic confirmed by an actual spoken utterance ("Hi, how are you?") transcribed and answered aloud.

## Orb UI 2026-09-21 (see docs/ORB_UI.md)
- [x] persistent bottom-right orb (thinking-orbs engine, colour-mapped to real runtime state), glass panel on click,
      status strip, context menu, confirm cards; topmost / no-focus-steal / DPI-aware / work-area-aware / full-screen-aware.
- [x] JEV hard ceiling ₹1,000/month (enforced at ₹900, fail-closed, persisted, auto-reset) — live fallback verified with 0 HTTP calls.
- [x] Shortcut now targets the signed `pythonw.exe` (Smart App Control blocked the rebuilt unsigned exe). Orb visible 5.6 s after click.
- [x] 6 live orb tests + 16 live computer-control tests + 39 unit tests green on the packaged app.

## Orb UI redesign 2026-09-21 (docs/ORB_UI.md) - visual acceptance
- [x] No rectangular backing around the collapsed orb (true per-pixel alpha via composition-hosted WebView2).
- [x] Orb genuinely floats with a luminous glow; desktop around it stays clickable (WM_NCHITTEST hit-rects).
- [x] Left click = continuous transformation into the glass HUD (no window resize); Esc collapses; focus restored.
- [x] Context menu is a compact rounded glass rectangle (radial menu removed).
- [x] Glass HUD legible + spatial; no large dashboard; status strip = local runtime health, no API calls.
- [x] States have distinct colour/animation; subtle multi-colour; no "IDLE" label inside the orb.
- [x] Idle CPU: msedgewebview2 = 0% at rest (calm freeze); residual is the Claude brain subprocess, not the UI.
- [x] JEV Rs 1,000/month ceiling, fail-closed, Rs 900 enforcement, persistence - unchanged and tested.
- [x] Packaged desktop launch intact (Desktop JARVIS.lnk -> signed pythonw); orb visible ~5-6 s after click.
- [x] 7 live orb tests + 45 unit + 16 live computer-control tests green.

## Orb life + daily conversation 2026-09-21b (docs/ORB_UI.md)
- [x] Outer membrane continuously ripples like a subtle water bubble; alive even when idle; state changes its character, never stops it.
- [x] Orb never intentionally disappears for ordinary/full-screen apps, focus changes, or inactivity (fullscreen-hide removed; topmost reasserted).
- [x] One date = one conversation; sessions are markers within the date; restart/reconnect/reopen never fork it (SQLite, verified across a real restart).
- [x] Clicking the orb reveals today's transcript (session dividers + YOU/JARVIS); collapsing hides it but persists the conversation; orb keeps rippling.
- [x] Voice and text share the same daily conversation; midnight rollover automatic; redaction applied; full day never sent to Claude.
- [x] Streamed reply committed once (no streamed+spoken duplicate). Claude session keyed per-date for same-day continuity.
- [x] Idle webview2 CPU 0 % with the ripple on; no RL added; JEV ₹1,000 ceiling and all prior tests intact (51 unit green).

## Perpetual learning / RL 2026-09-21 (docs/RL_LEARNING.md)
- [x] Real experiences auto-collected (SQLite, redacted, bounded); rewards auto-generated from verification.
- [x] Learning is automatic — no training mode, no labelling; background worker at below-normal priority.
- [x] Policy learns routing/target strategy; JEV usage learned DOWN when unnecessary (benchmark: 3→0 JEV calls/episode, success 3/3).
- [x] Candidate evaluated before promotion; bad candidate rejected; production regression auto-rolls-back to parent (tested).
- [x] Learning survives restart (versioned atomic policies in data/policies/; corrupt-file recovery tested).
- [x] Experience local-only; secrets never enter the dataset; safety + JEV ₹1,000 ceiling sit OUTSIDE RL and cannot be bypassed.
- [x] Daily-conversation, memory, budget, safety, privacy architectures untouched; 62 non-live tests green; RL hook replaces the inactive stub.

## Low-latency voice + fast execution 2026-09-22 (docs/PERFORMANCE_UX_2026-09-22.md)
- [x] Baseline measured first with a new offline harness (`benchmarks/latency/`, p50/p75/p95) — safe to run while the user works (no mouse/audio/app-launch).
- [x] Adaptive end-of-turn (`jarvis/voice/endpoint.py`): short command 930→570 ms trailing; hesitant speaker no longer cut off (legacy fixed 0.9 s rule did cut). Deterministic, tested.
- [x] Instant local acknowledgement (`jarvis/voice/ack.py`): ~90 ms vs Claude first token 2.9–11 s; short deterministic commands stay silent.
- [x] Streaming clause-by-clause TTS (`SpeechChunker`/`StreamingSpeaker`): speaks the first sentence as it streams, never re-speaks the final message; resumes the mic only when audio drains.
- [x] Voice barge-in (`jarvis/voice/bargein.py`): monitors during TTS instead of dropping frames; stops speech + cancels the obsolete reply/generation; ignores coughs/quiet echo (tested).
- [x] JEV connection pre-warm (token-free, budget untouched). Router/verification/safety/privacy/RL unchanged. 88 non-live tests green (was 81 + 7 latency).
- [x] **Live run (user away, 2026-09-22):** packaged app booted clean with all new code — log shows `JEV connection pre-warmed`
      (token-free GET→404), learning worker, Whisper (3.4 s), mic, Claude connected, TTS spoke. Live desktop suite
      `test_live.py` 16/16 (2 Notepad tests flake only when racing consecutive runs; pass in isolation). Live orb 6/7
      (the 1 fail is HUD pixel-brightness sampling, wallpaper-sensitive, unrelated to this work). New real-audio suite
      `tests/test_live_voice.py` **4/4**: adaptive endpointer on the real recorded utterance; real streaming-TTS
      playback (in order, resumes once, no double-speak); real ack playback; and **§13 zero self-echo** — JARVIS
      speaking aloud while the real mic monitored did NOT self-trigger barge-in.
- [~] One sliver unverifiable without a person: a *human voice* interrupting mid-reply (I validated the barge-in
      detector + the no-self-echo direction, but cannot physically speak over the speaker to trigger it).

## Memory graph + graphify + private GitHub backup 2026-09-22 (docs/GRAPH.md, docs/PROJECT_HISTORY.md)
- [x] Memory graph (`jarvis/graph/`): local, private, redacted, deduped, bounded, corrupt-file-safe; one turn → date/session/turn/app/tool/outcome/topic links; recall + graphify export; 7 tests.
- [x] graphify (`graphifyy==0.9.65`) installed + code graph built for `jarvis/` (~1034 nodes / 2294 edges, local AST, no key); `/graphify` skill installed for JARVIS's Claude brain.
- [x] Private GitHub repo: hardened `.gitignore` keeps the JEV key (`data/config/.env`) and ALL personal data (`data/`) off GitHub even if made public; secret scan clean; `.env.example` provided; fresh clone + install.ps1 rebuilds everything.

## Left for the user
1. **Close VS Code and double-click the desktop icon** to confirm independence on your side (structurally verified here).
2. If Windows hides the tray icon, drag it out of the overflow (Settings → Personalization → Taskbar → Other system tray icons).
3. Pick a voice: `settings.json → "tts_voice": "Zira"` (or "David"); `tts_rate` −10..10.
4. Multi-monitor: only one display was attached here; the orb follows the primary work area by design — tell me if it misplaces on your setup.
