# JARVIS orb — life + daily conversation (2026-09-21b)

Two additions on top of the composition-hosted glass orb: a permanent living membrane, and a persistent daily
conversation in the HUD. Nothing else (Claude / MCP / JEV / voice / TTS / privacy / verification / budget / launcher)
changed.

## 1. Living orb — permanent liquid-membrane ripple
The whole orb (glow + membrane + inner core) is drawn on ONE canvas — no CSS animations — so the transparent surface
recomposites only when the canvas paints, at a controllable rate.
- **Outer membrane**: a smooth closed liquid curve (quadratic midpoints through 72 points) whose radius is a sum of
  low-harmonic travelling sines + a breathing term: `r(θ) = BASE·breath + Σ ampₖ·sin(kθ ∓ ωₖt + flow·θ)`. Waves travel
  around and slightly through the circumference; local crests get brighter/thicker (per-segment highlight passes); a
  faint inner echo ring adds depth; a specular highlight sells the bubble. It looks like a thin elastic water membrane,
  never a spinner or a plain pulsing circle.
- **Inner core**: the thinking-orbs engine, drawn separately inside the membrane (its own animation).
- **Glow**: a radial gradient that breathes with the membrane's mean expansion; follows the state colour.
- **Always on**: the ripple never stops — it animates even when JARVIS is completely idle (the old calm-freeze is gone).
  State only changes its *character* (energy/amplitude/colour/speed), never turns it off: idle = slow calm membrane,
  listening = livelier cyan, thinking = tighter violet flow, working = stronger directional movement, searching =
  spectrum drift, responding/speaking = richer glow loosely pulsing with speech (`speakEnv`), success = brief outward
  wave, error = restrained warm ripple; after a temporary state it eases back to idle (all values are eased, not snapped).
- **Cost**: the loop runs 60 fps when active/busy, ~16 fps at idle. Measured idle (ripple on) in an isolated host:
  **msedgewebview2 = 0.0 %** of one core; the app's residual idle is the Claude CLI brain, not the orb. Reduced-motion
  draws one static frame.

## 2. JARVIS is always there
The orb is never hidden for full-screen apps, focus changes, or inactivity. The fullscreen-hide watchdog was removed;
the watchdog now only re-anchors on work-area/DPI changes and periodically reasserts `HWND_TOPMOST` (without activating)
so an app going full-screen can't push the orb below it. **Limitation (documented, not a hide by us):** a true
*exclusive* fullscreen DirectX game takes the whole GPU scan-out and the DWM cannot composite any window over it — that
is a Windows compositor constraint; switching such a game to borderless/windowed keeps the orb visible. Verified: a
full-screen Tk window does NOT hide the orb (live check + `tests/test_live_orb.py::test_orb_stays_visible_under_fullscreen_app`).

## 3. Daily continuous conversation
`jarvis/history.py` — SQLite at `data/memory/conversations/history.db` (`days`, `sessions`, `messages`).
- **One calendar date = one conversation container.** Restarting JARVIS, reconnecting Claude, closing/reopening the HUD,
  a new voice/text turn, or a new task never creates a new conversation — they all append to today.
- **Sessions are markers inside the day**, not separate threads: a session opens on the first interaction after idle /
  on restart, and closes on graceful shutdown, explicit end, or after 20 min idle. A force-kill leaves it open and the
  next start reuses it (a graceful Exit closes it and the next start adds a new marker) — either way the date stays one
  conversation.
- **Midnight rollover** is automatic (the date is computed per append; crossing midnight closes yesterday's session and
  opens a new day). Past days are kept.
- **Voice and text share the same day** (each message stores its `source`).
- **Redaction**: every stored message passes through `privacy.redact_text`; secrets/credential values are never stored.
  The full day is NEVER sent to Claude here — Claude context stays governed by the existing memory/privacy policy; the
  DB is purely the local UI history.
- **Claude continuity**: `claude_session.py` now keys the resumed session id by date, so a same-day restart continues the
  same Claude thread; a new day starts fresh.

## 4. HUD ↔ orb
- **Collapsed**: only the living orb — no transcript, no panel on the desktop.
- **Click the orb → the glass HUD unfolds** and shows **today's** conversation (session dividers + YOU/JARVIS messages
  with compact HH:MM times), most-recent at the bottom, scrollable up; live messages append as they arrive. A subtle
  `‹ Today ›` control pages back through previous dates (a new turn snaps back to today). Closing hides the transcript
  but the conversation persists and the orb keeps rippling.
