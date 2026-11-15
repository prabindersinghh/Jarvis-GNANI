"""JARVIS latency benchmark (TRD §17). Measures each layer on THIS machine and writes benchmarks/results.md.

    python -m benchmarks.run [--jev] [--claude]

Layers: native input, UIA observe, element re-resolve + Invoke, verification, OCR, screenshot, MCP round-trip
(stdio), Whisper (real audio fixture), JEV (only with a key and --jev), Claude turn (only with --claude; uses your
Claude Code login), and a full deterministic launch→type→verify chain.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("JARVIS_AUTO_CONFIRM", "1")
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmarks" / "results.md"


def ms(t0):
    return (time.perf_counter() - t0) * 1000


def rep(fn, n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append(ms(t0))
    return {"n": n, "median": round(statistics.median(xs)), "min": round(min(xs)), "max": round(max(xs))}


def main(argv):
    from jarvis.computer import server as s
    from jarvis.computer import uia
    from jarvis.computer.engine import capture, input as winput, winfind
    from jarvis.computer import ocr_boxes
    from jarvis.orchestrator import router, verify

    J = json.loads
    R: dict[str, dict] = {}

    # fixture: fresh Notepad
    for w in J(s.computer_window("list", "Notepad"))["windows"]:
        s.computer_window("close", w["title"])
    t0 = time.perf_counter()
    r = J(s.computer_app("launch", "notepad.exe", wait_window="Notepad", timeout=12))
    R["app_launch_verified_ms"] = {"n": 1, "median": round(ms(t0)), "min": round(ms(t0)), "max": round(ms(t0))}
    assert r["ok"], r
    hwnd = winfind.find_windows("Notepad")[0]["hwnd"]

    # native input
    R["native_key_tap_ms"] = rep(lambda: winput.tap("shift"), 20)
    x, y = 960, 600
    R["native_mouse_move_ms"] = rep(lambda: winput.move(x, y), 20)
    R["native_click_ms"] = rep(lambda: winput.click("left", 1, x, y), 10)

    # UIA
    R["uia_observe_ms"] = rep(lambda: router.observe(hwnd, interactive_only=True), 8)
    obs = router.observe(hwnd, interactive_only=True)
    doc = next(e for e in obs.elements if e["role"] == "document")
    R["uia_find_control_ms"] = rep(lambda: uia.find_control(hwnd, doc), 8)
    R["uia_text_read_ms"] = rep(lambda: uia.read_text(uia.find_control(hwnd, doc)), 5)

    # typing (paste-first) + verification
    R["type_20chars_paste_verified_ms"] = rep(lambda: J(s.computer_type("benchmark text 12345", target=doc["id"], capture_id=router.observe(hwnd).capture_id, clear=True, window=hwnd)), 5)
    R["verification_state_changed_ms"] = rep(lambda: verify.state_changed("nope", hwnd, timeout=1.0), 5)
    R["verification_fingerprint_ms"] = rep(lambda: verify.snapshot_fingerprint(hwnd), 8)

    # UIA click with routing (menu File → escape)
    def click_file():
        J(s.computer_click("File", window=hwnd))
        winput.tap("escape")
    R["uia_click_menu_routed_verified_ms"] = rep(click_file, 5)

    # screen + OCR
    R["screenshot_window_ms"] = rep(lambda: capture.grab_window(hwnd), 8)
    img, rect, _ = capture.grab_window(hwnd)
    R["ocr_window_ms"] = rep(lambda: ocr_boxes.ocr_boxes(img), 3)
    R["screenshot_tool_full_ms"] = rep(lambda: s.computer_screen("screenshot", "window:Notepad", max_dim=1568), 3)

    # MCP stdio round-trip
    async def mcp_rt():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=str(ROOT / ".venv/Scripts/python.exe"), args=["-m", "jarvis.computer"], cwd=str(ROOT), env={**os.environ})
        t0 = time.perf_counter()
        async with stdio_client(params) as (rd, wr):
            async with ClientSession(rd, wr) as sess:
                await sess.initialize()
                init = ms(t0)
                xs, ys = [], []
                for _ in range(8):
                    t1 = time.perf_counter(); await sess.call_tool("computer_status", {}); xs.append(ms(t1))
                    t1 = time.perf_counter(); await sess.call_tool("computer_observe", {"window": "Notepad"}); ys.append(ms(t1))
                return init, xs, ys
    init, xs, ys = asyncio.run(mcp_rt())
    R["mcp_server_startup_ms"] = {"n": 1, "median": round(init), "min": round(init), "max": round(init)}
    R["mcp_roundtrip_status_ms"] = {"n": len(xs), "median": round(statistics.median(xs)), "min": round(min(xs)), "max": round(max(xs))}
    R["mcp_roundtrip_observe_ms"] = {"n": len(ys), "median": round(statistics.median(ys)), "min": round(min(ys)), "max": round(max(ys))}

    # Whisper
    wav = ROOT / "tests" / "fixtures" / "open_notepad.wav"
    if wav.exists():
        from jarvis.voice.mic import load_wav
        from jarvis.voice.whisper import engine
        audio = load_wav(str(wav))
        e = engine()
        t0 = time.perf_counter(); e.load(); R["whisper_load_ms"] = {"n": 1, "median": round(ms(t0)), "min": round(ms(t0)), "max": round(ms(t0))}
        R[f"whisper_{e.model_name}_{round(len(audio)/16000,1)}s_audio_ms"] = rep(lambda: e.transcribe(audio), 3)

    # JEV (live, optional)
    if "--jev" in argv:
        from jarvis.jev import engine as jev
        if jev().available:
            cands = [{"id": i + 1, "role": "listitem", "name": n} for i, n in enumerate(["Documents", "Downloads", "Desktop", "Pictures", "Music", "Videos"])]
            R["jev_pick_6_candidates_ms"] = rep(lambda: jev().pick("Open the Downloads folder", cands, {"title": "File Explorer", "process": "explorer.exe"}), 5)
            R["jev_choose_op_and_target_ms"] = rep(lambda: jev().choose("Open the Downloads folder", cands, {"title": "File Explorer", "process": "explorer.exe"}), 5)
            R["jev_usage"] = jev().metrics.as_dict()
        else:
            R["jev"] = {"note": "no TYPESAFE_API_KEY"}

    # Claude (live, optional)
    if "--claude" in argv:
        from jarvis.app.claude_session import ClaudeSession
        cs = ClaudeSession()

        async def turn():
            t0 = time.perf_counter(); await cs.connect(resume_previous=False); c = ms(t0)
            r1 = await cs.ask("Reply with exactly: pong")
            r2 = await cs.ask("Use computer_status and tell me the DPI mode in three words.")
            await cs.disconnect()
            return c, r1, r2
        c, r1, r2 = asyncio.run(turn())
        R["claude_connect_ms"] = {"n": 1, "median": round(c), "min": round(c), "max": round(c)}
        R["claude_turn_text_only_ms"] = {"n": 1, "median": r1["claude_ms"], "min": r1["claude_ms"], "max": r1["claude_ms"], "first_token": r1.get("first_token_ms")}
        R["claude_turn_one_tool_ms"] = {"n": 1, "median": r2["claude_ms"], "min": r2["claude_ms"], "max": r2["claude_ms"], "first_token": r2.get("first_token_ms")}

    # deterministic chain total
    def chain():
        for w in J(s.computer_window("list", "Notepad"))["windows"]:
            s.computer_window("close", w["title"])
            s.computer_click("Don't save")
        r = J(s.computer_app("launch", "notepad.exe", wait_window="Notepad", timeout=12))
        o = J(s.computer_observe("Notepad"))
        d = next(e for e in o["elements"] if e["role"] == "document")
        t = J(s.computer_type("chain", target=d["id"], capture_id=o["capture_id"], clear=True, window="Notepad"))
        assert r["ok"] and t["ok"]
    R["total_launch_observe_type_verify_ms"] = rep(chain, 3)

    for w in J(s.computer_window("list", "Notepad"))["windows"]:
        s.computer_window("close", w["title"])
        s.computer_click("Don't save")

    # write results
    import platform
    lines = [f"# JARVIS benchmark — {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"Machine: {platform.processor()} · {platform.platform()} · Python {platform.python_version()} · "
             f"DPI {__import__('jarvis.computer.engine', fromlist=['DPI_AWARENESS']).DPI_AWARENESS}", "",
             "| metric | n | median ms | min | max |", "|---|---:|---:|---:|---:|"]
    for k, v in R.items():
        if isinstance(v, dict) and "median" in v:
            extra = f" (first token {v['first_token']} ms)" if v.get("first_token") else ""
            lines.append(f"| {k} | {v['n']} | **{v['median']}**{extra} | {v['min']} | {v['max']} |")
        else:
            lines.append(f"| {k} | | {json.dumps(v)} | | |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    from jarvis.orchestrator.metrics import metrics
    metrics.flush("benchmark")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
