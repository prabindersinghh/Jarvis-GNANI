# JARVIS interaction model & design system (2026-09-23)

The final experience contract for JARVIS after the OpenMuse capability transplant. It documents what the UI *is* today
(already premium — see `docs/ORB_UI.md`), how the new task/queue/receipt/notification/event state maps into it, the
design system, the remote-security model, and — honestly — the deferred full redesign.

## The one coherent model
```
collapsed:  a single luminous orb, bottom-right, always rippling (never a rectangle, never disappears)
click:      the orb unfolds into a glass HUD (transformation, not a new window)
HUD shows:  TODAY'S CONVERSATION · CURRENT TASK · FOLLOW-UP QUEUE · LIVE ACTIVITY · RESPONSE · RECEIPT · COMPACT STATUS
state:      the orb's motion/colour is driven ONLY by real runtime events from the event bus (never fake)
voice:      always able to hear you once a conversation starts — barge-in while busy, listen while idle
```

## Orb (primary identity, §32/§33) — SHIPPED (prior work, preserved)
Composition-hosted WebView2 with true per-pixel alpha (`jarvis/ui/comphost.py`), a permanent liquid outer ripple + inner
thinking-orb, state-mapped colour, DPI-aware, topmost, work-area-aware, does not vanish under full-screen, idle
msedgewebview2 CPU ≈ 0 %. States map to real events: IDLE / LISTENING / THINKING / WORKING(acting) / SEARCHING /
LEARNING / SPEAKING / SUCCESS / ERROR. State is communicated through motion, not large text (§33).

## New state, mapped into the existing glass HUD
- **Current task + follow-up queue** — `app._tasks_status()` feeds the status payload: current goal + `N queued`. The
  HUD's status strip and a compact CURRENT / UP-NEXT list render it (§8, §37). Reorder/remove call the manager.
- **Receipt** — on a task that did real computer work, `orb.push(receipt=…)` carries the proof-of-work
  (goal · actions · verified ✓ · duration) for a receipt card (§11, §38); a one-line summary also lands in the transcript.
- **Notifications** — `unread_count()` drives a subtle orb accent; the HUD lists high-value alerts (§21, §39). No
  intrusive desktop popups.
- **Live activity** — the event bus translates `RUN_STARTED → TOOL_CALL_START(…) → … → RUN_FINISHED` into a human
  activity line (Thinking → Searching → Working → Done), not raw tool spam (§36).

## Design system (§42) — the primitives the orb page already uses
Glass layers (translucent surfaces + subtle borders + luminous edge + soft shadow), restrained colour mapped to state,
premium typography with subtle dividers (no chat bubbles, §35), controlled animation durations, high-quality
anti-aliasing. Forbidden and absent: black rectangular backing, generic dashboard panels, gaming RGB, radial menus,
huge status labels, visual clutter (§40). Reusable tokens live in the orb template's CSS variables + the thinking-orbs
engine presets.

## Remote security model (§27, §28) — SPECIFIED, NOT SHIPPED
The event bus is a **read-only in-process contract**; nothing is exposed. A future gateway, if ever built, must sit
**above** JARVIS's existing safety and enforce:
- permission tiers: `READ_ONLY` · `STATUS` · `APPROVE` · `TASK_CONTROL` · `COMPUTER_CONTROL`; a new device defaults to
  `STATUS + READ_ONLY`; higher tiers require explicit authorization.
- strong authentication, encrypted transport, per-session identity, rate limiting, replay protection, audit logs, device
  revocation. It must never weaken UAC / credential / MFA / CAPTCHA boundaries, secret redaction, or the JEV budget.
- the privileged computer-control MCP is never bound to `0.0.0.0`; no unauthenticated public endpoint; no device inherits
  unrestricted Windows control. **None of this is enabled — it is the contract for a future, deliberate step.**

## Mobile / web readiness (§29, §30)
A future React-Native/web client consumes `EventBus.snapshot()` + the AG-UI-shaped event stream to render conversation /
task / state / progress / receipts **read-only**, observing the same tasks rather than duplicating the brain. The
contract exists; no client is shipped.

## Dynamic Island shell (2026-09-24) — SHIPPED
The presentation layer was rebuilt into a **top-center Dynamic-Island** (see docs/ORB_UI.md): a collapsed glass pill
[orb · JARVIS] that morphs (spring, ~340 ms) into a downward-growing glass panel with today's conversation, activity,
and input. The thinking-orb is its living core (membrane always rippling); the state→colour palette drives glow/accents;
system status + controls live **behind a settings gear** (only a health dot shows in the first view). True per-pixel
desktop alpha (verified see-through); DWM acrylic was empirically tested and found incompatible with the per-pixel-alpha
window, so premium simulated translucency is used (§18/§37). Engine, daily conversation, safety/budget/privacy unchanged.

## Remaining UI work (honestly deferred)
Still a dedicated effort, not done this pass: the full **rich-card system** (browser/PDF/email/finance cards) beyond the
receipt payload; an inline **task-timeline/queue** panel and a third "task workspace" expansion level; partial-transcript
in the island during speech; and a from-scratch typographic/spacing audit. Scoped rather than half-built, per §61.