- **Live turn, no duplication**: the user line is written immediately; the assistant reply streams into a *pending*
  bubble; on completion the app commits it ONCE (finalises the same bubble and writes one DB row) — it is not added a
  second time when spoken.

## Tests
`tests/test_history.py` (6): one-date-across-restarts, idle→new marker, midnight rollover, voice+text share the day,
redaction, ordering. Isolated visual/perf check (no mouse/focus stolen): idle ripple animates across frames, membrane
smooth in all 5 states, transcript renders with 3 session dividers, streamed+committed reply appears exactly once, idle
webview2 CPU 0 %. Live app: startup opens a history session in the DB, orb stays visible under a full-screen app, restart
keeps one date. 51 unit tests green.

---

# JARVIS orb UI — redesign 2026-09-21 (composition-hosted glass)

**The earlier pywebview / shaped-window orb was rejected at visual QA** (opaque disc, clipped radial menu, big "IDLE"
label). It was replaced by a composition-hosted WebView2 orb with true per-pixel transparency and a premium glass HUD.

## Why the host changed
A WinForms-hosted WebView2 (pywebview) renders into an opaque redirection surface — no colour key, DWM frame
extension, layered-window key, or pre-show trick gave real alpha (all four were tested). The fix is WebView2's
**composition controller**: it renders into a DirectComposition visual, and on a window created with
`WS_EX_NOREDIRECTIONBITMAP` the DWM composites those visuals onto the desktop with real alpha. `jarvis/ui/comphost.py`
builds this in pure Python over SIGNED components (user32 / d3d11 / dcomp via ctypes; `Microsoft.Web.WebView2.Core.dll`
shipped with pywebview, loaded through pythonnet) - important because Smart App Control blocks freshly-compiled
unsigned binaries, so a custom C#/WinUI host would likely be blocked. The host provides its own message loop,
mouse/focus forwarding (`SendMouseInput`/`MoveFocus`), DPI (`RasterizationScale`) and a web-message bridge.

## Result (verified on the real desktop; screenshots taken and reviewed)
- **No rectangular backing** - the window is transparent; only what the page paints (orb glow, glass HUD, menu) shows.
  The orb floats over the desktop with a luminous outer glow and a dark translucent core.
- **The core** is the thinking-orbs engine (MODE_FRAMES geometry) painted with JARVIS's own colour painter
  (depth to brightness+size, state palette to hue, slow drift; `searching` flows through the spectrum) - it reads as
  JARVIS's brain, not a package demo.
- **Left click** to the glass HUD materialising from the orb's corner (scale+translate from 100% 100%, ~320 ms); the
  window does not resize (same rect in both modes - pure motion), the orb keeps its screen position, and the input
  takes focus. Esc / x collapses it back into the orb and returns focus to the previous window.
- **Glass HUD**: legible frosted dark glass (luminous top edge, layered internal gradients tinted by state, soft
  shadow), compact - header (JARVIS, state), a one-line activity, the response, input + mic, and a small status strip.
  Genuine desktop glass shows subtly at the edges. No large dashboard.
- **Context menu**: a compact softly-rounded translucent glass rectangle beside the orb (Open / Listen / Mute /
  Settings / Hide / Exit), keyboard-navigable. The radial menu is gone.
- **No "IDLE" label inside the orb** - state is communicated by colour/animation; a small pill appears OUTSIDE the orb
  only while collapsed-and-busy or listening.
- **State to colour** (real runtime events only): idle cool-blue breathing, listening cyan wave, thinking violet
  solving, working blue/violet orbits, searching spectrum globe, responding/speaking brighter composing (the glow
  pulses while TTS actually speaks), success green accent, error restrained orange. The HUD tint, brand dot, input
  focus ring and glow all follow the state colour.

## Click-through and desktop respect
The window is larger than the orb (room for glow + HUD). The page reports its solid regions ("hit" rects in CSS px);
Python scales them by the AUTHORITATIVE RasterizationScale (not the page's `devicePixelRatio`, which can lag a frame
after load and caused cold-click misses) and answers `WM_NCHITTEST` HTCLIENT inside them, HTTRANSPARENT everywhere
else - so the desktop around the orb stays fully clickable. Collapsed: `WS_EX_TOPMOST | WS_EX_TOOLWINDOW |
WS_EX_NOACTIVATE` (no focus steal, no taskbar button). Bottom-right of the primary work area, DPI-aware, re-anchored on
work-area/DPI change, hidden under full-screen apps (as before).

