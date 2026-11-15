# JARVIS — MASTER BUILD DIRECTIVE FOR CLAUDE CODE

## YOU ARE NOW IN BUILD MODE

You are the lead engineer building **JARVIS**, the user's personal Windows computer agent.

The user has already opened Claude Code in VS Code.

**Do not just plan. Do not write a tutorial. Do not stop after a toy demo. BUILD THE PRODUCT.**

The user wants the final product quickly.

The product should be simple to use even though the internal engineering is sophisticated:

> **Click JARVIS → speak → Claude understands → JARVIS controls Windows → JEV accelerates UI decisions → difficult cases use Claude → result is verified.**

Read these files completely before acting:

```text
PRD.md
TRD.md
CONTEXT.md
MASTER_BUILD_PROMPT.md
```

---

# 1. NON-NEGOTIABLE PRODUCT REQUIREMENTS

## Brain

Use the user's existing **Claude Max / Claude Code** as the only general-purpose reasoning system.

Do NOT add:
- OpenAI;
- GPT;
- Gemini;
- another paid general LLM;
- another autonomous reasoning model.

## Reflex

Use **JEV** as the fast structured decision layer.

JEV is not the brain.

## Voice

Use **local Whisper** for speech-to-text.

## Computer

JARVIS must aim for manual-equivalent Windows control:
- keyboard;
- mouse;
- windows;
- apps;
- browser;
- terminal;
- files;
- screenshots;
- UIA;
- OCR;
- multi-monitor;
- DPI;
- raw input.

## Location

The product lives on:

```text
D:\JARVIS
```

## Launcher

Create:

```text
%USERPROFILE%\Desktop\JARVIS.lnk
```

Clicking it must start the packaged JARVIS product without VS Code.

---

# 2. IMPORTANT: DO NOT BUILD FROM SCRATCH BLINDLY

Inspect these repositories first:

```text
https://github.com/danielsimonjr/Windows-MCP
https://github.com/sshh12/windows-computer-use-mcp
https://github.com/awlevin/typesafe-computer-use
https://github.com/browser-use/jev-ultrafast
https://github.com/coff33ninja/go-mcp-computer-use
https://github.com/zavora-ai/computer-use-mcp
https://github.com/wyofalcon/windows-desktop-companion
https://github.com/Einosensokami/windows-computer-use-mcp
```

Use current repository contents.

Inspect:
- README;
- source;
- LICENSE;
- NOTICE;
- dependencies;
- build instructions;
- tests;
- recent commits;
- Windows compatibility;
- Claude Code compatibility.

The current `Windows-MCP` repository is a strong candidate for the Windows/MCP foundation.

The `windows-computer-use-mcp` repository is a strong low-level control reference.

The JEV projects are primarily architecture references for the fast structured decision loop.

---

# 3. MAKE ONE FOUNDATION DECISION

Create:

```text
docs/FOUNDATION_DECISION.md
```

Keep it concise.

Document:
- candidates inspected;
- selected foundation;
- why;
- license;
- what will be reused;
- what will be implemented ourselves.

Then BUILD.

Do not spend hours writing reports.

---

# 4. REUSE MATURE COMPUTER CONTROL

Do not reinvent:
- Windows UIA;
- mouse injection;
- keyboard injection;
- screenshots;
- DPI;
- multi-monitor;
- process/window discovery;
- basic MCP transport;
- basic PowerShell/file tooling

if a suitable open-source implementation already provides them.

Compose/fork/depend on mature code when legally appropriate.

Before copying code:
- inspect license;
- preserve attribution;
- record source commit;
- update `THIRD_PARTY_NOTICES.md`.

Do not blindly copy GPL/AGPL/etc. code into an incompatible project.

---

# 5. THE CENTRAL SPEED ARCHITECTURE

Implement exactly this conceptual hierarchy:

