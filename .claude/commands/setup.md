---
description: Install JARVIS on this machine (Python, Claude Code, Whisper model, MCP servers, shortcut) in one go
allowed-tools: Bash(powershell:*), Bash(powershell.exe:*)
---
Install JARVIS · GNANI on this Windows machine by running the one-shot installer from the repo root:

```
powershell -NoProfile -ExecutionPolicy Bypass -File ./setup.ps1 $ARGUMENTS
```

Run it with a 10-minute timeout (first run downloads Python packages and the Whisper model). Stream nothing back
while it runs. When it finishes, report in two or three lines: whether every step passed, anything flagged with `!!`
or `XX`, and the one thing the user must still do (usually: sign in to Claude Code once with `claude`).
If a step failed, read the error, fix the cause, and re-run setup — it is idempotent.