## Idle cost - the orb is free at rest
A per-pixel-alpha DWM layer is only cheap when static. The orb enters CALM after ~2.5 s idle-and-collapsed (driven by
the rAF loop, since `setTimeout` is throttled in a composition window): the core canvas freezes and CSS animations
pause, so the transparent surface stops recompositing. Measured with calm engaged: msedgewebview2 = 0% of one core at
rest (per-process breakdown); the app's residual idle CPU is the Claude Code CLI brain subprocess (~5%, always
present) plus the mic stream. Hover, any state change, or activation wakes it instantly. Reduced-motion renders one
static frame. Active/busy runs at 60 fps.

## Tests (real running app)
`tests/test_live_orb.py` (7): topmost / tool-window / NOREDIRECTIONBITMAP; anchored inside the work-area bottom-right;
the `WM_NCHITTEST` predicate makes empty area click-through; click activates + focuses, Esc collapses, the window never
resizes; does not steal focus while collapsed; the context menu renders as a rectangular glass panel; a full-screen app
hides then restores the orb. Cold-click activation measured 5/5 after the RasterizationScale fix. `tests/test_budget.py`
(5) unchanged - the JEV Rs 1,000 ceiling is intact. 45 unit + 16 live computer-control tests green; packaged launch via
the desktop shortcut unchanged (orb visible ~5-6 s after click).

## Files
`jarvis/ui/comphost.py` (composition host), `jarvis/ui/orb.py` (anchoring / activation / hit-testing / watchdog /
bridge), `jarvis/ui/web/orb.template.html` -> built `orb.html` (design + engine + state machine), `jarvis/ui/build.py`
(inlines the thinking-orbs engine). pywebview is retained only as the source of the signed WebView2 assemblies.

---

# JARVIS orb UI (2026-09-21)

## Architecture change
The primary UI is now a **persistent desktop orb**: a frameless, always-on-top, shaped WebView2 window hosted by
`pywebview` (no Electron; WebView2 runtime is part of Windows 11). The former tkinter console is demoted to an optional
log window ("Show log window" in the orb menu / tray) and runs on demand in its own thread. Everything behind the UI —
Claude session, MCP engine, router tiers, JEV adapter, memory, safety, verification, voice, launcher — is unchanged.

```
jarvis/ui/orb.py                 native window: anchoring, DPI, styles, shaping, watchdog, JS bridge, confirm cards
jarvis/ui/web/orb.template.html  the page: thinking-orbs engine + colour painter + glass HUD + menu + confirm
jarvis/ui/build.py               inlines third_party/thinking-orbs/package/dist/engine.es.js → web/orb.html
jarvis/jev/budget.py             ₹1,000/month hard ceiling (see below)
jarvis/orchestrator/hooks.py     inactive RL decision-hook point
jarvis/app/main.py               runtime state → orb state mapping; status strip; menu actions; confirm routing
```

## thinking-orbs integration
`thinking-orbs@0.3.1` (MIT, Jakub Antalik) is vendored under `third_party/thinking-orbs`. Its React component needs
React, but the package deliberately exposes a framework-free **engine** (`MODE_FRAMES`, `resolvePreset`, painters)
whose frames are "a complete set of draw instructions … a port draws the list". JARVIS uses that engine verbatim
(inlined at build time; only the ES `export` is rewritten to a `window.ThinkingOrbsEngine` assignment) and paints the
frames with its own colour painter (depth → brightness, state palette → hue). The clock/speed semantics match the
wrapper (`t = now/1000 × preset.speed × state multiplier`). `prefers-reduced-motion` renders one static frame.

## Orb window behaviour
- Anchored bottom-right of the **primary monitor's work area** (taskbar excluded), in physical pixels, sizes scaled by the
  monitor's DPI (96 CSS px circle → 144 px at 150 %). Re-anchored by a 1.5 s watchdog when the work area/DPI changes.
- `WS_EX_TOPMOST | WS_EX_TOOLWINDOW` (no taskbar button) and `WS_EX_NOACTIVATE` while collapsed → clicks work, keyboard
  focus is never stolen. On click the window grows up/left (420×460 CSS px) while the orb keeps its screen position; NOACTIVATE
  is dropped and the window is brought to front so the input takes focus; Esc/× collapses and restores NOACTIVATE.
- The window is **shaped** (`SetWindowRgn`): a circle when collapsed, rounded panel ∪ circle when active. Per-pixel alpha is not
  available through the WinForms-hosted WebView2 control (tested: DWM frame extension, colour key, layered window, pre-show
  key — all opaque), so the glass is an opaque dark surface with luminous borders rather than a see-through blur.
