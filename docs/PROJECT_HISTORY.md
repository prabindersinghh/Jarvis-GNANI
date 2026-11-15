# JARVIS — how it was formed (project history & timeline)

A single record of everything built into JARVIS so far, in the order it happened, with dates. JARVIS is a personal
Windows computer agent whose **only brain is Claude Code** (over the user's Claude login); the local engine under
`jarvis/` is its hands, JEV (TypeSafe) is a fast reflex layer, and Gnani Prisma/Timbre provide its speech input/output. Product
root: `D:\JARVIS`. Each phase below was built to an explicit directive, tested, and accepted (see `docs/ACCEPTANCE.md`).

## The idea
One always-present orb on the Windows desktop that you talk to. It hears you through Gnani Prisma, thinks with Claude,
and acts on the real computer through a Model-Context-Protocol tool surface (`computer_*`) that prefers the cheapest
reliable control tier — deterministic shortcuts → UI Automation → a JEV reflex loop → OCR → vision — always verifying
what it did, always asking a human before anything destructive, and never exceeding a hard ₹1,000/month JEV budget.

## Timeline

### 2026-09-21 — Phase 1: the agent (initial build)
The `MASTER_BUILD_PROMPT`. Built the whole spine: the 18-tool MCP computer server (DPI-aware capture, SendInput,
UIA element model, OCR), the router with its control tiers + verification + stale-state protection, the JEV adapter,
speech input/output adapters (now Gnani Prisma/Timbre), the Claude Code session (agent SDK), the tray app, and the packaged desktop
launcher. Evidence: `docs/ACCEPTANCE.md` (24 unit + 12 live tests), `docs/TRD.md`, `docs/PRD.md`.

### 2026-09-21 — Phase 2: upgrade (see docs/UPGRADE_2026-09-21.md)
Made JEV live with the real key (correct picks, ~0.4 s warm, 0 calls on unambiguous tasks); added `computer_run`
batching (33→14 Claude turns, 27→7 tool calls on a 3-task benchmark); persistent local experience memory; TTS response
modes (silent/concise/conversational) with mic suppression + barge-in; and privacy redaction + screenshot policy.

### 2026-09-21 — Phase 3: the orb UI (see docs/ORB_UI.md)
A persistent bottom-right orb driven only by real runtime state (thinking-orbs engine), a glass HUD on click, a status
strip of local health, and the JEV ₹1,000/month ceiling enforced at ₹900, fail-closed.

### 2026-09-21 — Phase 4: orb UI redesign — true transparency
Replaced the opaque WinForms host with a **composition-hosted WebView2** (`jarvis/ui/comphost.py`): DirectComposition
per-pixel alpha so the orb genuinely floats, with the desktop around it still clickable (hit-test rects). Radial menu
removed for a compact glass menu.

### 2026-09-21b — Phase 5: orb life + daily conversation
A permanent water-bubble ripple (alive even when idle; the orb never disappears, even under full-screen apps). "One
date = one conversation" persisted in SQLite (`jarvis/history.py`); the HUD shows today's transcript; the full day is
never sent to Claude.

### 2026-09-21 — Phase 6: perpetual learning / RL (see docs/RL_LEARNING.md)
A contextual-bandit policy over the router's decision hook (`jarvis/learning/`), learning from real verified outcomes to
serve known decisions locally and drive **JEV usage down** (benchmark 3→0 JEV calls/episode, success held 3/3), with a
background worker that promotes on measured improvement and rolls back on regression. Safety and the JEV budget sit
**outside** RL and cannot be bypassed.

### 2026-09-22 — Phase 7: low-latency voice + fast execution (see docs/PERFORMANCE_UX_2026-09-22.md)
Measured the real pipeline first (`benchmarks/latency/`), then made it feel immediate: **adaptive end-of-turn**
detection (`jarvis/voice/endpoint.py`, 930→570 ms on short commands, no more hesitation cut-offs), **streaming
clause-by-clause TTS**, an **instant local acknowledgement** (~90 ms vs Claude's first token at 2.9–11 s), **voice
barge-in** (`jarvis/voice/bargein.py`), and **JEV connection pre-warm** — all verified offline; live audible tests left
for an away session. 81→88 non-live tests green.

### 2026-09-22 — Phase 8: memory graph, graphify, and private GitHub backup (this document)
- **Memory graph** (`jarvis/graph/`): a local, private, redacted knowledge graph of JARVIS's own experience
  (date→session→turn→app→tool→outcome→topics), updated every turn, queryable for recall, exportable to graphify's
  format. Never networked.
- **graphify** (`graphifyy`, pinned in `requirements.txt`): builds a queryable graph of the JARVIS **codebase** so
  Claude can recall architecture (fully local, tree-sitter, no key). See `docs/GRAPH.md`.
- **Private GitHub backup**: the repo was initialized and pushed **private**, with a hardened `.gitignore` that keeps
  the JEV/TypeSafe key (`data/config/.env`) and **all** personal data (`data/`: conversations, memory, experiences,
  policies, budget ledger, graph) off GitHub — so even if the repo is ever made public, nothing private leaks. The
  purpose is recovery: a fresh clone + `launcher/install.ps1` rebuilds the venv, models and exe from scratch.

## Architecture at a glance
```
voice (Gnani Prisma STT · adaptive endpoint · barge-in · Gnani Timbre TTS)  ─┐
orb UI (composition WebView2, per-pixel alpha, live ripple)     ├─►  JarvisApp (jarvis/app)  ─►  Claude Code (brain, agent SDK)
daily history (SQLite) · memory · memory graph · learning (RL)  ─┘                                   │
                                                                                                     ▼
                                              router tiers → deterministic / UIA / JEV reflex / OCR / vision  (jarvis/orchestrator, jarvis/computer)
                                              guarded by: verification · safety (confirm/blocked) · privacy redaction · JEV ₹1,000/month budget
```

## Invariants that never change
Claude is the only brain. Every meaningful action is verified. Destructive/credential/payment/publish actions require a
human confirmation. Secrets are never logged or committed. JEV spend is hard-capped at ₹1,000/month, fail-closed.
Audio and personal data stay local. Safety and budget sit outside the learning layer.