```text
                    REQUEST
                       |
                       v
                 CLAUDE BRAIN
                       |
                       v
                 JARVIS ROUTER
                       |
        +--------------+--------------+
        |              |              |
        v              v              v
   DETERMINISTIC      UIA            JEV
        |              |              |
        |              |              |
        +--------------+--------------+
                       |
                 if unresolved
                       |
                       v
               OCR / SCREEN
                       |
                 if unresolved
                       |
                       v
              CLAUDE VISION
                       |
                 if blocked
                       |
                       v
                    HUMAN
```

---

# 6. DETERMINISTIC PATH

If the operation is known, execute directly.

Examples:

```text
Ctrl+L
Ctrl+C
Ctrl+V
Enter
Escape
Tab
type known string
launch known executable
PowerShell command
copy file
move file
rename file
window resize
window focus
```

No Jev.

No Claude.

This must be extremely fast.

---

# 7. UI AUTOMATION PATH

Use Windows UI Automation as the primary GUI mechanism.

Support:
- tree/state;
- element lookup;
- name;
- role/control type;
- automation ID;
- enabled;
- visible;
- bounds;
- text/value;
- invoke;
- select;
- expand/collapse;
- wait.

If the target is uniquely identified, execute directly.

Example:

```text
"Click Download"

UIA finds one visible enabled button:
Download

→ click directly
```

No Jev required.

---

# 8. JEV PATH

If multiple UI candidates exist, construct a compact candidate set.

Example:

```text
Goal:
"Open Downloads"

Candidates:
1. Documents
2. Downloads
3. Desktop
4. Pictures
```

Ask JEV to select:

```text
operation + target
```

Use the current real JEV API/schema after inspecting its documentation.

Do not assume an API that has not been verified.

Implement:
- timeout;
- cancellation;
- retry;
- error handling;
- confidence where available;
- request metrics;
- minimal payloads.

---

# 9. SCREEN/OCR PATH

When UIA is insufficient:
- capture relevant screen;
- OCR relevant text;
- identify structured candidates;
- use JEV if candidate selection is appropriate.

Do not capture/send the entire desktop unnecessarily.

Use the smallest useful region/state.

---

# 10. CLAUDE VISION PATH

Only use Claude vision/reasoning when:
- UIA is insufficient;
- OCR is insufficient;
- deterministic control is insufficient;
- the task genuinely requires visual reasoning.

Do NOT create a screenshot → Claude → click loop as the default.

That defeats the entire purpose of JEV.

---

# 11. FULL MANUAL INPUT

Implement reliable low-level fallback:

### Mouse
- move;
- left;
- right;
- middle;
- double;
- drag;
- hold;
- release;
- scroll.

### Keyboard
- key down;
- key up;
- shortcuts;
- modifiers;
- arbitrary normal keys;
- text;
- clipboard/paste.

Use native Windows input mechanisms such as `SendInput` where appropriate.

---

# 12. MULTI-MONITOR / DPI

Treat this as required.

Implement:
- monitor discovery;
- monitor-relative screenshots;
- DPI-aware coordinates;
- correct coordinate transformation;
- correct input mapping.

Never assume a single 100%-scaled monitor.

---

# 13. STALE STATE PROTECTION

This is mandatory.

Every observation must include enough state identity to detect change.

Example:

```text
capture_id
timestamp
active window
state hash/version
```

If JEV chooses target 7 from state A but state B is now displayed:

```text
DO NOT CLICK.
```

Instead:

```text
re-observe
→ regenerate candidates
→ re-decide
→ execute
```

---

# 14. VERIFICATION

Every meaningful operation must be verified.

Examples:

```text
launch app
→ verify process/window

click Download
→ verify resulting state

download file
→ verify file

move file
→ verify destination

git command
→ inspect output/status

browser navigation
→ verify page/title/URL/state

type text
→ verify value where possible
```

The product should know when something failed.

---

# 15. WAITING

Do not use arbitrary long sleeps as normal synchronization.

Bad:

```text
sleep(5)
```

Good:

```text
wait for window
wait for process
wait for UI element
wait for file
wait for browser state
wait for expected text
```

All waits must have bounded timeouts.

---

# 16. VOICE

Build:

```text
Microphone
→ VAD
→ local Whisper
→ transcript
→ Claude Code
```

Start with push-to-talk.

Implement a configurable hotkey.

Keep the voice runtime/model under:

```text
D:\JARVIS\models
```

No cloud speech API required.

---

# 17. MCP

Use MCP as the bridge between Claude Code and the local computer engine.

Keep Claude's conceptual tool surface compact.

Possible tools:

```text
computer.observe
computer.click
computer.type
computer.key
computer.scroll
computer.drag
computer.window
computer.app
computer.element
computer.screen
computer.shell
computer.file
computer.wait
computer.confirm
```

If the selected foundation exposes more internally, that is okay.

Do not unnecessarily rewrite mature code just to reduce its internal tool count.

---

# 18. CLAUDE CODE PROJECT CONFIG

Create/maintain:

```text
CLAUDE.md
.mcp.json
.claude/
```

`CLAUDE.md` should contain the permanent project instructions.

`.mcp.json` should register the local computer-control MCP server using the correct supported configuration for the chosen implementation.

Do not put secrets in files.

Use Claude Code permissions appropriately.

---

# 19. SAFETY

The product has broad control.

Do not bypass:
- UAC secure desktop;
- lock screen;
- MFA;
- CAPTCHA;
- biometrics;
- authentication barriers.

Dangerous operations should require confirmation:
- destructive deletion;
- destructive Git operations;
- firewall/security changes;
- registry changes;
- credential changes;
- financial actions;
- high-impact publishing/sending.

Do not log:
- passwords;
- tokens;
- API keys;
- private secrets.

---

# 20. DESKTOP LAUNCHER

This is required before declaring the product finished.

Create a packaged Windows launcher.

Target:

```text
D:\JARVIS
```

Create:

```text
%USERPROFILE%\Desktop\JARVIS.lnk
```

Requirements:
- name `JARVIS`;
- proper icon;
- launches packaged product;
- correct working directory;
- no VS Code dependency;
- no terminal dependency.

Test by closing VS Code and clicking the icon.

---

# 21. TRAY

Implement a lightweight tray/status process if the chosen architecture supports it cleanly.

It should show:
- JARVIS running;
- listening/not listening;
- stop/emergency;
- restart/settings later.

Do not spend the build on a large UI.

---

# 22. PROJECT ROOT

Everything should be under:

```text
D:\JARVIS
```

Suggested final structure:

```text
D:\JARVIS\
    computer\
    orchestrator\
    jev\
    voice\
    safety\
    state\
    launcher\
    tests\
    benchmarks\
    docs\
    models\
    data\
    logs\
    dist\
    third_party\
    CLAUDE.md
    PRD.md
    TRD.md
    CONTEXT.md
    MASTER_BUILD_PROMPT.md
    THIRD_PARTY_NOTICES.md
```

Adapt this to the selected open-source foundation.

Do not create a duplicate nested product root unless necessary.

---

# 23. TESTING

Do real tests.

Create safe fixtures using:
- Notepad;
- Calculator;
- File Explorer;
- local HTML test page;
- terminal.

Test:
- launch;
- focus;
- keyboard;
- mouse;
- UIA;
- screenshots;
- OCR;
- multi-monitor mapping if available;
- window management;
- files;
- shell;
- stale state;
- verification;
- JEV timeout;
- voice.

---

# 24. REAL END-TO-END TEST

Perform:

> "Open Notepad, type a test message, save it as `D:\JARVIS\data\demo.txt`, close Notepad, reopen it, and verify the content."

The system must actually execute this.

Then perform a browser/local webpage interaction.

Do not fake the result.

---

# 25. BENCHMARK

Create:

```text
benchmarks/
```

Measure:

