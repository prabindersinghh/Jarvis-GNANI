# JARVIS

Personal Windows computer agent. **Claude Code is the brain** (your Claude Max login), **JEV is the reflex layer**,
Gnani Prisma provides speech recognition and Gnani Timbre provides speech synthesis. A vendored Windows engine (UIA, SendInput, DPI-aware multi-monitor capture, OCR)
is the hands. Lives entirely under `D:\JARVIS`.

```
click Desktop\JARVIS → hold Ctrl+Alt+J and speak → Gnani Prisma transcribes → Claude plans →
JARVIS MCP executes (deterministic → UIA → JEV → OCR → vision → human) → every step verified
```

- **Start:** `Desktop\JARVIS.lnk` (→ signed `.venv\Scripts\pythonw.exe -m jarvis.app`). A desktop **orb** appears bottom-right;
  click it to talk/type. No VS Code, no terminal. UI details: [docs/ORB_UI.md](docs/ORB_UI.md).
- **Feels immediate:** adaptive end-of-turn, streaming clause-by-clause speech, an instant local "On it." for longer
  tasks, and voice barge-in. Voice audio uses Gnani Prisma/Timbre APIs. Details: [docs/PERFORMANCE_UX_2026-09-22.md](docs/PERFORMANCE_UX_2026-09-22.md).
- **Recall:** a local private memory graph of JARVIS's own experience + a graphify codebase graph —
  [docs/GRAPH.md](docs/GRAPH.md). How it all came to be: [docs/PROJECT_HISTORY.md](docs/PROJECT_HISTORY.md).
- **Docs:** [docs/USAGE.md](docs/USAGE.md) · [docs/FOUNDATION_DECISION.md](docs/FOUNDATION_DECISION.md) ·
  [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) · [benchmarks/results.md](benchmarks/results.md) · [CLAUDE.md](CLAUDE.md)
- **Rebuild/install:** `powershell -ExecutionPolicy Bypass -File launcher\install.ps1`
- **Tests:** `.venv\Scripts\python.exe -m pytest tests -q` (unit) · `-m live` (drives the real desktop)
- **JEV key:** `data\config\.env` → `TYPESAFE_API_KEY=...` (optional; JARVIS degrades gracefully without it).
  Copy [.env.example](.env.example) → `data\config\.env`. The real `.env` is git-ignored and never committed.

## Restore from this backup
This repo is a **private** backup of the code — not of personal data. Everything under `data/` (conversations, memory,
experiences, policies, the JEV key) and the large `models/`/`.venv/`/`dist/` are git-ignored on purpose. To bring JARVIS
back on a new machine:

```powershell
git clone <this-private-repo> D:\JARVIS
cd D:\JARVIS
copy .env.example data\config\.env      # then paste your TYPESAFE_API_KEY
powershell -ExecutionPolicy Bypass -File launcher\install.ps1   # rebuilds venv, exe, shortcut
```
