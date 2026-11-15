# Third-party notices

## windows-computer-use-mcp (vendored)
- Source: https://github.com/sshh12/windows-computer-use-mcp — commit `8d367a8` (2026-06-07)
- License: MIT — Copyright (c) 2026 sshh12 (full text: `third_party/LICENSE.windows-computer-use-mcp.txt`)
- Files: `jarvis/computer/engine/{dpi,displays,capture,input,keymap,coords,targets,images,winfind,windows,ocr,process,clipboard,system,artifacts,viewports}.py`
- Modifications: package relocation; JARVIS additions live in separate modules (`jarvis/computer/uia.py`, `ocr_boxes.py`).

## Windows-MCP (optional prebuilt binary, not linked)
- Source: https://github.com/danielsimonjr/Windows-MCP — commit `2a74b3e` (2026-09-17), `bundle/WindowsMcp.exe`
- License: MIT — Copyright (c) 2025 JEOMON GEORGE (`third_party/windows-mcp/LICENSE.txt`)
- Use: optional second MCP server for deep admin tools; see `docs/OPTIONAL_WINDOWS_MCP.md`.

## thinking-orbs (vendored engine)
- Source: https://github.com/Jakubantalik/thinking-orbs — npm `thinking-orbs@0.3.1` (`third_party/thinking-orbs/package`)
- License: MIT — Copyright (c) 2026 Jakub Antalik (`third_party/thinking-orbs/package/LICENSE`)
- Use: `dist/engine.es.js` (the framework-free geometry/painter engine) is inlined verbatim into `jarvis/ui/web/orb.html`
  by `jarvis/ui/build.py` (only the ES `export {}` statement is rewritten to a `window.ThinkingOrbsEngine` assignment).
  JARVIS renders the engine's `MODE_FRAMES` geometry with its own colour painter; the React wrapper is not used.
  Reference site: https://rareformlabs.github.io/thinking-orbs/

## pywebview (dependency)
- https://github.com/r0x0r/pywebview — BSD-3-Clause. Hosts the orb in a WebView2 window (Microsoft Edge WebView2 Runtime,
  already present on Windows 11; not redistributed).

## Architecture references (no code copied)
- browser-use/jev-ultrafast (MIT) — operation + speculative target heads in one JEV request; validation of choice answers.
- awlevin/typesafe-computer-use (MIT) — kind/item multi-question decisions, confidence gating.

## Python dependencies (pip, see `requirements.txt`)
mcp (MIT), mss (MIT), Pillow (MIT-CMU), uiautomation (Apache-2.0), httpx (BSD-3), faster-whisper (MIT),
CTranslate2 (MIT), sounddevice (MIT), pystray (LGPL-3.0, used as an unmodified library), pynput (LGPL-3.0, unmodified
library), psutil (BSD-3), pywin32 (PSF), claude-agent-sdk (MIT), numpy (BSD-3), pyinstaller (GPL-2.0 with bootloader
exception — build tool only; the produced JARVIS.exe is not GPL-encumbered).

## Models
- Whisper `base.en` / `small` via faster-whisper (Systran CTranslate2 conversions), MIT; weights under `models/`.


## pythonnet + WebView2 composition host (orb UI redesign, 2026-09-21)
- The orb window (`jarvis/ui/comphost.py`) hosts a WebView2 *composition controller* on a WS_EX_NOREDIRECTIONBITMAP
  window via DirectComposition for true per-pixel transparency. It uses:
  - `Microsoft.Web.WebView2.Core.dll` — the signed WebView2 .NET assembly shipped with pywebview
    (`.venv/Lib/site-packages/webview/lib/`), Microsoft proprietary redistributable; the runtime is the system Edge
    WebView2 Runtime (present on Windows 11, not redistributed here).
  - pythonnet (`clr`) — MIT — to load that assembly.
  - user32 / d3d11 / dcomp (system libraries) via ctypes for the window, D3D device and DirectComposition visual.
  No new compiled binary is produced (Smart App Control blocks unsigned exes), so the orb host stays pure Python over
  signed Microsoft components. pywebview is retained as the source of the WebView2 assemblies.


## OpenMuse — concepts only, NO code or assets vendored (2026-09-23)
- Reference: `github.com/CopilotKit/openmuse`, MIT License (alpha). Also referenced: CopilotKit docs (`mcp-servers`,
  `mastra/ag-ui`) and the AG-UI event vocabulary.
- The JARVIS durable task engine, follow-up queue, receipts, event bus (AG-UI-compatible event names via
  `to_agui()`), notifications, and recurring watches (`jarvis/tasks/`) are a **clean-room reimplementation of the
  concepts in Python** for native Windows. **No OpenMuse source code, assets, branding, or dependencies were copied
  or installed.** `graphifyy` is graphify's, not OpenMuse's. See docs/OPENMUSE_INTEGRATION_AUDIT.md and
  docs/OPENMUSE_INTEGRATION_REPORT.md. Recorded per the MIT terms and the integration directive §44.

## graphifyy (codebase knowledge graph, 2026-09-22)
- `graphifyy` (PyPI), dual-licensed Apache-2.0 / MIT, used as an unmodified CLI/library for the codebase knowledge
  graph (docs/GRAPH.md). Pulls tree-sitter grammars (MIT/Apache-2.0), networkx (BSD-3), rapidfuzz (MIT).
