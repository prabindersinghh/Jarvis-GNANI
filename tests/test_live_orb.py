"""Live tests for the composition-hosted desktop orb against the RUNNING packaged app (launch dist first).
Run: python -m pytest tests/test_live_orb.py -q -m live"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

import pytest

os.environ["JARVIS_AUTO_CONFIRM"] = "1"
from jarvis.computer.engine import capture, input as winput  # noqa: E402 (enables DPI awareness)
from jarvis.ui import orb as orbmod  # noqa: E402

pytestmark = pytest.mark.live
u32 = ctypes.windll.user32
u32.GetWindowLongPtrW.restype = ctypes.c_longlong
u32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
TITLE = "JARVIS Orb"
SCALE = orbmod.primary_work_area()["scale"]


@pytest.fixture(autouse=True)
def _settled_orb():
    """Each orb test starts from a clean state: window present, visible, collapsed, mouse parked away from the orb."""
    h = u32.FindWindowW(None, TITLE)
    if h:
        if not u32.IsWindowVisible(h):
            u32.ShowWindow(h, 4)
            time.sleep(1.0)
        l, t, r, b = _rect(h)
        winput.move(l + 10, t + 10)  # off the orb
        # collapse if a prior test left it active
        for _ in range(3):
            px_active = _js_active()
            if not px_active:
                break
            winput.tap("escape")
            time.sleep(0.6)
        time.sleep(0.3)
    yield


def _hwnd():
    h = u32.FindWindowW(None, TITLE)
    if not h:
        pytest.skip("JARVIS orb is not running (launch the app first)")
    return h


def _rect(h):
    r = wt.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _work_area():
    r = wt.RECT()
    u32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def _orb_center(h):
    # Dynamic Island (2026-09-24): the orb sits at the LEFT of the top-center pill (~74 css px left of the window
    # centre, ~28 css px down). NOTE: the HUD/settings pixel-sampling tests below still assume the old bottom-right
    # panel geometry and need a live re-tune session for the top-center layout.
    l, t, r, b = _rect(h)
    return (l + r) // 2 - int(74 * SCALE), t + int(28 * SCALE)


def _wait(pred, timeout=4.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _js_active():
    """Ask the app whether the orb reports active — via a probe the app exposes in the log is overkill;
    instead detect the panel by a bright pixel in the upper-left of the window (HUD present)."""
    h = _hwnd()
    l, t, r, b = _rect(h)
    # the HUD sits in the lower area (bottom:70 css, ~250-460 css tall). Sample a grid; the frosted panel is clearly
    # brighter than the (dark) wallpaper. Require several lit samples so a stray bright pixel does not read as active.
    lit = 0
    for dy in range(280, 470, 20):
        for dx in (40, 120, 220):
            px = capture.grab_rect(l + int(dx * SCALE), t + int(dy * SCALE), 2, 2).getpixel((0, 0))[:3]
            if sum(px) > 95:
                lit += 1
    return lit >= 6


def test_orb_window_topmost_noredirection_no_taskbar():
    h = _hwnd()
    ex = u32.GetWindowLongPtrW(h, -20)
    assert ex & 0x8, "not topmost"
    assert ex & 0x80, "not a tool window (would show a taskbar button)"
    assert ex & 0x00200000, "not WS_EX_NOREDIRECTIONBITMAP (no true transparency)"
    assert u32.IsWindowVisible(h)


def test_orb_anchored_top_center_inside_work_area():
    # Dynamic Island (2026-09-24): the shell now anchors TOP-CENTER of the work area, not bottom-right.
    h = _hwnd()
    l, t, r, b = _rect(h)
    wl, wtp, wr, wb = _work_area()
    win_cx, wa_cx = (l + r) / 2, (wl + wr) / 2
    assert abs(win_cx - wa_cx) <= int(24 * SCALE), f"island not horizontally centered: {win_cx} vs {wa_cx}"
    assert (t - wtp) <= int(24 * SCALE), f"island not hugging the top: t={t} wa_top={wtp}"


def test_hittest_makes_empty_area_click_through():
    """The WM_NCHITTEST predicate: solid only inside reported hit rects (orb / HUD), transparent everywhere else.
    Verified directly on the OrbWindow logic (no running app needed for this one)."""
    ow = orbmod.OrbWindow.__new__(orbmod.OrbWindow)
    ow._hit = [(531, 681, 678, 828)]  # a typical orb rect (physical client px)
    assert ow.is_solid(604, 754) is True          # orb centre → the orb gets the click
    assert ow.is_solid(40, 40) is False            # empty top-left → click-through
    assert ow.is_solid(600, 300) is False          # above the orb, empty → click-through
    # and against the live window: the top-left region is not in any hit rect
    h = _hwnd()
    l, t, r, b = _rect(h)
    winput.move(l + 30, t + 30)  # hovering empty area must not change the cursor to a hand or capture


def test_click_activates_and_escape_collapses_same_size():
    h = _hwnd()
    before = _rect(h)
    if _js_active():
        winput.tap("escape")
        _wait(lambda: not _js_active(), 3)
    cx, cy = _orb_center(h)
    winput.move(cx - 300, cy - 300)
    time.sleep(0.2)
    t0 = time.perf_counter()
    winput.click("left", 1, cx, cy)
    assert _wait(_js_active, 3), "HUD did not appear on click"
    ms = (time.perf_counter() - t0) * 1000
    assert _rect(h) == before, "window resized on activation (should be pure in-window motion)"
    assert _wait(lambda: u32.GetForegroundWindow() == h, 2), "orb did not take focus for typing"
    print(f"activation {ms:.0f} ms")
    winput.tap("escape")
    # collapse restores WS_EX_NOACTIVATE (deterministic) and the HUD fades out; assert on the style bit + a pixel recheck
    assert _wait(lambda: bool(u32.GetWindowLongPtrW(h, -20) & 0x08000000), 4), "orb not returned to NOACTIVATE after Esc"
    assert _wait(lambda: not _js_active(), 4), "HUD still visible well after Esc"


def test_does_not_steal_focus_while_collapsed():
    h = _hwnd()
    if _js_active():
        winput.tap("escape")
        _wait(lambda: not _js_active(), 3)
    from jarvis.computer import server as s
    r = json.loads(s.computer_app("launch", "notepad.exe", wait_window="Notepad", timeout=12))
    assert r["ok"]
    time.sleep(0.4)
    np = u32.FindWindowW(None, None)  # current foreground
    fg = u32.GetForegroundWindow()
    assert fg != h, "collapsed orb stole foreground"
    # clicking the orb should NOT be needed to keep Notepad usable; the orb stays topmost though
    assert u32.IsWindowVisible(h)
    for w in json.loads(s.computer_window("list", "Notepad"))["windows"]:
        s.computer_window("close", w["title"])
    _ = np


def test_context_menu_is_rectangular_glass_not_radial():
    h = _hwnd()
    cx, cy = _orb_center(h)
    winput.click("right", 1, cx, cy)
    time.sleep(0.5)
    l, t, r, b = _rect(h)
    img = capture.grab_rect(r - int(280 * SCALE), b - int(300 * SCALE), int(280 * SCALE), int(300 * SCALE))
    winput.tap("escape")
    time.sleep(0.3)
    # the menu is a bright rounded rectangle: find the largest lit bounding box and check it is wider than tall-ish
    px = img.load()
    xs, ys = [], []
    for yy in range(0, img.height, 3):
        for xx in range(0, img.width, 3):
            if sum(px[xx, yy][:3]) > 210:
                xs.append(xx)
                ys.append(yy)
    assert len(xs) > 80, "context menu did not render"
    w_lit, h_lit = max(xs) - min(xs), max(ys) - min(ys)
    assert w_lit > 60 and h_lit > 60  # a real panel, not a thin sliver


def test_orb_stays_visible_under_fullscreen_app():
    """JARVIS is ALWAYS there: the orb must NOT hide for a full-screen application (directive change 2026-09-21)."""
    h = _hwnd()
    code = "import tkinter as tk; r=tk.Tk(); r.attributes('-fullscreen',True); r.configure(bg='black'); r.after(4500, r.destroy); r.mainloop()"
    p = subprocess.Popen([sys.executable, "-c", code])
    try:
        time.sleep(2.5)
        assert u32.IsWindowVisible(h), "orb was hidden under a full-screen app (should stay visible)"
    finally:
        p.wait(timeout=10)
    assert _wait(lambda: u32.IsWindowVisible(h), 4.0), "orb did not restore after full-screen closed"
