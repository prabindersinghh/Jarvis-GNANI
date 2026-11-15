# Using JARVIS

**Start:** double-click `JARVIS` on the Desktop. A glowing **orb** appears at the bottom-right of your primary screen
(above the taskbar) plus a tray icon. **Click the orb** to open the glass panel: type a command, press ● mic for a
hands-free conversation, watch the live activity and the system status strip (Claude · JEV budget · memory · voice …).
Esc or × collapses it back to the orb. Right-click the orb for Listen / Mute / Settings / log window / Hide / Exit.
The orb's animation and caption always reflect what JARVIS is really doing (idle, listening, thinking, working,
searching, responding, speaking, done, error). See docs/ORB_UI.md.

**Talk:** hold `Ctrl+Alt+J`, speak, release. Tap it once to toggle listening (auto-stops on silence).
Emergency stop: `Ctrl+Alt+X` (interrupts Claude and any recording). You can also type in the console.

**What happens:** mic → local Whisper (`models/whisper-base.en`) → Claude Code (your Claude Max login) → JARVIS MCP tools →
Windows. Deterministic actions run directly; UI clicks go through UI Automation; ambiguous choices go to JEV; OCR and
screenshots are fallbacks. Every step is verified; dangerous actions pop a confirmation dialog.

**JEV:** put `TYPESAFE_API_KEY=...` in `D:\JARVIS\data\config\.env`. Spending is capped at **₹1,000 per calendar month**
by a local guard (stops at ₹900, resets on the 1st; shown in the orb as `JEV ₹x / ₹1000`); beyond it JARVIS silently uses
the non-JEV routing paths. It is only consulted for genuinely ambiguous UI targets (≈0.4 s, ~$0.00004 per decision); deterministic and uniquely-named actions never touch it. Without a key JARVIS
still works — ambiguous targets fall back to a local tie-breaker, then to Claude vision.

**Speech:** JARVIS speaks the final reply (local Windows voices). Modes — `silent` (text only), `concise` (one or two
spoken sentences, default), `conversational` (full reply) — from the console dropdown or the tray menu. While JARVIS
speaks, the microphone is muted; pressing the push-to-talk key interrupts it and listens.

**Memory:** JARVIS keeps local experience under `D:\JARVIS\data\memory\` — preferences, workflows that worked,
corrections, failures to avoid, app quirks. Tell it "remember that I keep papers in D:\Research" and it will. Delete a
file there to forget; nothing in it is secret (values are redacted, credential fields never stored).

**Privacy:** `settings.json → "privacy"`: `screenshots` = `allow` | `confirm` (asks before sending pixels to Claude) |
`never`; `redact_cloud` scrubs secrets/PII from everything sent to Claude; `mask_fields` hides password/PIN/OTP/card
values; `blocked_titles` lists windows JARVIS will never observe or capture (password managers, InPrivate, …).
Audio never leaves the machine.

**Settings:** `D:\JARVIS\data\config\settings.json` — `hotkey`, `whisper_model` (`base.en` fast / `small` more accurate),
`mic_device` (index from `python -c "from jarvis.voice.mic import list_input_devices as l; print(l())"`), `brand`,
`claude_model`, `jev_min_confidence`, `act_max_steps`.

**Logs:** `D:\JARVIS\logs\jarvis.log` (app), `computer.log` (engine), `metrics.jsonl` (latencies).

**Security boundaries:** UAC prompts, the lock screen, Windows Hello, CAPTCHA and MFA are never automated — JARVIS tells you
and waits. Deleting, destructive git, firewall/registry/credential changes, payments and publishing require your Yes.