- Hidden while a full-screen application owns the primary monitor; restored afterwards. "Hide orb" (menu/tray) keeps JARVIS running.
- Idle render loop throttles to 20 fps when collapsed and idle, 60 fps when active or busy; paused when hidden.

## State mapping (real runtime events only)
| runtime | orb state (thinking-orbs mode) | palette |
|---|---|---|
| starting / connecting | connecting | blue → violet |
| idle | breathing (slow) | neutral cool |
| listening (mic open) | listening | cyan → blue, caption "● LISTENING" |
| transcribing / thinking | solving | violet |
| tool use (working) | working | blue → violet |
| tool use that searches/reads (search, fetch, find, ocr, screen, grep, glob) | searching | slow spectrum drift |
| streaming reply | composing | brighter multi-tone |
| TTS speaking | composing | brighter multi-tone (only while the synthesizer is actually speaking) |
| turn finished | success accent 1.4 s | green |
| error | breathing (slow) | restrained orange/red, 3 s |
The caption text (not just colour) names the state; the panel header repeats it; canvas `aria-label` follows.

## Glass panel (active)
JARVIS · state · activity line (last tool) · streamed response · input + ● mic · stop · status strip:
`CLAUDE · JEV ₹x / ₹1000 · MEMORY · VOICE · COMPUTER · OCR · SAFETY · VERIFY` — computed locally every 5 s from runtime
health (session connected, key/budget, model loaded, …); no model/API calls. Safety confirmations render as a glass card
in the panel (fallback: native message box). Right-click menu: Open, Listen, Mute/Unmute voice, Settings…, Show log window,
Hide orb, Exit. Keyboard: Tab/Enter/Space on the orb, arrows in the menu, Esc everywhere.

## JEV hard spending ceiling — ₹1,000 / calendar month
TypeSafe has no balance/billing endpoint (docs checked 2026-09-21; only per-response `usage.input_tokens`), so the guard is
local and conservative (`jarvis/jev/budget.py`, ledger `data/jev_budget.json`):
- price $0.042 / Mtok input (output free) × a deliberately high FX of ₹95/USD (settings `jev_usd_per_mtok`, `jev_usd_inr`);
- enforced line = ceiling − 10 % reserve (₹900); every call is pre-checked with an over-estimate (chars/3 + 200 tokens)
  and **rejected before sending** if it would cross the line; actual `usage.input_tokens` is recorded after each call;
- fail closed on any accounting error; persisted across restarts; resets automatically on the 1st of the next month;
- wraps `_post` in the adapter, so *every* outbound call passes through it; a rejection surfaces as `JevUnavailable`, and the
  router falls through to heuristic → OCR → `needs_vision` exactly as with no key. Live-tested: with the ledger at the line,
  an ambiguous click made **0 HTTP calls** and returned `needs_vision` with the budget reason; `computer_status` and the orb
  show `₹x / ₹1000 · limit reached`. No auto-refill exists anywhere.

