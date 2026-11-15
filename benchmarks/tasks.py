"""Scenario benchmark: real Claude-driven desktop tasks. Records Claude turns, tool calls (UIA/screenshots/JEV),
tokens, latency and completion per task -> benchmarks/tasks_<tag>.json + a markdown row in benchmarks/tasks_rows.md.

    python -m benchmarks.tasks --tag before
    python -m benchmarks.tasks --tag after [--only notepad_save_reopen,calculator]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("JARVIS_AUTO_CONFIRM", "1")
os.environ.setdefault("PYTHONUTF8", "1")
ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "tests" / "fixtures" / "page.html"
BENCH_FILE = ROOT / "data" / "bench.txt"

PAGE_HTML = """<html><head><title>JARVIS Test Page</title></head><body><h1>JARVIS fixture</h1>
<label>Name <input id="name" aria-label="Name field"></label>
<button onclick="document.getElementById('out').textContent='CLICKED:'+document.getElementById('name').value">Submit</button>
<p id="out" aria-live="polite">waiting</p></body></html>"""

TASKS = {
    "notepad_save_reopen": (
        "Open Notepad, type the message 'JARVIS benchmark OK', save it as D:\\JARVIS\\data\\bench.txt (overwrite if asked), "
        "close Notepad, reopen that file and verify its content. Be brief.",
        lambda text: BENCH_FILE.exists() and "JARVIS benchmark OK" in BENCH_FILE.read_text(encoding="utf-8", errors="replace"),
    ),
    "calculator": (
        "Open Calculator, compute 12*34 using the keyboard, and tell me the result. Be brief.",
        lambda text: "408" in text,
    ),
    "edge_form": (
        f"Open {PAGE.as_uri()} in Microsoft Edge (new window), type 'Bob Marley' into the Name field, click Submit, "
        "verify the page shows 'CLICKED:Bob Marley', then close that browser window. Be brief.",
        lambda text: "CLICKED:Bob Marley" in text or "verified" in text.lower(),
    ),
}


def _server_metrics_after(marker_ts: float) -> dict:
    """Aggregate JARVIS MCP server metrics flushed after marker_ts (server-exit lines in logs/metrics.jsonl)."""
    out = {"uia_calls": 0, "jev_calls": 0, "screenshots": 0, "ocr": 0, "tiers": {}}
    f = ROOT / "logs" / "metrics.jsonl"
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if d.get("ts", 0) < marker_ts or d.get("tag") != "server-exit":
            continue
        out["uia_calls"] += d.get("uia_ms", {}).get("n", 0)
        out["jev_calls"] += d.get("jev_ms", {}).get("n", 0)
        out["screenshots"] += d.get("tiers", {}).get("vision", 0)
        out["ocr"] += d.get("ocr_ms", {}).get("n", 0)
        for k, v in d.get("tiers", {}).items():
            out["tiers"][k] = out["tiers"].get(k, 0) + v
    return out


def _cleanup():
    from jarvis.computer import server as s
    for q in ("Notepad", "Calculator", "JARVIS Test Page"):
        for w in json.loads(s.computer_window("list", q))["windows"]:
            s.computer_window("close", w["title"])
            s.computer_click("Don't save")
    try:
        BENCH_FILE.unlink()
    except OSError:
        pass


async def run_task(name: str, prompt: str, check) -> dict:
    from jarvis.app.claude_session import ClaudeSession
    _cleanup()
    marker = time.time()
    cs = ClaudeSession()
    t0 = time.perf_counter()
    await cs.connect(resume_previous=False)
    connect_ms = round((time.perf_counter() - t0) * 1000)
    r = await cs.ask(prompt)
    await cs.disconnect()
    await asyncio.sleep(1.5)
    srv = _server_metrics_after(marker)
    tools = r.get("tool_calls", {})
    text = r.get("text") or ""
    usage = r.get("usage") or {}
    return {"task": name, "ok": bool(check(text)) and not r.get("is_error"), "turns": r.get("num_turns"),
            "claude_ms": r.get("claude_ms"), "connect_ms": connect_ms,
            "tool_calls": sum(tools.values()), "jarvis_tool_calls": sum(v for k, v in tools.items() if k.startswith("mcp__jarvis__")),
            "tools": tools, "screenshots": srv["screenshots"], "jev_calls": srv["jev_calls"], "uia_calls": srv["uia_calls"],
            "ocr": srv["ocr"], "tiers": srv["tiers"],
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"), "cache_create": usage.get("cache_creation_input_tokens"),
            "reply": text[:200]}


def main(argv):
    tag = argv[argv.index("--tag") + 1] if "--tag" in argv else time.strftime("%H%M")
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else list(TASKS)
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(PAGE_HTML, encoding="utf-8")
    results = []
    for name in only:
        prompt, check = TASKS[name]
        print(f"=== {name}", flush=True)
        r = asyncio.run(run_task(name, prompt, check))
        print(json.dumps({k: v for k, v in r.items() if k not in ("tools", "reply")}), flush=True)
        results.append(r)
    _cleanup()
    (ROOT / "benchmarks" / f"tasks_{tag}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    def tot(k):
        return sum((r.get(k) or 0) for r in results)

    row = (f"| {tag} | {sum(r['ok'] for r in results)}/{len(results)} | {tot('turns')} | {tot('jarvis_tool_calls')} | {tot('uia_calls')} | "
           f"{tot('screenshots')} | {tot('jev_calls')} | {round(tot('claude_ms') / 1000, 1)} s | "
           f"{tot('input_tokens')} in + {tot('cache_read')} cached / {tot('output_tokens')} out |")
    header = "| tag | ok | Claude turns | JARVIS tool calls | UIA calls | screenshots | JEV calls | wall | tokens |\n|---|---|---:|---:|---:|---:|---:|---:|---|\n"
    rows = ROOT / "benchmarks" / "tasks_rows.md"
    if not rows.exists():
        rows.write_text(header, encoding="utf-8")
    rows.open("a", encoding="utf-8").write(row + "\n")
    print(header + row)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
