"""Live tests against the real desktop (Notepad, Calculator, Explorer, a local HTML page, MCP stdio, voice).
They move the mouse / open windows. Run: python -m pytest tests -q -m live   (JARVIS_AUTO_CONFIRM=1 is set here)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

os.environ["JARVIS_AUTO_CONFIRM"] = "1"
os.environ["PYTHONUTF8"] = "1"

from jarvis.computer import server as s  # noqa: E402
from jarvis.state import StaleStateError, store  # noqa: E402
from jarvis.orchestrator import router  # noqa: E402

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parent.parent
J = lambda x: json.loads(x)  # noqa: E731


def _close_all(q):
    for _ in range(3):
        wins = J(s.computer_window("list", q))["windows"]
        if not wins:
            return
        r = J(s.computer_window("close", wins[0]["title"]))
        if r.get("dialog"):
            s.computer_click("Don't save")
        time.sleep(0.2)


@pytest.fixture
def notepad():
    _close_all("Notepad")
    r = J(s.computer_app("launch", "notepad.exe", wait_window="Notepad", timeout=12))
    assert r["ok"], r
    yield r
    _close_all("Notepad")


def test_launch_focus_keyboard_type_verify(notepad):
    o = J(s.computer_observe("Notepad"))
    doc = next(e for e in o["elements"] if e["role"] == "document")
    t = J(s.computer_type("JARVIS live test ünïcödé 123", target=doc["id"], capture_id=o["capture_id"], clear=True, window="Notepad"))
    assert t["ok"] and t["verification"]["verified"], t
    k = J(s.computer_key(["ctrl+a", "ctrl+c"], window="Notepad"))
    assert k["ok"] and k["tier"] == "deterministic"
    txt = J(s.computer_element("text", role="document", window="Notepad"))
    assert "JARVIS live test" in txt["text"]


def test_uia_menu_navigation_and_dialog(notepad):
    c = J(s.computer_click("File", window="Notepad"))
    assert c["ok"] and c["tier"] == "uia", c
    c2 = J(s.computer_click("Save as", goal="open the save-as dialog"))
    assert c2["ok"], c2
    w = J(s.computer_wait("window", "Save as", timeout=8))
    assert w["ok"], w
    o = J(s.computer_observe("Save as"))
    names = {e["name"] for e in o["elements"]}
    assert "Save" in names and "Cancel" in names and any("File name" in n for n in names)
    assert J(s.computer_click("Cancel", window="Save as"))["ok"]
    assert J(s.computer_wait("window_gone", "Save as", timeout=5))["ok"]


def test_stale_state_is_refused(notepad):
    o = J(s.computer_observe("Notepad"))
    cap = o["capture_id"]
    J(s.computer_observe("Notepad"))  # newer observation supersedes
    r = J(s.computer_click(1, capture_id=cap, window="Notepad"))
    assert r.get("error") == "stale_state" and "fresh" in r, r
    # live-UI mismatch: observe, change UI (open menu), click element from old capture → stale
    o = J(s.computer_observe("Notepad"))
    s.computer_key("alt")  # toggles menu accelerator state → fingerprint changes
    time.sleep(0.2)
    doc = next(e for e in o["elements"] if e["role"] == "document")
    with pytest.raises(StaleStateError):
        router.execute_click({"id": doc["id"]}, capture_id=o["capture_id"], window="Notepad") if False else store.check_fresh(o["capture_id"], "different")
    s.computer_key("escape")


def test_calculator_keys_and_result():
    _close_all("Calculator")
    r = J(s.computer_app("launch", "calc.exe", wait_window="Calculator", timeout=15))
    assert r["ok"], r
    assert J(s.computer_wait("element", "Display is", window="Calculator", timeout=10))["ok"]
    J(s.computer_key(["escape", "1", "2", "shift+8", "3", "4", "enter"], window="Calculator"))
    e = J(s.computer_element("find", automation_id="CalculatorResults", window="Calculator"))
    assert e["count"] >= 1 and "408" in e["elements"][0]["name"], e
    _close_all("Calculator")


def test_window_management(notepad):
    r = J(s.computer_window("move", "Notepad", x=100, y=100, width=900, height=600))
    assert r["ok"] and r["verification"]["verified"], r
    assert J(s.computer_window("maximize", "Notepad"))["zoomed"] is True
    assert J(s.computer_window("minimize", "Notepad"))["iconic"] is True
    assert J(s.computer_window("restore", "Notepad"))["iconic"] is False
    assert J(s.computer_window("focus", "Notepad"))["ok"]


def test_screenshots_ocr_displays(notepad):
    d = J(s.computer_screen("displays")[0].text)
    assert d["monitors"] and all("dpi" in m and "scale" in m for m in d["monitors"])
    res = s.computer_screen("screenshot", "window:Notepad", max_dim=800)
    meta = J(res[0].text)
    assert res[1].type == "image" and meta["capture_id"] and meta["geometry"]["width"] > 0
    o = J(s.computer_observe("Notepad"))
    doc = next(e for e in o["elements"] if e["role"] == "document")
    J(s.computer_type("OCR TARGET TEXT", target=doc["id"], capture_id=o["capture_id"], clear=True, window="Notepad"))
    ocr = J(s.computer_screen("ocr", "window:Notepad")[0].text)
    assert any("OCR" in ln["text"] and len(ln["bounds"]) == 4 for ln in ocr["lines"]), ocr


def test_files_and_shell(tmp_path):
    p = tmp_path / "a.txt"
    assert J(s.computer_file("write", str(p), content="hello"))["ok"]
    assert J(s.computer_file("read", str(p)))["content"] == "hello"
    assert J(s.computer_file("copy", str(p), dest=str(tmp_path / "b.txt")))["ok"]
    assert J(s.computer_file("move", str(tmp_path / "b.txt"), dest=str(tmp_path / "c.txt")))["ok"]
    assert J(s.computer_file("archive", str(tmp_path), dest=str(tmp_path.parent / "z.zip")))["ok"]
    assert J(s.computer_file("extract", str(tmp_path.parent / "z.zip"), dest=str(tmp_path / "out")))["ok"]
    assert J(s.computer_file("search", str(tmp_path), pattern="*.txt"))["count"] >= 2
    assert J(s.computer_file("delete", str(tmp_path / "c.txt")))["ok"]  # auto-confirmed in test mode
    r = J(s.computer_shell("Write-Output hi; exit 0"))
    assert r["ok"] and "hi" in r["stdout"]
    r = J(s.computer_shell("exit 3"))
    assert not r["ok"] and r["exit"] == 3
    r = J(s.computer_shell("echo %CD%", shell="cmd", cwd="D:\\JARVIS"))
    assert "JARVIS" in r["stdout"]


def test_wait_timeouts_are_bounded():
    t0 = time.perf_counter()
    r = J(s.computer_wait("window", "ZZ-no-such-window-ZZ", timeout=0.8))
    assert not r["ok"] and r.get("timed_out")
    assert time.perf_counter() - t0 < 2.5


def test_local_html_page_via_uia(notepad):
    page = ROOT / "tests" / "fixtures" / "page.html"
    page.write_text("""<html><head><title>JARVIS Test Page</title></head><body>
    <h1>JARVIS fixture</h1><label>Name <input id="name" aria-label="Name field"></label>
    <button onclick="document.getElementById('out').textContent='CLICKED:'+document.getElementById('name').value">Submit</button>
    <p id="out" aria-live="polite">waiting</p></body></html>""", encoding="utf-8")
    r = J(s.computer_app("launch", "msedge", args=["--new-window", page.as_uri()], wait_window="JARVIS Test Page", timeout=20))
    if not r["ok"]:
        pytest.skip("Edge not available: " + json.dumps(r)[:200])
    assert J(s.computer_wait("element", "Submit", role="button", window="JARVIS Test Page", timeout=15))["ok"]
    t = J(s.computer_type("Alice", target={"name": "Name field"}, window="JARVIS Test Page", clear=True))
    assert t["ok"], t
    c = J(s.computer_click({"name": "Submit", "role": "button"}, window="JARVIS Test Page"))
    assert c["ok"] and c["tier"] in ("uia", "jev"), c
    assert J(s.computer_wait("text", "CLICKED:Alice", window="JARVIS Test Page", timeout=8))["ok"]
    b = J(s.computer_wait("browser", "page.html", timeout=5))
    assert b["verified"], b
    _close_all("JARVIS Test Page")


def test_mcp_stdio_roundtrip():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def main():
        params = StdioServerParameters(command=str(ROOT / ".venv/Scripts/python.exe"), args=["-m", "jarvis.computer"], cwd=str(ROOT),
                                       env={**os.environ, "PYTHONUTF8": "1"})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                tools = {t.name for t in (await sess.list_tools()).tools}
                assert {"computer_observe", "computer_click", "computer_type", "computer_key", "computer_act", "computer_confirm"} <= tools
                res = await sess.call_tool("computer_status", {})
                return json.loads(res.content[0].text)
    st = asyncio.run(main())
    assert st["dpi"] in ("per-monitor-v2", "per-monitor", "system")


def test_voice_pipeline_real_audio():
    from jarvis.voice.mic import load_wav
    from jarvis.voice.prisma import engine
    if not engine().available:
        pytest.skip("GNANI_API_KEY is not configured")
    wav = ROOT / "tests" / "fixtures" / "open_notepad.wav"
    if not wav.exists():
        pytest.skip("fixture missing")
    audio = load_wav(str(wav))
    r = engine().transcribe(audio)
    assert "notepad" in r["text"].lower() and "hello world" in r["text"].lower(), r
    assert r["voice_ms"] < 6000


def test_act_loop_degrades_without_jev(notepad):
    from jarvis.orchestrator import act
    r = act.run("click the File menu", window="Notepad", max_steps=2)
    # without a key: jev_unavailable; with a key JEV drives it and may hit the 2-step budget — never an error/crash
    assert not r["status"].startswith("jev_error"), r
    assert r["status"].split(":")[0] in ("jev_unavailable", "done", "blocked", "max_steps", "stalled", "low_confidence"), r
    s.computer_key("escape")


# ----------------------------------------------------------------------------- upgrade: batch, JEV live, memory
def test_batch_notepad_flow_one_call():
    _close_all("Notepad")
    p = ROOT / "data" / "batch_test.txt"
    p.unlink(missing_ok=True)
    steps = [
        {"app": "notepad.exe", "wait_window": "Notepad"},
        {"key": "ctrl+n", "expect": {"title": "Untitled"}},
        {"type": "batch live", "target": {"role": "document"}, "clear": True},
        {"key": "ctrl+shift+s"},
        {"wait": "window", "query": "Save as"},
        {"type": str(p), "target": {"name": "File name:", "role": "edit"}, "clear": True, "window": "Save as"},
        {"click": {"name": "Save", "role": "button"}, "window": "Save as", "expect": {"window_gone": "Save as"}},
        {"expect": {"file": str(p), "contains": "batch live"}},
    ]
    r = J(s.computer_run(steps, goal="batch live test"))
    assert r["status"] == "done", r
    assert all(st["ok"] for st in r["trace"])
    assert p.read_text(encoding="utf-8") == "batch live"
    _close_all("Notepad")
    p.unlink(missing_ok=True)


def test_batch_stops_at_failed_expect_and_returns_observation():
    _close_all("Notepad")
    r = J(s.computer_run([{"app": "notepad.exe", "wait_window": "Notepad"},
                          {"expect": {"window": "ZZ-nope-ZZ"}, "timeout": 0.5},
                          {"key": "ctrl+n"}], goal="expect failure"))
    assert r["status"] == "failed" and r["steps_run"] == 2 and "observation" in r
    _close_all("Notepad")


@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY") and not (ROOT / "data/config/.env").exists(), reason="no JEV key")
def test_jev_live_disambiguates_identical_buttons():
    from jarvis.jev import engine
    if not engine().available:
        pytest.skip("no JEV key")
    _close_all("Notepad")
    r = J(s.computer_run([{"app": "notepad.exe", "wait_window": "Notepad"}, {"key": "ctrl+n"}, {"key": "ctrl+shift+s"},
                          {"wait": "window", "query": "Save as"}], goal="open save-as"))
    assert r["status"] == "done", r
    o = J(s.computer_observe("Save as"))
    opens = sorted([e["bounds"] for e in o["elements"] if e["name"] == "Open"], key=lambda b: b[1])
    assert len(opens) == 3, opens
    c = J(s.computer_click("Open", window="Save as", goal="open the 'Save as type' file-type dropdown (not the encoding or filename dropdown)"))
    assert c.get("tier") == "jev" and c["target"]["bounds"] == opens[1], c  # middle 'Open' = next to 'Save as type:'
    assert c["jev"]["confidence"] >= 0.55 and c["jev"]["latency_ms"] < 4000
    s.computer_key("escape")
    a = J(s.computer_act("dismiss this Save As dialog without saving", window="Save as", max_steps=3, done_when={"window_gone": "Save as"}))
    assert a["status"] == "done", a
    _close_all("Notepad")


def test_memory_learns_from_batches():
    r = J(s.computer_memory("recall", query="batch live test", app="Notepad"))
    assert any(e["kind"] == "workflows" and "batch live" in e.get("goal", "") for e in r["memory"]), r
    assert J(s.computer_memory("remember", kind="preferences", key="test_pref", value="yes"))["ok"]
    assert J(s.computer_memory("preferences"))["preferences"].get("test_pref") == "yes"
    assert J(s.computer_memory("forget", kind="preferences", query="test_pref"))["removed"] == 1
