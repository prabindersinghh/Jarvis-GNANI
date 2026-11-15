<div align="center">

# JARVIS · GNANI Edition

### A voice-controlled personal agent that operates your Windows PC — Claude as the brain, your desktop as the hands.

**Speak. It plans. It acts. It verifies.**

![Platform](https://img.shields.io/badge/platform-Windows%2011-0078D4?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Brain](https://img.shields.io/badge/brain-Claude%20Code-D97757)
![Protocol](https://img.shields.io/badge/tools-MCP%20·%2018-6E40C9)
![Voice](https://img.shields.io/badge/voice-local%20Whisper-111111)
![Tests](https://img.shields.io/badge/tests-136-2EA043)

</div>

---

## What it is

JARVIS · GNANI is a Windows computer agent you talk to. A floating orb sits on your desktop. Hold a hotkey, say what
you want, and JARVIS hears you locally, reasons with Claude, and drives the real machine: apps, windows, browser,
files, terminal, keyboard and mouse. Every meaningful action is checked afterwards, and anything destructive waits
for your confirmation.

> *"Open Chrome, find the latest Qwen paper, download it to my research folder, rename it properly, and summarize it."*

JARVIS turns that into one plan, executes it locally through the cheapest reliable control path, verifies the download
and the rename, and reports back in a sentence.

**GNANI** (ज्ञानी, *the knowing one*) is this edition's name: the build where JARVIS keeps learning from its own
verified outcomes, remembers context through a private local memory graph, and gets faster at the work you repeat.

## The core idea: cheapest control tier first

Most computer-use agents take a screenshot, ask a model where to click, click, and repeat. That is slow and expensive.
JARVIS routes every action down a ladder and stops at the first rung that works:

```
1. DETERMINISTIC        known shortcut, app launch, shell, file op       → no model at all
2. UI AUTOMATION        structured Windows elements by id / name          → ~3 ms to find a control
3. JEV REFLEX           fast structured pick between ambiguous targets    → ~0.4 s warm
4. OCR                  text + bounds when the app exposes no structure
5. CLAUDE VISION        pixels, only when genuinely needed
6. HUMAN                UAC, login, MFA, CAPTCHA: handed back, never bypassed
```

Claude plans the goal. The local engine carries out the routine clicks. Claude is never asked to micromanage a mouse.

## Architecture

```
            YOU ── "JARVIS…"
                     │
      ┌──────────────▼───────────────┐
      │  VOICE  (all local)          │  push-to-talk · VAD · faster-whisper
      │  adaptive end-of-turn        │  instant "On it." ack · barge-in
      │  streaming clause-by-clause  │  SAPI text-to-speech
      └──────────────┬───────────────┘
                     ▼
      ┌──────────────────────────────┐
      │  BRAIN  Claude Code          │  claude-agent-sdk over your Claude login
      │  plans · codes · reasons     │  memory block injected per request
      └──────────────┬───────────────┘
                     ▼  MCP (stdio)
      ┌──────────────────────────────┐
      │  JARVIS MCP  · 18 tools      │  router → verify → stale-state guard
      │  orchestrator + act loop     │  batch execution with checkpoints
      └───┬──────────┬──────────┬────┘
          ▼          ▼          ▼
     DETERMINISTIC  UIA       JEV · OCR · VISION
          └──────────┼──────────┘
                     ▼
         Windows: apps · browser · terminal · files
                     ▼
              VERIFIED RESULT
```

| Layer | Role | Lives in |
|---|---|---|
| **Brain** | Claude Code via the Agent SDK. The only general-purpose reasoner. | `jarvis/app` |
| **Hands** | DPI-aware capture, SendInput, UI Automation, OCR, multi-monitor | `jarvis/computer` |
| **Router** | Tier selection, verification, waits, batch runs, JEV act loop | `jarvis/orchestrator` |
| **Reflexes** | JEV (TypeSafe) structured decisions, hard budget cap | `jarvis/jev` |
| **Ears & voice** | Mic, VAD, Whisper STT, endpointing, barge-in, TTS | `jarvis/voice` |
| **Safety** | Policy (safe / confirm / blocked), confirm dialog, secret redaction | `jarvis/safety` |
| **Learning** | Contextual-bandit policy over router decisions, promote / rollback | `jarvis/learning` |
| **Memory** | Experience memory, daily conversations, private memory graph | `jarvis/memory` `jarvis/graph` `jarvis/history.py` |
| **Tasks** | Durable task engine, follow-ups, receipts, watches, notifications | `jarvis/tasks` |
| **Orb UI** | Composition-hosted WebView2 with true per-pixel transparency | `jarvis/ui` |

## The 18-tool MCP surface

| Group | Tools |
|---|---|
| Observe | `computer_observe` · `computer_screen` · `computer_status` · `computer_wait` |
| Act | `computer_click` · `computer_type` · `computer_key` · `computer_scroll` · `computer_drag` · `computer_element` |
| System | `computer_app` · `computer_window` · `computer_shell` · `computer_file` |
| Orchestrate | `computer_run` (batched steps with `expect` checkpoints) · `computer_act` (local JEV loop for a visible UI goal) |
| Govern | `computer_confirm` · `computer_memory` |

Every tool returns a `verification` block. `verified: false` is treated as failure, and a `stale_state` error forces a
fresh observation before anything is retried. Input sent is never taken as proof of success.

## Measured performance

Medians on the development machine (Windows 11, Intel, Python 3.12). Full tables in [`benchmarks/results.md`](benchmarks/results.md).

| Operation | Median |
|---|---:|
| Find a UIA control | **3 ms** |
| Read UIA text | **4 ms** |
| Window screenshot | **33 ms** |
| Native click | **53 ms** |
| Full UIA observe | **101 ms** |
| Type 20 chars, paste + read-back verified | **212 ms** |
| App launch, verified | **605 ms** |
| Launch → observe → type → verify, end to end | **2.18 s** |

Architectural wins that do not depend on machine load ([`benchmarks/latency/comparison.md`](benchmarks/latency/comparison.md)):

- **Batching:** `computer_run` cut a three-task benchmark from 33 → 14 Claude turns and 27 → 7 tool calls.
- **Adaptive end-of-turn:** short commands finalize in 570 ms instead of 930 ms, and hesitant speakers are no longer cut off.
- **Instant acknowledgement:** a local "On it." in about 90 ms, against Claude's first token at 2.9 to 11 s.
- **Learning:** the RL policy took JEV calls per episode from 3 → 0 while holding a 3/3 success rate.

## Safety model

- **Confirmation required** for deletion, destructive git, registry / firewall / security changes, credentials,
  payments, and publishing or sending. The dialog is raised by the engine, not by the model, and a denial is final.
- **Never bypassed:** UAC secure desktop, lock screen, MFA, CAPTCHA, biometrics. JARVIS hands control back.
- **Secrets stay out:** typing is redacted in logs, `data/config/.env` is never read into context, and the
  `.gitignore` keeps every key and all personal data off GitHub.
- **Budget is fail-closed:** JEV spend is hard-capped at ₹1,000/month (enforced at ₹900) in `jarvis/jev/budget.py`.
- **Outside the learner:** safety rules and the budget sit outside the RL layer, so learning can never relax them.
- **Local by default:** audio, transcripts, memory and the memory graph never leave the machine.

## Quick start

**Requirements:** Windows 11 · Python 3.12 · an active Claude Code login · optional TypeSafe (JEV) key.

```powershell
git clone https://github.com/prabindersinghh/Jarvis-GNANI.git D:\JARVIS
cd D:\JARVIS
copy .env.example data\config\.env      # optional: paste TYPESAFE_API_KEY
powershell -ExecutionPolicy Bypass -File launcher\install.ps1
```

The installer rebuilds the virtual environment, downloads the Whisper model, packages the app and places
`JARVIS.lnk` on your desktop. Then:

1. Click **JARVIS** on the desktop. The orb appears bottom-right.
2. Hold **Ctrl + Alt + J** and speak, or click the orb to type.
3. Watch it work. No terminal or VS Code needed.

Without a JEV key, JARVIS degrades gracefully to local UIA, OCR and vision tiers.

## Development

```powershell
.venv\Scripts\python.exe -m pytest tests -q              # unit + fast integration
.venv\Scripts\python.exe -m pytest tests -q -m live       # live GUI tests (moves the mouse)
.venv\Scripts\python.exe -m benchmarks.run                # layer latency benchmark
.venv\Scripts\python.exe -m jarvis.computer               # run the MCP server on stdio
.venv\Scripts\pythonw.exe -m jarvis.app                   # run the product
.venv\Scripts\python.exe -m jarvis.graph recall <term>    # query the memory graph
```

## Repository map

```
jarvis/
  app/            tray, Claude session, console
  computer/       MCP server + vendored Windows engine (capture, input, UIA, OCR, DPI)
  orchestrator/   router, verify, waits, act loop, batch, metrics
  jev/            TypeSafe adapter + monthly budget
  voice/          mic, Whisper, endpointing, barge-in, ack, TTS
  safety/         policy, confirmation, privacy redaction
  learning/       bandit policy, rewards, experience
  memory/ graph/  experience memory, private memory graph
  tasks/          durable tasks, watches, receipts, events
  ui/             orb (WebView2 composition host)
launcher/         installer, launcher, desktop shortcut
benchmarks/       latency + task + learning benchmarks
tests/            136 tests (unit, integration, live)
docs/             design, history, acceptance, usage
```

## Documentation

| Doc | What's inside |
|---|---|
| [`PRD.md`](PRD.md) · [`TRD.md`](TRD.md) | Product and technical requirements |
| [`docs/PROJECT_HISTORY.md`](docs/PROJECT_HISTORY.md) | How JARVIS was built, phase by phase |
| [`docs/FOUNDATION_DECISION.md`](docs/FOUNDATION_DECISION.md) | Why this foundation over the alternatives |
| [`docs/USAGE.md`](docs/USAGE.md) | Day-to-day use |
| [`docs/ORB_UI.md`](docs/ORB_UI.md) | The orb interface |
| [`docs/RL_LEARNING.md`](docs/RL_LEARNING.md) | The perpetual-learning layer |
| [`docs/GRAPH.md`](docs/GRAPH.md) | Memory graph and codebase graph |
| [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) | Acceptance record |
| [`CLAUDE.md`](CLAUDE.md) | Operating rules for the brain |

## Acknowledgements

Built on open-source work, credited in full in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md): the Windows engine
from **sshh12/windows-computer-use-mcp** (MIT), the optional **Windows-MCP** binary, the **thinking-orbs** visual
engine, **faster-whisper**, **pywebview**, **graphifyy**, and the Anthropic **Claude Agent SDK**.

---

<div align="center">

Built by **[Prabinder Singh](https://github.com/prabindersinghh)**

</div>
