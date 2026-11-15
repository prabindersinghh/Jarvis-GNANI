"""Windows computer-control engine.

Vendored from https://github.com/sshh12/windows-computer-use-mcp
(commit 8d367a8, MIT License, Copyright (c) 2026 sshh12) — see
THIRD_PARTY_NOTICES.md. Local modifications are marked with `# JARVIS:`.

Importing this package enables per-monitor-v2 DPI awareness. This MUST happen
before any capture or input so everything operates in physical pixels.
"""
from .dpi import enable_dpi_awareness

DPI_AWARENESS = enable_dpi_awareness()
