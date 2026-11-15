# OpenMuse → JARVIS capability audit (2026-09-23)

Mandated by the integration directive §0: inspect both codebases, compare feature-by-feature, decide what to transplant,
**before** major changes. This is the gate for everything that follows.

## What was actually inspected
- **OpenMuse** — `github.com/CopilotKit/openmuse` (README/features/architecture). Tech: TypeScript, React Native (Expo)
  for iOS/Android/web, Hono + CopilotKit runtime (Node), **PGlite/Postgres**, **Docker Linux container** computer,
  **Playwright Chromium**, pnpm monorepo. **License: MIT.** Self-described **alpha**.
- **CopilotKit MCP docs** — `docs.copilotkit.ai/mcp-servers`.
- **AG-UI docs** — `docs.copilotkit.ai/mastra/ag-ui`.

## Honest corrections to the directive's assumptions
1. **OpenMuse does NOT implement MCP.** The persistent-client + tool-cache pattern is a **CopilotKit general-docs**
   recommendation (`mcpClients` vs `mcpServers`, SSE/HTTP transports), *not* an OpenMuse feature. **JARVIS already runs a
   stdio MCP server that is persistent for the life of a Claude session** (spawned once, tools discovered once, reused),
   so the headline "rediscovers tools every run" problem does not apply here. Action: **keep stdio**, add explicit
   schema caching only if measurement shows a real per-turn discovery cost (it does not today).
2. **Voice, device push, graphical desktop, autonomous checkout/payments, health/bank/social connectors, generated
   tools** are **roadmap-only** in OpenMuse — not importable. JARVIS already has a superior local voice stack.
3. **The Linux-container computer and Playwright Chromium are the opposite of JARVIS's differentiator** (native Windows
   UIA/SendInput + real Chrome/Edge). Rejected outright (§46).
4. Nothing is code-vendored: OpenMuse is TS/React/Node/Docker; JARVIS is Python/Windows. Per §44 we **clean-room
   reimplement the concepts in Python**. THIRD_PARTY_NOTICES records the reference (MIT) even though no code is copied.

## Comparison table (updated from inspection)
| OpenMuse capability | OpenMuse status | JARVIS status | Value | Action |
|---|---|---|---|---|
| Durable task engine (plan/progress/state) | implemented | **absent** (turn-based only) | very high | **build** (P0) — SQLite, adapter around existing loop |
| Pause / resume / cancel / retry | implemented | partial (emergency_stop only) | very high | **build** (P0) |
| Follow-up queue | implemented | **absent** | very high | **build** (P0) |
| Task receipts / proof-of-work | implemented | **absent** (verification exists but not recorded per task) | high | **build** (P0) using existing verification |
| Background / delegated tasks | implemented | **absent** | high | **build** (P0, bounded concurrency) |
| SQL-lease recovery | implemented (Postgres leases) | absent | high | **adapt** — SQLite lease/heartbeat, no blind replay |
| AG-UI event protocol | implemented (SSE) | callbacks + polling | very high | **adapt** — internal event bus w/ AG-UI-compatible names (P0); SSE transport = future |
| Human takeover (browser) | implemented | emergency_stop only | very high | **build** (P1) — pause + return-with-re-observe on the real window |
| Recurring watches | implemented (public-page/price) | absent | high | **build** (P1) — local file/page, bounded backoff, dedup |
| Notifications (durable, dedup, backoff) | implemented | absent | high | **build** (P1) local store; orb signal |
| Goals / milestones | implemented | absent | medium | **build** (P1, thin) |
| Rich result cards (file/browser/pdf/task/receipt) | implemented | plain HUD text | high | **adapt** into the glass HUD (P1) — JARVIS's own design, not OpenMuse's |
| Editable / forget memory | implemented | store exists, no edit/forget surface | high | **extend** existing `jarvis.memory` (P1), never a 2nd store |
| Personal context | implemented | in memory already | medium | merge into existing memory |
| Source / evidence backing | implemented | absent | high | **build** (P1) — link results to real receipts/URLs, never fabricate |
| Persistent MCP + tool cache | **CopilotKit docs, not OpenMuse** | stdio already persistent/session | medium | **evaluate → keep stdio**; measure; cache only if a cost exists |
| Mobile app (React Native) | implemented | absent | future | **prepare event contract only** — do NOT ship/fake a mobile app (§29) |
| Web client | implemented | orb only | future | same event contract; future |
| Remote control / device push | push = roadmap | absent | future | **spec a security model** (§27/§28); do NOT open the PC (no shipped gateway) |
| Linux container computer | implemented | native Windows | — | **reject** (§46) |
| Playwright Chromium | implemented | real Chrome/Edge via UIA | — | **reject** (concepts only) |
| PGlite/Postgres store | implemented | SQLite (history) | — | **reject** — reuse SQLite |
| CopilotKit Intelligence service | external (API key) | Claude is the brain | — | **reject** |
| Gmail / Calendar connectors | implemented (OAuth) | not core | medium | **optional/deferred** — this session's Gmail MCP is separate |
| Daily conversation model | thread-based | **one date = one conversation** | — | **preserve** JARVIS's model; AG-UI is transport only (§24/§49) |
| Safety / approvals | stored-action review | confirm/blocked already | — | **reuse** JARVIS safety; new subsystems call it (§50) |
| JEV budget | n/a | ₹1,000/mo hard ceiling | — | **preserve**, untouched (§50) |
| RL policy | n/a | contextual bandit | — | **preserve**, untouched |

## Plan (priority-ordered, honest about scope)
- **P0 — build this pass:** durable task engine (`jarvis/tasks/`) with lifecycle + SQLite persistence + lease/recovery;
  pause/resume/cancel/retry; follow-up queue; receipts from real verification; an **event bus** with AG-UI-compatible
  event names that becomes the single source for orb/HUD state and the future client contract. Foreground/background
  tasks with bounded concurrency. Tests for all of it.
- **P1 — next:** human takeover (pause → real manual control → return + re-observe), recurring watches (local, bounded,
  dedup), notifications, editable/forget memory surface, source/evidence links, rich HUD cards in the existing glass
  design, goals.
- **P2 — future-ready (contract only, nothing exposed):** the event bus is the contract a future iPhone/web client
  consumes read-only; a remote gateway is **specified** with auth/authorization tiers/encryption/replay/revocation but
  **not shipped** — the PC is never bound to a public endpoint (§27/§28).
- **P3 — rejected:** Linux computer, Playwright Chromium, PGlite/Postgres, duplicate memory/conversation/safety/budget,
  CopilotKit Intelligence, faked mobile/push/voice.

## Guardrails honored throughout
One authoritative task engine, one memory (existing), one conversation model (existing daily), one safety/budget/privacy
(existing). New subsystems are **adapters that call existing services**, never parallel replacements. No core capability
is broken; every integrated capability is tested; the PC is never exposed.
