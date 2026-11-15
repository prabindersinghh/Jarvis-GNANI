# JARVIS — COMPLETE CONTEXT

## Identity

JARVIS is the user's personal Windows computer agent.

Working name: **JARVIS**.

The name will likely change later, so keep branding configurable.

The product should feel like a practical, extremely capable Jarvis rather than a chatbot demo.

---

## User's exact goal

The user wants:

> **Full voice-controlled computer operation, with Claude as the intelligence and JEV as a fast reflex/decision layer.**

The user already has Claude Max and does not want to pay for another general-purpose LLM.

The user is willing to spend approximately ₹500–700/month on JEV.

The user wants the product built quickly and directly, not dragged through artificial development phases.

---

## Mental model

```text
Claude = Brain
JEV = Reflexes
MCP = Tool/Nervous System
Windows APIs + UIA = Hands
Whisper = Ears
Screen/OCR = Eyes
JARVIS = Whole system
```

---

## The desired experience

The user should be able to say:

> "Open Chrome, find the latest Qwen paper, download it to my AI research folder, rename it properly, and summarize it."

JARVIS should:
1. hear the request locally;
2. transcribe;
3. give the meaningful goal to Claude;
4. let Claude plan;
5. execute computer operations locally;
6. use direct deterministic actions where possible;
7. use UI Automation when possible;
8. use JEV when a UI choice needs fast semantic selection;
9. use OCR/screen extraction if structured UI is insufficient;
10. use Claude vision/reasoning only when needed;
11. verify downloads/files/navigation;
12. report completion.

---

## Critical speed principle

Do NOT build:

```text
Claude
→ screenshot
→ click
→ screenshot
→ Claude
→ click
→ screenshot
...
```

Build:

```text
Claude
→ meaningful plan
→ local computer engine
→ deterministic/UIA/JEV loop
→ verified result
→ Claude
```

Claude should not micromanage routine clicks.

---

## Full control philosophy

JARVIS should have manual-equivalent primitives:

### Keyboard
All ordinary keys, modifiers, shortcuts, text, clipboard.

### Mouse
Movement, clicks, double-click, right-click, middle click, drag, hold, release, scroll.

### Windows
Launch, focus, move, resize, minimize, maximize, restore, close, multi-monitor.

### Applications
Open and operate ordinary Windows applications.

### Browser
Navigate, search, tabs, forms, downloads, uploads, scrolling, GUI fallback.

### Terminal
PowerShell, CMD, processes, commands, output.

### Files
Read, write, copy, move, rename, search, archive, extract, delete with safety controls.

### Screen
Screenshot, monitor-specific capture, OCR, UI state.

---

## Control hierarchy

```text
1. deterministic
2. UI Automation
3. JEV
4. OCR/structured screen
5. Claude vision/reasoning
6. human
```

This hierarchy is the central technical idea.

---

## Existing open-source foundations

Do not reinvent everything.

Relevant projects:

### Windows-MCP
https://github.com/danielsimonjr/Windows-MCP

Primary candidate Windows foundation.

### windows-computer-use-mcp
https://github.com/sshh12/windows-computer-use-mcp

Raw input, screenshots, DPI, multi-monitor and UIA reference.

### TypeSafe Computer Use
https://github.com/awlevin/typesafe-computer-use

JEV structured decision architecture.

### Jev Ultrafast
https://github.com/browser-use/jev-ultrafast

Structured operation + target selection and fast decision-loop reference.

### go-mcp-computer-use
https://github.com/coff33ninja/go-mcp-computer-use

Broad UIA/OCR/memory/recording reference.

### Zavora Computer Use MCP
https://github.com/zavora-ai/computer-use-mcp

Native/performance architecture reference.

### Windows Desktop Companion
https://github.com/wyofalcon/windows-desktop-companion

Windows + Claude Code companion reference.

### Einosensokami Windows MCP
https://github.com/Einosensokami/windows-computer-use-mcp

Minimal Windows computer-control reference.

Inspect current code and licenses before reuse.

---

## Product location

Everything should be built under:

```text
D:\JARVIS
```

Models, source, runtime, logs, caches, data, tests and artifacts should live there.

The desktop launcher is:

```text
%USERPROFILE%\Desktop\JARVIS.lnk
```

Clicking it must start JARVIS independently of VS Code.

---

## What makes this different from a generic computer-use agent

The target is not maximum model complexity.

The target is:

> **Maximum practical computer capability per unit of latency and Claude usage.**

That means:
- deterministic first;
- UIA first;
- JEV for structured micro-decisions;
- vision only when needed;
- Claude for real intelligence.

---

## Security boundary

JARVIS must never bypass:
- UAC secure desktop;
- lock-screen authentication;
- MFA;
- CAPTCHA;
- biometrics;
- other deliberate security controls.

It should hand control back to the user when necessary.

---

## Product quality

The final system should:
- feel immediate for simple actions;
- handle complex tasks;
- verify actions;
- recover from stale UI;
- expose useful logs;
- have a desktop icon;
- start independently;
- be maintainable;
- be genuinely usable.

The product is not complete because a demo works once.

---

## Development philosophy

The user explicitly does NOT want a boring slow "Phase 1 / Phase 2 / Phase 3" development experience.

Claude Code should work in **build mode**:

```text
inspect → choose → implement → test → fix → package → verify
```

as a continuous execution flow.

Phases can exist internally for dependency ordering, but Claude should not stop after each phase and ask the user to continue.

---

## Final mental picture

```text
                     YOU
                      |
                  "JARVIS..."
                      |
                      v
              LOCAL WHISPER
                      |
                      v
                 CLAUDE CODE
                    BRAIN
                      |
                      v
                 JARVIS MCP
                      |
          +-----------+-----------+
          |           |           |
       DIRECT        UIA        JEV
          |           |           |
          +-----------+-----------+
                      |
                Windows OS
                      |
       +--------------+--------------+
       |              |              |
     Browser         Apps         Terminal
       |              |              |
       +--------------+--------------+
                      |
                 VERIFIED RESULT
```

That is the product.