```text
Whisper latency
Claude latency
MCP latency
UIA latency
JEV latency
OCR latency
native input latency
verification latency
total latency
```

Record actual numbers.

Optimize based on measurements.

---

# 26. PRODUCT UX

The user should be able to say:

> "JARVIS, open VS Code."

and it happens.

Then:

> "Open my AXIOM project."

Then:

> "Check what changed."

Then:

> "Run the tests."

Then:

> "Fix the failures."

Then:

> "Open Chrome and find the latest Qwen paper."

JARVIS should use the correct mechanism for each action.

---

# 27. BUILD MODE — VERY IMPORTANT

Do NOT turn this into:

```text
Phase 1
ask user
Phase 2
ask user
Phase 3
ask user
...
```

The user has explicitly authorized you to build the product.

Work continuously:

```text
inspect
→ decide
→ implement
→ build
→ test
→ fix
→ benchmark
→ package
→ verify
```

Internal phases are fine for dependency ordering, but **do not stop after each phase waiting for the user**.

---

# 28. WHEN SOMETHING BREAKS

Fix it.

Do not respond with:
> "There is an error; you can fix it."

You are the build agent.

Inspect:
- logs;
- stack traces;
- dependencies;
- environment;
- source.

Then correct the implementation and rerun tests.

---

# 29. WHEN AN OPEN-SOURCE COMPONENT IS INCOMPATIBLE

Do not get stuck.

Use the hierarchy:

```text
reuse directly
→ adapt
→ compose
→ replace with another open-source component
→ implement only the missing small piece
```

Do not rewrite a mature subsystem unnecessarily.

---

# 30. WHEN A REQUIREMENT IS IMPOSSIBLE

Do not fake it.

Identify the exact technical/security boundary.

Implement the closest legitimate control path.

Examples:
- UAC secure desktop → human;
- CAPTCHA → human;
- MFA → human.

Everything else should have a serious implementation attempt.

---

# 31. FINAL ACCEPTANCE CHECKLIST

Do not declare success until these are true:

### Launch
- [ ] `D:\JARVIS` exists and contains the product.
- [ ] Desktop `JARVIS.lnk` exists.
- [ ] Icon is correct.
- [ ] Clicking it starts JARVIS.
- [ ] VS Code is not required.

### Voice
- [ ] local Whisper works.
- [ ] microphone works.
- [ ] transcript reaches the agent.

### Claude
- [ ] Claude Code can use the JARVIS MCP.
- [ ] complex tasks can be planned.

### Computer
- [ ] keyboard.
- [ ] mouse.
- [ ] UIA.
- [ ] screenshots.
- [ ] windows.
- [ ] processes.
- [ ] shell.
- [ ] files.
- [ ] browser.
- [ ] multi-monitor/DPI where available.

### JEV
- [ ] adapter works.
- [ ] structured candidate selection works.
- [ ] timeout/retry works.
- [ ] no unnecessary JEV calls.

### Routing
- [ ] deterministic actions bypass models.
- [ ] unique UIA targets bypass JEV.
- [ ] JEV handles ambiguous structured UI.
- [ ] OCR/screen fallback works.
- [ ] Claude vision is fallback.

### Verification
- [ ] meaningful actions are verified.
- [ ] stale UI is detected.

### Safety
- [ ] dangerous operations confirm.
- [ ] security boundaries are respected.

### Performance
- [ ] benchmark exists.
- [ ] latency is measured.
- [ ] obvious unnecessary model calls have been removed.

### Packaging
- [ ] packaged launcher works.
- [ ] desktop icon works.
- [ ] D: drive requirement is respected.

---

# 32. START NOW

Do not give the user another architecture essay.

Start by inspecting the current folder and environment.

Then inspect the open-source foundations.

Then create `docs/FOUNDATION_DECISION.md`.

Then implement.

Then test.

Then fix.

Then package.

Then create the desktop icon.

Then test launching it.

The final result should be a working JARVIS product, not a plan for one.

**BUILD IT.**
