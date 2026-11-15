# JARVIS — Foundation Decision

Date: 2026-09-21. Environment: Windows 11 (26200), Python 3.12, Node 24, no .NET SDK, no Go, no Rust, Intel Core Ultra 7 (no NVIDIA GPU), 1 display, Claude Code 2.1.278.

## Candidates inspected (current HEAD, shallow clone under `third_party/_inspect`)

| Repo | Commit | Stack | License | Verdict |
|---|---|---|---|---|
| danielsimonjr/Windows-MCP | 2a74b3e (2026-09-17) | C# / .NET 9, 63 tools, prebuilt `bundle/WindowsMcp.exe` | MIT | **Secondary (optional)** — no .NET SDK here to modify/build; 63 tools bloat Claude's context; prebuilt exe usable as-is for admin tools (registry, services, firewall, event log…). |
| sshh12/windows-computer-use-mcp | 8d367a8 (2026-06-07) | Python, FastMCP, 3.9k LOC, 4 deps | MIT | **SELECTED FOUNDATION** — per-monitor-DPI-v2, multi-monitor, `SendInput` scan codes, `capture_id` stale-frame model, UIA via `uiautomation`, built-in Windows OCR (no extra deps), process/window/clipboard/wait primitives. Same language as the rest of JARVIS. |
| awlevin/typesafe-computer-use | cc7b506 (2026-09-18) | Python, macOS only, uses `typesafe_sdk` | MIT | Architecture reference for JEV kind/item multi-question decisions and confidence gating. |
| browser-use/jev-ultrafast | 1231850 (2026-09-18) | Python, Chrome-only | MIT | Architecture reference for JEV **operation + target speculative heads in one request**; validated the real `/v1/systemone` schema. |
| coff33ninja/go-mcp-computer-use | 612ddf9 | Go (+CGO), raw COM UIA/WinRT OCR | Apache-2.0 | Reference only (no Go toolchain; Apache attribution rules noted). |
| zavora-ai/computer-use-mcp | 35de1a2 | Node/TS + native workers, cross-platform | MIT | Reference for verification/efficiency docs. Not reused. |
| wyofalcon/windows-desktop-companion | db858dc | Electron + Python (pywinauto) MCP | MIT | Reference for tray/companion UX + `.mcp.json` shape. pywinauto is heavier than `uiautomation`; not reused. |
| Einosensokami/windows-computer-use-mcp | 693239e | 115-line Node script | **No LICENSE file** | Not reused (unlicensed). |

JEV API verified against https://docs.typesafe.ai/api (2026-09-21): `POST https://api.typesafe.ai/v1/systemone`, bearer key, body `{model:"jev-latest", state, questions:{id:{type:"choice", instructions, criteria:{option:desc}}}}` → `answers[id] = {choice, probabilities, confidence}` + `usage`. Price $0.042/Mtok input, output free.

## Why sshh12 engine

1. Python — matches the only toolchain on this machine and the rest of JARVIS (voice, JEV, tray, tests).
2. Already solves the hard Windows plumbing correctly: DPI awareness before any capture/input, physical-pixel virtual-desktop coordinates, per-window PrintWindow capture, scan-code `SendInput`, UWP process resolution, `wait_for_window/file`.
3. Already has the stale-state primitive (`capture_id`) the PRD mandates.
4. Small enough to own: 3.9k LOC, MIT, readable.

## What is reused (vendored, MIT, attribution in THIRD_PARTY_NOTICES.md)

`jarvis/computer/engine/` ← `src/windows_computer_use/`: `dpi, displays, capture, input, keymap, coords, targets, images, winfind, windows (UIA), ocr, process, clipboard, system, artifacts, viewports`. `server.py`, `video.py`, `playscript.py`, `debughtml.py` are not vendored (JARVIS has its own MCP surface; no video/play-test loop needed).

Optional: `third_party/windows-mcp/WindowsMcp.exe` (prebuilt, MIT) registered as an **opt-in** second MCP server (`docs/OPTIONAL_WINDOWS_MCP.md`).

## What JARVIS implements itself

- `jarvis/computer/` — compact 15-tool MCP surface (`computer.*`), UIA element model (`id, role, name, automation_id, enabled, visible, bounds, value, patterns`), element actions (invoke/select/expand/collapse/set_value/wait).
- `jarvis/state/` — observation store: `capture_id`, timestamp, active window identity, state hash; stale detection + re-observe.
- `jarvis/orchestrator/` — router (deterministic → UIA unique → JEV → OCR → vision → human), verifiers, bounded waits, `act` micro-loop.
- `jarvis/jev/` — isolated JEV adapter (httpx, timeout/retry/cancel/metrics, operation+target heads, no-key fallback).
- `jarvis/safety/` — policy classification (safe / confirm / blocked), secret redaction, human confirm.
- `jarvis/voice/` — sounddevice mic → VAD → Gnani Prisma transcript; Gnani Timbre speech output; push-to-talk hotkey.
- `jarvis/app/` — tray + console window (tkinter), Claude Code session via `claude-agent-sdk` (uses the user's Claude Max login; no other LLM).
- `launcher/`, `dist/JARVIS.exe`, desktop `JARVIS.lnk`; `benchmarks/`, `tests/`.

Layout note: the TRD's suggested top-level `computer/ orchestrator/ jev/ …` are kept as sub-packages of one importable `jarvis/` package so the product is a single Python package; everything else (`launcher tests benchmarks docs models data logs dist third_party`) is at the root as specified.
