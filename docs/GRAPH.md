# JARVIS knowledge graphs — recall for the codebase and for JARVIS's own experience (2026-09-22)

JARVIS uses **two** graphs, for two different kinds of recall. Both are **local-first**; the personal one never leaves
the machine.

## 1. Memory graph — JARVIS's own experience (native, live, private)
`jarvis/graph/` builds an incremental knowledge graph of what JARVIS actually does, so it (and Claude) can recall past
activity: which day/session, what the user asked, which app was touched, which tools ran, and whether the turn verified.

- **Where:** `data/graph/memory_graph.json` (git-ignored — it holds the user's activity). Atomic writes; a corrupt file
  recovers to empty. Bounded (oldest pruned).
- **Privacy:** every label is run through `safety.privacy.redact_text` before storage; structured secrets (keys, cards,
  tokens) are stripped. No screenshots, no raw transcripts — short labels + counts only. **Never networked.**
- **How it fills:** one `record_turn(...)` per completed turn from `jarvis/app/main.py` (linking
  `date → session → turn → {app, tools, outcome, topics}`), plus `record_memory(...)` for stored memories. Enabled by
  `settings.json → graph_enabled` (default true).
- **CLI:**
  ```powershell
  .venv\Scripts\python.exe -m jarvis.graph stats            # node/edge counts by type
  .venv\Scripts\python.exe -m jarvis.graph recall "chrome"  # nodes mentioning a term, most-recent first
  .venv\Scripts\python.exe -m jarvis.graph export           # write data/graph/graph.json (graphify shape)
  ```
- **Visualize:** `export` writes a `graph.json` in **graphify's** node/edge shape, so graphify's viewer/query tooling
  works on it — but the file stays on disk; nothing is uploaded.

## 2. Codebase graph — graphify (the repo the user asked to bring in)
[`graphifyy`](https://github.com/Graphify-Labs/graphify) turns the JARVIS **source** into a queryable graph so Claude can
recall architecture and file relationships. Code extraction is **fully local** (tree-sitter AST, no LLM, no key). It is
pinned in `requirements.txt` (`graphifyy==0.9.65`) and installed by `launcher/install.ps1`.

```powershell
.venv\Scripts\graphify.exe update jarvis --no-cluster        # (re)build the code graph → jarvis\graphify-out\graph.json (local, no LLM)
.venv\Scripts\graphify.exe explain "ClaudeSession"           # explain a node + its neighbours
.venv\Scripts\graphify.exe path "JarvisApp" "JevDecisionEngine"   # trace how two parts connect
.venv\Scripts\graphify.exe install --platform claude         # expose the /graphify skill to JARVIS's Claude brain
```

`graphify-out/` is git-ignored (rebuildable from source in seconds). A first build of `jarvis/` produced ~1034 nodes /
2294 edges here. Docs/PDF extraction (optional) needs an LLM backend key (`--backend`); code recall does not.

## Which to use
- "How does JARVIS's code fit together?" → **graphify** (codebase graph).
- "What did JARVIS do about X before / what does it know?" → **memory graph** (`jarvis.graph recall`).
