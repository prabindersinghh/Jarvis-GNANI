# JARVIS — FINAL PRODUCT PRD

## Product directive

Build **JARVIS**, a Windows personal computer agent.

This is a **build specification, not a phased roadmap**.

The user does not want a slow sequence of demo phases. Claude Code is authorized to implement the complete practical product now, using existing open-source foundations wherever they are better than custom code.

The final experience must be:

> Click the JARVIS desktop icon → JARVIS starts → speak naturally → Claude understands the task → JARVIS controls the Windows computer → deterministic actions happen directly → JEV handles fast UI decisions when necessary → difficult visual/semantic cases fall back to Claude → actions are verified.

---

## 1. Core requirements

JARVIS must provide practical manual-equivalent control of Windows:

- keyboard;
- mouse;
- clicks/double-click/right-click;
- drag/hold/release;
- scrolling;
- text entry;
- hotkeys;
- windows;
- applications;
- browser;
- terminal;
- PowerShell/CMD;
- filesystem;
- dialogs;
- screenshots;
- OCR;
- Windows UI Automation;
- multiple monitors;
- DPI-aware interaction.

If a normal human can perform an ordinary task through the Windows desktop, JARVIS should have an equivalent control path whenever the OS/application/security boundary allows it.

Security boundaries such as UAC secure desktop, MFA, CAPTCHA, biometric authentication and lock-screen authentication must not be bypassed.

---

## 2. Intelligence model

### Claude Max / Claude Code

Claude is the **only general-purpose reasoning brain**.

Use it for:
- understanding natural-language goals;
- planning;
- coding;
- debugging;
- complex reasoning;
- interpreting difficult UI states;
- deciding what should happen;
- final explanations.

Do not add GPT, Gemini, another paid LLM, or another general-purpose agent model.

### JEV

JEV is the **fast reflex/decision layer**.

Use it for:
- choosing among UI candidates;
- selecting operation + target;
- repetitive structured GUI decisions;
- low-latency action selection.

Do not use JEV for general reasoning.

### Local AI

Use local AI only where specialized/local processing makes sense, especially:
- Gnani Prisma speech recognition and Timbre speech synthesis;
- VAD;
- OCR;
- deterministic/specialized vision utilities.

---

## 3. Speed is a primary feature

The system must aggressively avoid unnecessary model calls.

Routing priority:

```text
DIRECT LOCAL ACTION
        ↓
WINDOWS UI AUTOMATION
        ↓
JEV
        ↓
LOCAL OCR / STRUCTURED SCREEN
        ↓
CLAUDE VISION / REASONING
        ↓
HUMAN
```

Examples:

`Ctrl+L` → direct keyboard input.

Unique `Download` UIA element → direct UIA invocation.

Five possible controls → JEV chooses target.

Canvas/custom UI with no useful structure → screen/OCR/vision fallback.

Never send a screenshot to Claude for every click.

Never call JEV when a deterministic action is already known.

Never make Claude micromanage obvious mouse movements.

---

## 4. Voice

Voice uses Gnani's hosted speech APIs:

```text
Microphone
→ VAD
→ Gnani Prisma
→ transcript
→ Claude Code
```

Start with push-to-talk and make the architecture ready for a wake word later.

---

## 5. Computer control

JARVIS must combine:

### Windows UI Automation
Use structured UI elements wherever possible.

### Native Windows input
Use Win32/SendInput or equivalent robust native mechanisms for keyboard/mouse fallback.

### Screen
Support:
- full screen;
- monitor-specific;
- window/region capture where practical;
- DPI-aware coordinates.

### OCR
Use when UIA cannot expose useful text.

### Browser
Prefer structured browser/DOM/accessibility mechanisms when available, then UIA, then raw input/vision fallback.

### Windows
Support:
- process discovery;
- launch;
- focus;
- minimize;
- maximize;
- restore;
- move;
- resize;
- close;
- wait.

### Files
Support:
- read;
- write;
- search;
- copy;
- move;
- rename;
- archive/extract;
- delete with confirmation.

### Shell
Support:
- PowerShell;
- CMD;
- process execution;
- stdout/stderr;
- waiting;
- cancellation.

---

## 6. Verification

JARVIS must verify meaningful actions.

Examples:
- launched app → verify process/window;
- clicked button → verify expected UI state;
- typed text → verify value where possible;
- downloaded file → verify file;
- moved file → verify destination;
- git operation → inspect result;
- browser navigation → verify page/window state.

Never treat "input was sent" as proof of success.

---

## 7. Stale UI protection

Every screen/UI observation needs an identity/version/timestamp.

If an action was selected from stale state:

```text
OLD STATE
→ reject stale action
→ re-observe
→ regenerate candidates
→ decide again
```

Never blindly execute coordinates against a changed screen.

---

## 8. Safety

Low-risk operations can run automatically.

Require confirmation for high-impact operations such as:
- destructive deletion;
- destructive repository operations;
- security configuration;
- firewall/registry changes;
- credential/password changes;
- financial actions;
- high-impact publishing/sending.

Never bypass deliberate security controls.

---

## 9. Desktop experience

The final product must create:

```text
%USERPROFILE%\Desktop\JARVIS.lnk
```

The shortcut must:
- display `JARVIS`;
- use a proper icon;
- launch the packaged product;
- work when VS Code is closed;
- use `D:\JARVIS` as the product root.

The user must not need to open a terminal or VS Code to start JARVIS.

A lightweight tray/status component is desirable.

---

## 10. Storage

Primary product root:

```text
D:\JARVIS
```

Source, runtime, models, data, logs, caches, tests and build output should live there.

Do not create the product under C:.

---

## 11. Existing open-source work to use

Inspect and reuse rather than reinvent:

- `https://github.com/danielsimonjr/Windows-MCP`
- `https://github.com/sshh12/windows-computer-use-mcp`
- `https://github.com/awlevin/typesafe-computer-use`
- `https://github.com/browser-use/jev-ultrafast`
- `https://github.com/coff33ninja/go-mcp-computer-use`
- `https://github.com/zavora-ai/computer-use-mcp`
- `https://github.com/wyofalcon/windows-desktop-companion`
- `https://github.com/Einosensokami/windows-computer-use-mcp`

Use the current repository contents and licenses.

The preferred strategy is:

**reuse mature Windows computer-control code + add the JARVIS routing/JEV/voice/verification/product layer.**

Do not blindly combine repositories.

---

## 12. Quality target

This should feel like a real personal computer agent:

- fast;
- capable;
- reliable;
- voice controlled;
- broad computer control;
- Claude-powered;
- JEV-accelerated;
- locally controlled;
- verifiable;
- safe;
- launchable from one desktop icon.

Do not optimize for a flashy demo at the expense of actual computer capability.

---

## 13. Final acceptance

The product is complete when the user can:

1. click JARVIS on the desktop;
2. JARVIS starts independently;
3. speak a command;
4. Gnani Prisma transcribes the audio;
5. Claude understands the goal;
6. JARVIS operates Windows;
7. deterministic actions bypass models;
8. JEV handles structured UI choices;
9. difficult cases fall back to vision/reasoning;
10. actions are verified;
11. errors recover or are clearly reported;
12. dangerous actions request confirmation;
13. the system works without VS Code being open;
14. the product lives on D:.

Do not call the product complete before this works.
