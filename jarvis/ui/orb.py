"""The JARVIS orb — a transparent, always-on-top, non-activating desktop object.

Rendering: WebView2 *composition controller* on a WS_EX_NOREDIRECTIONBITMAP window (jarvis/ui/comphost.py) →
true per-pixel alpha composited by the DWM; the page (jarvis/ui/web/orb.html) draws the thinking-orbs core, the glow,
the glass HUD and the menu. Nothing opaque exists outside what the page paints.

Desktop behaviour
  * anchored TOP-CENTER of the PRIMARY monitor's work area (Dynamic Island), DPI-aware (CSS px × monitor scale,
    placed in physical px); a 1.5 s watchdog re-anchors on work-area/DPI changes (it never hides for full-screen apps);
  * WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE while collapsed (no focus steal, no taskbar button);
    activation makes the window activatable and focuses it so the HUD input can type; closing restores no-activate
    and hands focus back to the previously active window;
  * the window is larger than the orb (room for glow / HUD); the page reports its solid regions ("hit" rects) and
    WM_NCHITTEST answers HTTRANSPARENT everywhere else, so the desktop stays fully usable around the orb;
  * the window rect is the same in both modes (the HUD materialises inside it), so activation is pure motion — no
    window resize, no flicker.
Python → page: `push(**msg)` (queued; drained on the UI thread each idle tick). Page → Python: JSON web messages.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import queue
import threading
import time
from pathlib import Path

from .. import config
from .comphost import CompositionHost, u32

log = logging.getLogger("jarvis.orb")

TITLE = "JARVIS Orb"
HTML = Path(__file__).resolve().parent / "web" / "orb.html"
WIN = (720, 620)      # CSS px — one window size for both modes; the island lives TOP-CENTER inside it and grows DOWN
MARGIN = 8            # CSS px gap from the work-area top edge (the page keeps its own inset)
WM_NCHITTEST, HTTRANSPARENT = 0x84, -1
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4


def primary_work_area() -> dict:
    from ..computer.engine import displays
    mons = displays.list_monitors()
    m = next((x for x in mons if x.get("primary")), mons[0] if mons else None)
    if not m:
        return {"left": 0, "top": 0, "width": 1920, "height": 1040, "scale": 1.0, "monitor": {"left": 0, "top": 0, "width": 1920, "height": 1080}}
    wa = m.get("work_area") or {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
    return {**wa, "scale": float(m.get("scale") or 1.0), "monitor": {k: m[k] for k in ("left", "top", "width", "height")}}


def anchor_rect(active: bool = False, wa: dict | None = None) -> tuple[int, int, int, int]:
    """(x, y, w, h) physical px: TOP-CENTER of the work area (Dynamic Island). Same rect in both modes — the island
    pill sits at the top-center of the window and the glass panel materialises DOWNWARD inside it on activation."""
    wa = wa or primary_work_area()
    s = wa["scale"]
    w, h, m = int(round(WIN[0] * s)), int(round(WIN[1] * s)), int(round(MARGIN * s))
    x = wa["left"] + (wa["width"] - w) // 2
    return x, wa["top"] + m, w, h


class OrbWindow:
    def __init__(self, app):
        self.app = app
        self.host: CompositionHost | None = None
        self.hwnd = 0
        self.active = False
        self.hidden = False
        self._fullscreen_hidden = False
        self._q: queue.Queue = queue.Queue()
        self._js_ready = threading.Event()
        self._confirms: dict[str, tuple[threading.Event, dict]] = {}
        self._hit: list[tuple[int, int, int, int]] = []   # physical px, client coords
        self._last_wa = None
        self._prev_fg = 0
        self._quit = False

    # ---------------------------------------------------------------- lifecycle (main thread)
    def run(self):
        wa = primary_work_area()
        x, y, w, h = anchor_rect(False, wa)
        self._last_wa = (wa["left"], wa["top"], wa["width"], wa["height"], wa["scale"])
        self._scale = wa["scale"]
        self.host = CompositionHost(TITLE, HTML.as_uri(), x, y, w, h, wa["scale"], config.DATA / "webview2",
                                    on_message=self._on_message, on_web_message=self._on_web_message)
        self.hwnd = self.host.hwnd
        threading.Thread(target=self._watchdog, name="orb-watch", daemon=True).start()
        log.info("orb ready hwnd=%s rect=%s scale=%s", self.hwnd, (x, y, w, h), wa["scale"])
        self.host.run(idle=self._idle)

    def _idle(self):
        # drain pushes on the UI thread (PostWebMessage must run here); "_place" is an internal request
        n = 0
        while n < 50:
            try:
                msg = self._q.get_nowait()
            except queue.Empty:
                break
            n += 1
            try:
                if msg.get("_place"):
                    self.place()
                else:
                    self.host.post_json(msg)
            except Exception as e:  # noqa: BLE001
                log.debug("push failed: %s", e)
        if self._quit:
            self.host.close()

    def close(self):
        self._quit = True

    # ---------------------------------------------------------------- Win32 messages
    def is_solid(self, client_x: int, client_y: int) -> bool:
        """True if (physical client px) falls inside a painted region → the orb receives the click; else click-through."""
        for (l, t, r, b) in self._hit:
            if l <= client_x <= r and t <= client_y <= b:
                return True
        return False

    def _on_message(self, hwnd, msg, wp, lp):
        if msg == WM_NCHITTEST:
            pt = wt.POINT(ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
            u32.ScreenToClient(hwnd, ctypes.byref(pt))
            return None if self.is_solid(pt.x, pt.y) else HTTRANSPARENT
        return None

    # ---------------------------------------------------------------- page → Python
    def _on_web_message(self, raw: str):
        try:
            m = json.loads(raw)
        except Exception:  # noqa: BLE001
            return
        t = m.get("t")
        if t == "hit":
            # scale by the authoritative RasterizationScale Python set, NOT the page's devicePixelRatio
            # (WebView2's dpr can lag for a frame after load, which would misplace the hit rects → missed clicks)
            s = self._scale
            self._hit = [tuple(int(v * s) for v in r) for r in m.get("rects", [])]
        elif t == "ready":
            self._js_ready.set()
            self.app.on_orb_ready()
        elif t == "activate":
            self.set_active(True)
        elif t == "deactivate":
            self.set_active(False)
        elif t == "submit":
            self.app.submit(str(m.get("text", "")))
        elif t == "listen_toggle":
            self.app.toggle_listening()
        elif t == "stop":
            self.app.emergency_stop()
        elif t == "menu":
            self.app.orb_menu(str(m.get("action", "")))
        elif t == "history_nav":
            self.app.history_nav(int(m.get("dir", 0)))
        elif t == "confirm":
            self._confirm_answer(str(m.get("id")), bool(m.get("ok")))

    # ---------------------------------------------------------------- activation / placement
    def set_active(self, active: bool):
        self.active = bool(active)
        if not self.hwnd:
            return
        if self.active:
            self._prev_fg = u32.GetForegroundWindow()
            self.host.set_activatable(True)
            self.host.focus()
        else:
            self.host.set_activatable(False)
            if self._prev_fg and u32.IsWindow(self._prev_fg) and self._prev_fg != self.hwnd:
                try:
                    u32.SetForegroundWindow(self._prev_fg)
                except Exception:  # noqa: BLE001
                    pass
        self.app.on_orb_active(self.active)

    def place(self):
        if not self.host or self.hidden or self._fullscreen_hidden:
            return
        wa = primary_work_area()
        self._last_wa = (wa["left"], wa["top"], wa["width"], wa["height"], wa["scale"])
        x, y, w, h = anchor_rect(False, wa)
        self._scale = wa["scale"]
        self.host.set_rect(x, y, w, h)
        if abs(float(self.host.ctl.RasterizationScale) - wa["scale"]) > 0.01:
            self.host.ctl.RasterizationScale = float(wa["scale"])

    def hide(self):
        self.hidden = True
        if self.host:
            self.host.show(False)

    def show(self):
        self.hidden = False
        if self.host:
            self.host.show(True)
            self._q.put({"_place": True})

    def toggle_hidden(self):
        (self.show if self.hidden else self.hide)()

    def rect(self) -> tuple[int, int, int, int]:
        r = wt.RECT()
        u32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    # ---------------------------------------------------------------- watchdog (work area / DPI only)
    def _watchdog(self):
        # JARVIS is ALWAYS there: the orb is never hidden for full-screen apps / focus changes / inactivity.
        # It stays WS_EX_TOPMOST above ordinary windows. (A true exclusive-fullscreen DirectX game takes the whole
        # GPU output and can cover any topmost window — that is a Windows compositor limitation, not a hide by us;
        # switching such a game to borderless/windowed keeps the orb visible. Documented in docs/ORB_UI.md.)
        while not self._quit:
            time.sleep(1.5)
            if not self.hwnd:
                continue
            try:
                wa = primary_work_area()
                key = (wa["left"], wa["top"], wa["width"], wa["height"], wa["scale"])
                if key != self._last_wa and not self.hidden:
                    log.info("work area / DPI changed → re-anchoring orb %s", key)
                    self._last_wa = key
                    self._q.put({"_place": True})
                elif not self.hidden and not u32.IsWindowVisible(self.hwnd):
                    u32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                # keep the topmost band: some apps going full-screen re-order z; reassert topmost without activating
                elif not self.hidden:
                    u32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x10 | 0x2 | 0x1)  # HWND_TOPMOST, NOACTIVATE|NOMOVE|NOSIZE
            except Exception as e:  # noqa: BLE001
                log.debug("orb watchdog: %s", e)

    # ---------------------------------------------------------------- Python → page
    def push(self, **msg):
        self._q.put(msg)

    # ---------------------------------------------------------------- safety confirmation
    def confirm(self, title: str, message: str, timeout_s: int) -> bool:
        if not self._js_ready.is_set():
            raise RuntimeError("orb not ready")
        cid = str(time.time_ns())
        ev, box = threading.Event(), {"ok": False}
        self._confirms[cid] = (ev, box)
        self.push(confirm={"id": cid, "title": title, "text": message})
        ev.wait(timeout_s)
        self._confirms.pop(cid, None)
        return bool(box["ok"])

    def _confirm_answer(self, cid: str, ok: bool):
        item = self._confirms.get(cid)
        if item:
            item[1]["ok"] = ok
            item[0].set()
