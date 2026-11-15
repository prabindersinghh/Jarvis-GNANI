# JARVIS — FINAL PRODUCT TRD

## Build mandate

Implement the **complete working product now**.

Do not turn this into a tutorial or a long sequence of artificial milestones.

Use the fastest route:
- inspect existing code;
- reuse mature open-source foundations;
- integrate;
- test;
- fix;
- package;
- launch.

---

# 1. Target architecture

```text
                         USER
                           |
                    Voice / Text
                           |
                           v
                    Local Whisper
                           |
                           v
                  +----------------+
                  |   CLAUDE CODE  |
                  |   MAIN BRAIN   |
                  +-------+--------+
                          |
                         MCP
                          |
                  +-------v--------+
                  | JARVIS ROUTER |
                  +---+--------+---+
                      |        |
               deterministic   UI decision
                      |        |
                      v        v
                 Native/UIA    JEV
                      |        |
                      +----+---+
                           |
                           v
                    Windows Desktop
                           |
                 +---------+---------+
                 |                   |
               OCR              Claude Vision
```

---

# 2. Foundation

Before implementing equivalent functionality, inspect:

```text
Windows-MCP
https://github.com/danielsimonjr/Windows-MCP

windows-computer-use-mcp
https://github.com/sshh12/windows-computer-use-mcp
```

Evaluate current code, licenses, maintenance and completeness.

The first is a strong candidate for the Windows/MCP foundation.

Use other repositories as reference where they provide better implementation ideas.

Create `docs/FOUNDATION_DECISION.md`, then immediately implement the selected architecture.

Do not spend the project doing research without building.

---

# 3. External MCP surface

Keep Claude's exposed interface compact. A practical conceptual set:

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

The internal implementation can contain many more native operations.

---

# 4. Internal routing

Every action passes through a router:

```text
Can deterministic local code do it?
    YES → execute directly
    NO
Can UIA identify it uniquely?
    YES → execute UIA
    NO
Are there structured candidates?
    YES → JEV
    NO
Can OCR/structured screen extraction solve it?
    YES → execute
    NO
Use Claude vision/reasoning
```

---

# 5. JEV adapter

Create an isolated adapter so the rest of the system does not depend directly on the JEV SDK.

Conceptually:

```text
JevDecisionEngine.choose(
    goal,
    candidate_actions,
    current_state
)
```

It should return:
- selected operation;
- selected target;
- confidence if supported;
- timing/usage metadata.

Handle:
- timeout;
- retry;
- cancellation;
- API errors;
- cost/usage metrics.

Keep requests compact.

---

# 6. UI state

Normalize Windows UI state into a compact representation.

Example:

```json
{
  "capture_id": "state-123",
  "active_window": {
    "title": "Chrome",
    "process": "chrome.exe"
  },
  "elements": [
    {
      "id": 42,
      "role": "button",
      "name": "Download",
      "enabled": true,
      "visible": true,
      "bounds": [100, 200, 220, 245],
      "patterns": ["Invoke"]
    }
  ]
}
```

Use actual UIA properties available from the chosen implementation.

Do not invent fake capabilities.

---

# 7. Raw control

Implement robust Windows input:

- mouse move;
- left/right/middle click;
- double click;
- drag;
- hold/release;
- scroll;
- keyboard down/up;
- shortcuts;
- text input;
- clipboard/paste.

Use native Windows APIs where appropriate.

---

# 8. UI Automation

Support:
- tree/state capture;
- element lookup;
- properties;
- text/value;
- invoke;
- select;
- expand/collapse;
- wait;
- window scoping.

Prefer UIA over coordinates.

---

# 9. Screen/OCR

Implement:
- multi-monitor screenshots;
- DPI-aware coordinate mapping;
- region capture;
- OCR.

Do not make screenshot analysis the default path.

---

# 10. Browser

Prefer:
1. DOM/accessibility/structured browser control;
2. UIA;
3. native input;
4. OCR/vision.

Support:
- tabs;
- navigation;
- forms;
- search;
- clicks;
- typing;
- scrolling;
- downloads;
- uploads.

---

# 11. Voice

Use local Whisper.

Required:
- microphone capture;
- VAD;
- push-to-talk;
- transcript;
- error handling.

Store model/runtime data under:

```text
D:\JARVIS\models
```

---

# 12. Claude Code integration

Create/maintain:

```text
CLAUDE.md
.mcp.json
.claude/
```

Use Claude Code's supported MCP/configuration/permission mechanisms.

Do not duplicate Claude as another independent general-purpose API client without a concrete reason.

---

# 13. Verification

Every meaningful action should have a verifier.

Implement reusable verification primitives:

```text
verifyWindow
verifyProcess
verifyElement
verifyText
verifyFile
verifyDirectory
verifyCommand
verifyBrowserState
```

---

# 14. Stale-state handling

Observations need:
- capture ID;
- timestamp;
- relevant window identity;
- state hash/version if feasible.

Reject stale action requests.

Re-observe and re-decide.

---

# 15. Error handling

All operations require:
- timeout;
- cancellation;
- bounded retry;
- recovery;
- clear error.

Never use infinite retry.

Prefer state-based waits over arbitrary sleeps.

---

# 16. Safety/policy

Create a policy layer before execution.

It should classify:
- safe;
- confirmation-required;
- blocked/security-boundary.

Never bypass:
- UAC;
- secure desktop;
- MFA;
- CAPTCHA;
- lock screen;
- biometrics.

---

# 17. Performance instrumentation

Instrument:

```text
voice_ms
claude_ms
mcp_ms
uia_ms
jev_ms
ocr_ms
input_ms
verification_ms
total_ms
```

Create a benchmark command.

Do not claim performance without measurement.

---

# 18. Desktop launcher

Create:

```text
D:\JARVIS\launcher\
```

and a packaged executable/launcher.

Create:

```text
%USERPROFILE%\Desktop\JARVIS.lnk
```

with:
- JARVIS icon;
- correct target;
- correct working directory;
- no VS Code dependency.

Test by launching the shortcut.

---

# 19. Project layout

Adapt to the chosen foundation, but aim for:

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

Do not create duplicate nested roots.

---

# 20. Testing

Create automated tests for:
- keyboard;
- mouse;
- UIA;
- windows;
- filesystem;
- shell;
- screenshots;
- stale state;
- JEV timeout;
- verification;
- voice pipeline;
- launcher.

Use safe fixtures such as Notepad, Calculator, File Explorer and a local test webpage.

---

# 21. Final product test

Run an actual end-to-end test:

```text
voice:
"Open Notepad, type a test message, save it as
D:\JARVIS\data\demo.txt, close Notepad, reopen the file,
and verify the content."
```

Then verify:
- voice works;
- Claude understands;
- MCP works;
- computer control works;
- verification works;
- file exists;
- content is correct;
- JARVIS reports completion.

Also test a browser/local-web scenario.

---

# 22. Packaging

At the end:
- build release binaries;
- confirm D: drive paths;
- create desktop shortcut;
- confirm launcher works without VS Code;
- confirm graceful shutdown;
- confirm logs;
- confirm no secrets are committed.

---

# 23. Definition of engineering success

A user should be able to treat JARVIS as:

```text
"Computer, do this."
```

and have Claude handle the intelligence while the local computer engine handles the actual machine.

The architecture should be fast enough that simple interactions do not feel like a cloud-agent demo.