## Packaging
`Desktop\JARVIS.lnk` → `D:\JARVIS\.venv\Scripts\pythonw.exe -m jarvis.app` (cwd `D:\JARVIS`, orb icon). This changed
from `dist\JARVIS.exe`: Windows **Smart App Control blocked the freshly rebuilt unsigned exe** ("Application Control
policy has blocked this file"), whereas the venv's `pythonw.exe` is Authenticode-signed by the Python Software Foundation.
`dist\JARVIS.exe` is kept and `launcher\make_shortcut.ps1 -UseExe` targets it on machines without Smart App Control.
`launcher\install.ps1` now also runs `python -m jarvis.ui.build`. No VS Code, Claude Code UI, or terminal is involved.

## Tests performed (all on the real desktop / packaged app)
- `tests/test_budget.py` (5): ceiling, reserve, pre-check rejection, actual-usage recording, persistence, month rollover,
  adapter sends nothing when exhausted. `tests/test_unit.py` + `test_upgrade.py` unchanged (39 unit total).
- `tests/test_live_orb.py` (6, against the running app): exists bottom-right inside the work area, topmost, tool-window,
  no-activate, circular; multi-monitor/DPI anchor math (2nd monitor left, 100 %/200 %); click → activates in ~120–170 ms,
  keeps anchor, takes foreground, Esc collapses & restores no-activate; survives app switching (Notepad launched: orb visible,
  nothing non-topmost above it, centre pixel not Notepad's); full-screen app → orb hidden within 4 s, restored after;
  right-click menu renders and closes.
- Manual/instrumented: typed command through the HUD → streamed reply + spoken; state screenshots (idle/connecting/listening/
  solving/responding); PTT tap (toggle) and hold; budget fallback live; shortcut launch timings; `tests/test_live.py` (16)
  computer-control regression green.

## Measured performance (this machine, 150 % DPI)
- Startup via shortcut: orb visible **5.6 s**, Whisper 6.7 s, Claude connected 16.4 s (all background; orb usable immediately).
- Activation click → panel: 120–170 ms. Idle CPU (orb collapsed, JARVIS idle): **0.2 % of one core** total across
  `pythonw` + WebView2 processes. Memory: ~105 MB `pythonw` (Whisper loaded) + WebView2 renderer/GPU processes ~300 MB
  (shared Edge runtime).

## Fixed along the way
- PTT release could be lost if it arrived before the recorder finished opening the mic (first open ≈ 0.8 s): the recorder is
  now armed synchronously in the hotkey callback, the stream opens inside `record()`, and the mic is pre-warmed at start.
- pywebview introspects the whole `js_api` object graph → API object holds only private references.

## Limitations
- True see-through glass (backdrop blur of the desktop) is not possible with the WinForms-hosted WebView2; the panel is an
  opaque dark glass. A composition-hosted WebView2 (C#/C++) would be needed for per-pixel alpha.
- Only one physical monitor was available; multi-monitor is covered by anchor math + the work-area watchdog, not a live test.
- The unsigned `dist\JARVIS.exe` is unreliable under Smart App Control; the signed `pythonw.exe` target is the default.
- RL is not implemented; `orchestrator/hooks.py` is the inactive integration point (consulted only for ambiguous targets).

## Dynamic Island redesign 2026-09-24 (top-center native shell)
The bottom-right orb was relocated and rebuilt into a **top-center Dynamic-Island** shell — ONE object that
transforms, not a new window (directive: NATIVE WINDOWS DYNAMIC-ISLAND UI).
- **Collapsed:** a compact top-center glass pill = the thinking-orb (its living core, with the always-on liquid
  membrane) + the JARVIS wordmark. No rectangle; the desktop around it stays clickable (WM_NCHITTEST hit-rects on the
  island only).
- **Activation:** the pill morphs (spring easing, ~340 ms) into a glass panel that grows DOWNWARD — the bar (orb +
  wordmark + header controls) stays at top, the conversation/activity/input materialise below. `jarvis/ui/orb.py`
  `anchor_rect` now returns top-center; `WIN=(720,620)` gives room for the downward expansion; one window rect in both
  modes.
- **Status behind the gear (user requirement):** the first expanded view shows only a small at-a-glance **health dot**;
  the full system status (Claude · JEV ₹x/₹1000 · Memory · Voice · Verify · Tasks) + controls (Mute/Settings file/Hide/
  Exit) live behind the ⚙ settings button, not in the foremost visual.
- **Core preserved:** the thinking-orbs engine (v0.3.1, MIT) still renders the orb (`ThinkingOrbsEngine` presets); the
  liquid membrane + glow + inner core are one canvas, ripple always on; the state→colour palette drives glow/accents.
- **Native glass (§18 investigation, empirically tested):** the shell is composition-hosted WebView2 on a
  WS_EX_NOREDIRECTIONBITMAP window → **true per-pixel desktop alpha** (verified see-through: apps behind show through the
  island). A native DWM system backdrop (Acrylic/Mica via `DwmSetWindowAttribute` DWMWA_SYSTEMBACKDROP_TYPE) was tried:
  the call returns S_OK but renders **no blur** on a NOREDIRECTIONBITMAP window (no redirection surface for DWM). A full
  WinUI 3/WinAppSDK rewrite to get DesktopAcrylic would mean a new native binary — blocked by Smart App Control unless
  signed — so per-pixel-alpha translucency (which keeps the floating orb glow with no rectangle) is kept as the
  higher-value choice. `comphost.apply_backdrop()` remains as the tested hook.
- **Unchanged:** the engine (Claude/JEV/RL/memory/tasks/Windows control/verify/safety/privacy/budget), the daily
  conversation model, hit-test click-through, activation focus handling, the push()/web-message bridge, DPI/multi-monitor
  anchoring, and the "never hide for full-screen apps" rule. 118 non-live tests green.
