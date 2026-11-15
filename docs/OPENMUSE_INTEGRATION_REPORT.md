# OpenMuse → JARVIS integration report (2026-09-23)

Companion to `docs/OPENMUSE_INTEGRATION_AUDIT.md`. Records what was actually built, adapted, rejected, and honestly
deferred. Guiding principle (§64): *harvest the best engineering ideas from OpenMuse; do not clone the product.* JARVIS
remains the source of truth; every new subsystem is an **adapter that calls existing services**, never a replacement.

## Features audited
OpenMuse (`CopilotKit/openmuse`, MIT, alpha) — TypeScript / React Native (Expo) / Hono + CopilotKit / PGlite / Docker
Linux container / Playwright Chromium. Implemented: durable tasks (plan/progress/pause/resume/cancel/retry/approvals/
receipts), SQL-lease recovery, follow-up queue, background/delegated tasks, human takeover, persistent browser profiles,
rich cards, goals/milestones, recurring watches, deduped alerts + backoff + durable notifications, editable memory,
streamed AG-UI events, RN mobile+web, Gmail/Calendar, PDF viewer. **Roadmap-only (not imported, not faked):** voice,
device push, graphical desktop, autonomous checkout/payments, health/bank/social connectors, generated tools. **Not in
OpenMuse at all:** MCP (that is a CopilotKit general-docs pattern).

## Imported / built (clean-room in Python) — SHIPPED & TESTED
- **Durable task engine** — `jarvis/tasks/store.py` (SQLite, atomic, lifecycle: created→queued→planning→running→waiting→
  needs_input→paused→cancelling→verifying→completed/failed/cancelled), `manager.py` (transitions, guards). One
  authoritative engine (§48). Tasks are distinct from conversations (§5, §24).
- **Pause / resume / cancel / retry** (§7) — first-class, bounded retry (`task_max_retries`), resume re-enters RUNNING
  so the caller re-observes before repeating (§7).
- **Follow-up queue** (§8) — "after that / then …" said while a task runs is *queued*, not run now and not a barge-in;
  `jarvis/tasks/intent.py` classifies FOLLOWUP vs INTERRUPT vs NEW; the queue auto-advances after each turn.
- **Receipts / proof-of-work** (§11) — `jarvis/tasks/receipts.py`, built from **real** tool counts + the real tool-error
  signal + JARVIS verification, never the model's claim; persisted on the task; a compact line shows in the HUD.
- **Event bus** (§13, §14) — `jarvis/tasks/events.py`, AG-UI-compatible event vocabulary (`RUN_STARTED`,
  `TOOL_CALL_START/RESULT`, `STATE_DELTA`, `TASK_*`, `TTS_*`, `VOICE_*`, `NOTIFICATION`, `WATCH_TRIGGERED`, …) with a
  `to_agui()` renderer and a bounded ring buffer + `snapshot()` for a future client on connect. Fed by the app's real
  runtime callbacks. Bad subscribers cannot break the runtime.
- **Lease / crash recovery** (§10) — a running task carries a pid + expiring lease (heartbeat renews); on restart an
  orphaned task is surfaced as `needs_input` for review, **never blindly replayed** (uncertain side effects).
- **Background tasks** (§9) — kind=background with bounded concurrency (`MAX_BACKGROUND=2`); does not block the mic/orb.
- **Notifications** (§21, §39) — `jarvis/tasks/notify.py`, durable, deduplicated (window), local; emits a NOTIFICATION
  event. High-value only (watch triggers, human-required) — not a popup per action.
- **Recurring watches** (§20, §56) — `jarvis/tasks/watch.py`, local file/text conditions (offline, safe) + read-only
  public URL; bounded exponential backoff on failure; deduplicated alerts; **watch only, never buys/pays** (§20).

