# JARVIS — project instructions for Claude Code

JARVIS is the user's personal Windows computer agent. **Claude Code is the only brain**; the local engine under
`jarvis/` is the hands; JEV (TypeSafe) is the reflex layer; Gnani Prisma/Timbre provide speech input/output. Product root is `D:\JARVIS`.

## Operating rules (when driving the computer through the `jarvis` MCP)

Take the cheapest control tier that works, in this order:

1. **Deterministic** — `computer_app` (launch/kill/list), `computer_key` (shortcuts), `computer_shell`, `computer_file`,
   `computer_window` (focus/move/resize/close/wait). No models involved. Use these whenever the action is known.
2. **UI Automation** — `computer_observe` → `computer_click` / `computer_type` by element **id** or **name**;
   `computer_element` for invoke/select/toggle/expand/set_value/text/wait. A unique name clicks directly.
   Ambiguous names: pass `goal=` and JEV picks; if JEV is unavailable a local tie-breaker or `needs_vision` results.
3. **`computer_run`** — batch predictable steps (same vocabulary as the tools) with `expect` checkpoints in ONE call;
   stops at the first failure with the trace + a fresh observation. A typical task is 1–3 batches, not 15 calls.
4. **`computer_act`** — hand over a concrete multi-step *visible* UI goal; the JEV loop runs locally
   (observe → decide → execute → verify) without consulting you per click. Supply any text via `text_values`.
5. **OCR** — `computer_screen action='ocr'` when the app exposes no UIA structure; its line bounds are click targets.
6. **Vision** — `computer_screen` screenshot only when pixels are genuinely needed. Never screenshot-per-click.
   (privacy setting may require confirmation or forbid it).

- **Memory**: a `<memory>` block may precede requests (preferences, workflows, corrections, app patterns, lessons).
  Store user preferences/corrections and app quirks with `computer_memory(remember)`; never secrets.
- **Voice UX**: the final message is spoken (concise mode = 1–2 plain sentences, no markdown).

- Waits: `computer_wait` (window / window_gone / process / element / text / file / title / browser). Never sleep.
- Every tool returns `verification`; treat `verified: false` as failure and recover. `error: stale_state` → re-observe.
  `needs_vision` → one screenshot or a better target. `blocked` / `human_required` → tell the user (UAC/login/CAPTCHA/MFA).
- Dangerous actions (delete, destructive git, security/registry/firewall, credentials, payments, publish/send) trigger a
  human confirmation dialog automatically. Never work around a denial.
- Typing uses paste-first with read-back verification; secrets are never logged. Do not read `data/config/.env`.
- Reply briefly (the user is on voice), then act; report what was verified.

## Layout

```
jarvis/computer/engine   vendored Windows engine (sshh12/windows-computer-use-mcp, MIT) — DPI, capture, SendInput, UIA, OCR
jarvis/computer/uia.py   element model (id/role/name/automation_id/enabled/bounds/value/patterns) + pattern actions
jarvis/computer/server.py  the 18-tool MCP surface (`python -m jarvis.computer`, stdio) incl. computer_run / computer_memory
jarvis/orchestrator      router (tiers), verify, waits, act loop, batch (computer_run), metrics
jarvis/memory            persistent local experience memory (data/memory/*.jsonl, preferences.json)
jarvis/history.py         daily conversation store (data/memory/conversations/history.db) — one date = one conversation
jarvis/state             observation store: capture_id + fingerprint → stale-state protection
jarvis/jev               TypeSafe /v1/systemone adapter (timeout/retry/cancel/metrics; degrades gracefully without a key)
jarvis/safety            policy (safe/confirm/blocked) + confirmation dialog + secret redaction + privacy controls
jarvis/voice             mic (push-to-talk, VAD, suppression) → Gnani Prisma; Gnani Timbre TTS/playback; endpoint.py
                         (adaptive end-of-turn), bargein.py (voice interrupt), ack.py (instant "On it."), StreamingSpeaker
jarvis/app               tray + Claude session (claude-agent-sdk over the user's Claude Code login) + optional log console
jarvis/ui                desktop orb — composition-hosted WebView2 (comphost.py, true transparency) + thinking-orbs engine; state mirrors runtime
jarvis/jev/budget.py     ₹1,000/month hard JEV ceiling (local, fail-closed) — never bypass
jarvis/learning          perpetual learning: contextual-bandit policy over the router hook (data/policies, data/memory/experiences); learns JEV down, promote/rollback
jarvis/graph             local private memory graph of JARVIS's own experience (data/graph); recall + graphify export; codebase graph via graphifyy
jarvis/tasks             durable task engine + follow-up queue + receipts + AG-UI-compatible event bus + notifications + watches (data/tasks); adapter over the brain, one authoritative engine
launcher/ dist/          Desktop JARVIS.lnk → .venv pythonw -m jarvis.app (signed; Smart App Control blocks unsigned exe);   tests/ benchmarks/ docs/ models/ data/ logs/ third_party/
```

## Dev commands

```powershell
.venv\Scripts\python.exe -m pytest tests -q                 # unit + fast integration (Notepad/Calculator fixtures)
.venv\Scripts\python.exe -m pytest tests -q -m live          # live GUI tests (moves the mouse / opens apps)
.venv\Scripts\python.exe -m benchmarks.run [--jev] [--claude]  # layer latency benchmark → benchmarks/results.md
.venv\Scripts\python.exe -m benchmarks.tasks --tag <name>     # Claude-driven scenario benchmark (turns/tools/tokens)
.venv\Scripts\python.exe -m jarvis.computer                  # run the MCP server on stdio
.venv\Scripts\pythonw.exe -m jarvis.app                      # run the product (or click Desktop\JARVIS.lnk)
.venv\Scripts\python.exe -m jarvis.ui.build                    # rebuild the orb page after editing jarvis/ui/web/orb.template.html
.venv\Scripts\python.exe -m pytest tests/test_live_orb.py -q -m live   # orb tests (app must be running)
.venv\Scripts\python.exe -m benchmarks.latency.bench --label before|after [--claude]  # voice/exec latency (offline, no mouse/audio)
.venv\Scripts\python.exe -m jarvis.graph stats|recall <term>|export     # JARVIS memory graph (recall)
.venv\Scripts\graphify.exe update jarvis --no-cluster                    # (re)build the codebase knowledge graph (local, no LLM)
powershell -ExecutionPolicy Bypass -File launcher\install.ps1  # rebuild venv/models/exe/shortcut
```

Secrets: `data/config/.env` (`TYPESAFE_API_KEY=...`) — never commit, never read into context. Settings: `data/config/settings.json`.