## Adapted rather than copied
- SQL-lease recovery → a **SQLite lease + heartbeat** (no Postgres; reuse JARVIS's existing SQLite approach, §45/§47).
- AG-UI SSE transport → an **in-process event bus** with AG-UI-compatible names now; SSE transport is a future
  *client* concern, not something to bolt onto the local Windows engine (§13: "do not make the engine depend on AG-UI").
- Rich cards → a **receipt card payload** pushed to the existing glass HUD in JARVIS's own design (§43: own identity).

## Already present in JARVIS (not duplicated, §47-50)
- **Editable / forget memory + personal context** — `jarvis.memory` already has `remember`, `forget`, `set_preference`,
  `preferences`, `recall`. Nothing new built; §22 satisfied by the existing local-first store.
- **Daily conversation model** (one date = one conversation) — preserved; AG-UI is transport only (§24, §49).
- **Safety / verification / privacy / JEV ₹1,000 budget / RL** — untouched and authoritative; new subsystems call them.
- **Native Windows control + real Chrome/Edge** — preserved; the OpenMuse Linux container and Playwright Chromium were
  **rejected** (§46) as the opposite of JARVIS's differentiator.

## Rejected (with reasons)
| Rejected | Reason |
|---|---|
| Linux-container computer | JARVIS uses native Windows UIA/SendInput — the core differentiator (§46) |
| Playwright Chromium / 2nd browser | JARVIS drives the user's real Chrome/Edge (§26, §46) |
| PGlite/Postgres | SQLite already used; one store (§45) |
| Duplicate memory / conversation / safety / budget | one authoritative each (§47-50) |
| CopilotKit Intelligence service | Claude is the brain |
| Faked mobile app / device push / voice transplant | roadmap in OpenMuse or already superior in JARVIS — not faked (§0, §21, §29) |

## MCP persistence / caching (evaluated, §15, §16)
JARVIS's MCP is **stdio, spawned once per Claude session and reused** — tools are discovered once per session, not per
turn. The CopilotKit "fresh connection per run" problem does not occur here, so a persistent SSE/HTTP client would add
latency and a network surface for no gain. **Decision: keep stdio.** No measured per-turn discovery cost exists to cache
away. Documented rather than changed.

## Mobile / web / remote readiness (§27-30) — CONTRACT ONLY, nothing exposed
The event bus + `snapshot()` is the **read-only contract** a future iPhone/web client would consume; it observes the
same tasks/messages/state without a second brain. **No client is shipped and nothing is exposed.** A remote gateway is
*specified* (permission tiers READ_ONLY/STATUS/APPROVE/TASK_CONTROL/COMPUTER_CONTROL; default STATUS+READ_ONLY; strong
auth, encrypted transport, authorization, replay protection, revocation, audit) but **not built** — the PC is never
bound to a public endpoint, and no remote device inherits Windows control (§27, §28). See `docs/UI_UX_FINAL.md`.

## Deferred (honestly not shipped this pass)
- **Human takeover** (§12): the manager's pause/resume + a re-observe-on-return hook are in place, but the live
  "user manually drives Chrome, then RETURN CONTROL → fresh observation" wiring on the real window is **designed, not
  shipped**. The safe primitive (pause computer actions / re-observe) exists; the UX button + browser-session handoff
  are next.
- **Rich card suite** (email/PDF/finance) and **goals/milestones** — the receipt card ships; the rest is deferred.
- **Full premium UI redesign** (§31-42, §60) — the orb + glass HUD are already premium (prior work) and now carry task/
  queue/receipt state; a ground-up visual redesign with the full card system is a separate dedicated effort tracked in
  `docs/UI_UX_FINAL.md`.
- **User-facing watch creation** ("watch this page") — the watch engine + scheduler run; exposing creation via an MCP
  tool or a voice command is the next integration step.

## Privacy / security / licensing impact
- Local-first preserved; watches read local files or public pages read-only; notifications are local; nothing networked
  by these subsystems. Secrets never stored (memory redaction unchanged).
- **Licensing:** OpenMuse is MIT. **No OpenMuse code or assets were copied** — everything is clean-room Python of the
  *concepts* (§44). `THIRD_PARTY_NOTICES.md` records the reference (repo, MIT, "concepts only, no code vendored").

## Performance
The task/event layer is in-process and event-driven (no polling). Task ops are single SQLite writes (sub-millisecond
locally). The event bus is a list dispatch + bounded ring buffer. No measurable impact on the computer-control latency
(the engine path is unchanged). The UI remains an observer of runtime state (§41).

## Tests / results
`tests/test_tasks.py` (14), `tests/test_watches.py` (7), plus app-level integration in `tests/test_conversation.py`
(task-per-turn + receipt, follow-up queued, failed-turn fails task). **Full suite: 114 passed, 27 skipped (live).**
Failures found & fixed during the build: a watch-test used unrealistic `now` values (< the watch's real `next_check`) —
fixed the test, not the code.

## Remaining limitations
Live human takeover, the full rich-card UI, user-facing watch creation, and any mobile/web/remote client are **not
shipped** and are documented as such — no roadmap feature is presented as implemented (§0, §65).
