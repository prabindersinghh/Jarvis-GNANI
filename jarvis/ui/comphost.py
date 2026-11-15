"""Composition-hosted WebView2 with TRUE per-pixel transparency — no WinForms, no pywebview.

Why: a WinForms-hosted WebView2 control renders into an opaque redirection surface, so no colour key / DWM trick
gives real alpha. WebView2's *composition controller* renders into a DirectComposition visual instead; on a window
created with WS_EX_NOREDIRECTIONBITMAP the DWM composites those visuals straight onto the desktop with alpha.
Everything used here is a signed Windows / WebView2 component (important under Smart App Control): user32, d3d11,
dcomp via ctypes; Microsoft.Web.WebView2.Core.dll (shipped with pywebview) via pythonnet.

Host responsibilities that WinForms used to cover:
  * a message loop (pump()) on this STA thread — WebView2 completes its async work through it;
  * forwarding mouse input (SendMouseInput) and focus (MoveFocus);
  * DPI: RasterizationScale = monitor scale, bounds in physical px.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("jarvis.comphost")

u32, k32, d3d11, dcomp = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.d3d11, ctypes.windll.dcomp

# ---------------------------------------------------------------- Win32 bits
WS_POPUP = 0x80000000
WS_EX_NOREDIRECTIONBITMAP, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00200000, 0x8, 0x80, 0x08000000
WM_DESTROY, WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP = 0x2, 0x200, 0x201, 0x202, 0x204, 0x205
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_LBUTTONDBLCLK, WM_MOUSEWHEEL, WM_MOUSELEAVE, WM_MOUSEACTIVATE = 0x207, 0x208, 0x203, 0x20A, 0x2A3, 0x21
WM_SETFOCUS, WM_KILLFOCUS, WM_ACTIVATE, WM_DPICHANGED, WM_SIZE, WM_MOVE = 0x7, 0x8, 0x6, 0x2E0, 0x5, 0x3
MA_NOACTIVATE = 3
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
u32.DefWindowProcW.restype = ctypes.c_longlong
u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.CreateWindowExW.restype = wt.HWND
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
u32.GetWindowLongPtrW.restype = ctypes.c_longlong
u32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
u32.SetWindowLongPtrW.restype = ctypes.c_longlong
u32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_longlong]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s: str):
        import uuid
        u = uuid.UUID(s)
        g = cls()
        g.d1, g.d2, g.d3 = u.time_low, u.time_mid, u.time_hi_version
        g.d4[:] = u.bytes[8:]
        return g


IID_IDXGIDevice = GUID.of("54ec77fa-1377-44e6-8c32-88fd5f44c84c")
IID_IDCompositionDevice = GUID.of("C37EA93A-E7AA-450D-B16F-9746CB0407F3")


def _vcall(obj: ctypes.c_void_p, index: int, restype, argtypes, *args):
    """Call a COM method by vtable slot: obj is an IUnknown* (c_void_p)."""
    vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(obj, *args)


class DComp:
    """D3D11 device → DXGI device → IDCompositionDevice; target for our HWND; one root visual (WebView2's parent)."""

    def __init__(self, hwnd: int):
        self.d3d = ctypes.c_void_p()
        hr = d3d11.D3D11CreateDevice(None, 1, None, 0x20, None, 0, 7, ctypes.byref(self.d3d), None, None)  # HARDWARE, BGRA_SUPPORT
        if hr < 0:
            hr = d3d11.D3D11CreateDevice(None, 5, None, 0x20, None, 0, 7, ctypes.byref(self.d3d), None, None)  # WARP fallback
        if hr < 0:
            raise OSError(f"D3D11CreateDevice failed 0x{hr & 0xFFFFFFFF:08X}")
        self.dxgi = ctypes.c_void_p()
        hr = _vcall(self.d3d, 0, ctypes.c_long, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(IID_IDXGIDevice), ctypes.byref(self.dxgi))
        if hr < 0:
            raise OSError("QueryInterface IDXGIDevice failed")
        self.device = ctypes.c_void_p()
        hr = dcomp.DCompositionCreateDevice(self.dxgi, ctypes.byref(IID_IDCompositionDevice), ctypes.byref(self.device))
        if hr < 0:
            raise OSError(f"DCompositionCreateDevice failed 0x{hr & 0xFFFFFFFF:08X}")
        self.target = ctypes.c_void_p()
        hr = _vcall(self.device, 6, ctypes.c_long, [wt.HWND, wt.BOOL, ctypes.POINTER(ctypes.c_void_p)], hwnd, True, ctypes.byref(self.target))
        if hr < 0:
            raise OSError(f"CreateTargetForHwnd failed 0x{hr & 0xFFFFFFFF:08X}")
        self.visual = ctypes.c_void_p()
        hr = _vcall(self.device, 7, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(self.visual))
        if hr < 0:
            raise OSError("CreateVisual failed")
        hr = _vcall(self.target, 3, ctypes.c_long, [ctypes.c_void_p], self.visual)  # IDCompositionTarget::SetRoot
        if hr < 0:
            raise OSError("SetRoot failed")
        self.commit()

    def commit(self):
        _vcall(self.device, 3, ctypes.c_long, [])


class CompositionHost:
    """A transparent, frameless, topmost, non-activating window rendering a WebView2 page with per-pixel alpha."""

    def __init__(self, title: str, url: str, x: int, y: int, w: int, h: int, scale: float, user_data: Path,
                 on_message=None, on_web_message=None):
        import pythoncom
        pythoncom.CoInitialize()  # STA for WebView2
        import clr
        lib = Path(__file__).resolve().parents[2] / ".venv" / "Lib" / "site-packages" / "webview" / "lib"
        clr.AddReference(str(lib / "Microsoft.Web.WebView2.Core.dll"))
        clr.AddReference("System.Drawing")
        import System  # noqa
        from System import IntPtr, Uri  # noqa
        from System.Drawing import Color, Point, Rectangle  # noqa
        from System.Runtime.InteropServices import Marshal  # noqa
        from Microsoft.Web.WebView2.Core import (CoreWebView2Environment, CoreWebView2EnvironmentOptions,  # noqa
                                                 CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys,
                                                 CoreWebView2MoveFocusReason)
        self._S = System
        self._Point, self._Rect, self._Color = Point, Rectangle, Color
        self._MouseKind, self._MouseKeys, self._FocusReason = CoreWebView2MouseEventKind, CoreWebView2MouseEventVirtualKeys, CoreWebView2MoveFocusReason
        self.on_message = on_message          # (hwnd, msg, wparam, lparam) -> LRESULT or None (default handling)
        self.on_web_message = on_web_message  # (str) from window.chrome.webview.postMessage
        self.title, self.scale = title, scale
        self.x, self.y, self.w, self.h = x, y, w, h
        self._proc = WNDPROC(self._wndproc)
        self.hwnd = self._create_window()
        self.dcomp = DComp(self.hwnd)
        opts = CoreWebView2EnvironmentOptions()
        opts.AdditionalBrowserArguments = "--disable-features=msWebOOUI,msPdfOOUI --enable-features=OverlayScrollbar"
        env_task = CoreWebView2Environment.CreateAsync(None, str(user_data), opts)
        self._wait(env_task)
        self.env = env_task.Result
        ctl_task = self.env.CreateCoreWebView2CompositionControllerAsync(IntPtr(self.hwnd))
        self._wait(ctl_task)
        self.ctl = ctl_task.Result
        self.ctl.RootVisualTarget = Marshal.GetObjectForIUnknown(IntPtr(self.dcomp.visual.value))
        self.ctl.DefaultBackgroundColor = Color.Transparent
        self.ctl.RasterizationScale = float(scale)
        self.ctl.ShouldDetectMonitorScaleChanges = False
        self.ctl.Bounds = Rectangle(0, 0, w, h)
        self.ctl.IsVisible = True
        self.web = self.ctl.CoreWebView2
        st = self.web.Settings
        st.AreDefaultContextMenusEnabled = False
        st.IsZoomControlEnabled = False
        st.AreDevToolsEnabled = bool(os.environ.get("JARVIS_ORB_DEVTOOLS"))
        st.IsStatusBarEnabled = False
        st.AreBrowserAcceleratorKeysEnabled = False
        self._hook_web_message()
        self.dcomp.commit()
        self.web.Navigate(url)
        self._mouse_inside = False
        log.info("composition host ready hwnd=%s", self.hwnd)

    # ---------------------------------------------------------------- window
    def _create_window(self) -> int:
        hinst = k32.GetModuleHandleW(None)
        cls = WNDCLASSEXW()
        cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
        cls.style = 0x0008 | 0x0002 | 0x0001  # CS_DBLCLKS | CS_HREDRAW | CS_VREDRAW
        cls.lpfnWndProc = self._proc
        cls.hInstance = hinst
        cls.hCursor = u32.LoadCursorW(None, 32512)
        cls.lpszClassName = "JarvisOrbHost"
        u32.RegisterClassExW(ctypes.byref(cls))  # may already exist
        ex = WS_EX_NOREDIRECTIONBITMAP | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        hwnd = u32.CreateWindowExW(ex, "JarvisOrbHost", self.title, WS_POPUP, self.x, self.y, self.w, self.h, None, None, hinst, None)
        if not hwnd:
            raise ctypes.WinError()
        u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        return hwnd

    def apply_backdrop(self, kind: int = 3) -> int:
        """§18 investigation: try a native Windows 11 DWM system backdrop (Mica/Acrylic) on this window.
        DWMWA_SYSTEMBACKDROP_TYPE=38; kind: 1=auto 2=Mica 3=Acrylic(transient) 4=Tabbed. Returns the HRESULT.
        NOTE (documented finding): DWM system backdrops require a DWM-redirected client area; this window is
        WS_EX_NOREDIRECTIONBITMAP + a DirectComposition visual (for true per-pixel alpha / the floating orb glow with
        no rectangle), so DWM has no redirection surface to draw the backdrop under — the call succeeds but no blur
        appears. Per-pixel-alpha translucency (already see-through) is kept as the higher-value identity choice."""
        try:
            dwm = ctypes.windll.dwmapi
            val = ctypes.c_int(kind)
            return dwm.DwmSetWindowAttribute(self.hwnd, 38, ctypes.byref(val), ctypes.sizeof(val))
        except Exception as e:  # noqa: BLE001
            log.debug("apply_backdrop: %s", e)
            return -1

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if self.on_message:
                r = self.on_message(hwnd, msg, wp, lp)
                if r is not None:
                    return int(r)
            if msg == WM_MOUSEACTIVATE and (u32.GetWindowLongPtrW(hwnd, -20) & WS_EX_NOACTIVATE):
                return MA_NOACTIVATE
            if msg == 0x20 and getattr(self, "ctl", None) is not None:  # WM_SETCURSOR → the page's cursor (hand over the orb, etc.)
                try:
                    u32.SetCursor(u32.LoadCursorW(None, int(self.ctl.SystemCursorId) or 32512))
                    return 1
                except Exception:  # noqa: BLE001
                    pass
            if WM_MOUSEMOVE <= msg <= WM_MOUSEWHEEL or msg == WM_MOUSELEAVE:
                self._forward_mouse(msg, wp, lp)
                return 0
            if msg == WM_SETFOCUS and getattr(self, "ctl", None) is not None:
                self.ctl.MoveFocus(self._FocusReason.Programmatic)
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
        except Exception as e:  # noqa: BLE001
            log.debug("wndproc %s: %s", msg, e)
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    def _forward_mouse(self, msg, wp, lp):
        if getattr(self, "ctl", None) is None:
            return
        x = ctypes.c_short(lp & 0xFFFF).value
        y = ctypes.c_short((lp >> 16) & 0xFFFF).value
        keys = self._MouseKeys(int(wp & 0xFFFF)) if msg != WM_MOUSEWHEEL else self._MouseKeys(int(wp & 0xFFFF))
        data = 0
        if msg == WM_MOUSEWHEEL:
            data = ctypes.c_short((wp >> 16) & 0xFFFF).value & 0xFFFFFFFF
            # wheel gives screen coords → client
            pt = wt.POINT(x, y)
            u32.ScreenToClient(self.hwnd, ctypes.byref(pt))
            x, y = pt.x, pt.y
        if msg == WM_MOUSEMOVE and not self._mouse_inside:
            self._mouse_inside = True

            class TME(ctypes.Structure):
                _fields_ = [("cbSize", wt.DWORD), ("dwFlags", wt.DWORD), ("hwndTrack", wt.HWND), ("dwHoverTime", wt.DWORD)]
            t = TME(ctypes.sizeof(TME), 0x2, self.hwnd, 0)  # TME_LEAVE
            u32.TrackMouseEvent(ctypes.byref(t))
        if msg == WM_MOUSELEAVE:
            self._mouse_inside = False
        kind = self._S.Enum.ToObject(self._MouseKind, int(msg))
        self.ctl.SendMouseInput(kind, keys, self._S.UInt32(data), self._Point(int(x), int(y)))

    # ---------------------------------------------------------------- bridge
    def _hook_web_message(self):
        import System

        def handler(sender, args):
            try:
                if self.on_web_message:
                    self.on_web_message(args.TryGetWebMessageAsString())
            except Exception as e:  # noqa: BLE001
                log.debug("web message: %s", e)
        from Microsoft.Web.WebView2.Core import CoreWebView2WebMessageReceivedEventArgs
        self._wm_handler = System.EventHandler[CoreWebView2WebMessageReceivedEventArgs](handler)
        self.web.WebMessageReceived += self._wm_handler

    def eval_js(self, script: str):
        """Fire-and-forget script execution (result ignored)."""
        try:
            self.web.ExecuteScriptAsync(script)
        except Exception as e:  # noqa: BLE001
            log.debug("eval_js: %s", e)

    def post_json(self, obj) -> None:
        import json
        self.web.PostWebMessageAsJson(json.dumps(obj, ensure_ascii=False))

    # ---------------------------------------------------------------- geometry / focus
    def set_rect(self, x: int, y: int, w: int, h: int):
        self.x, self.y, self.w, self.h = x, y, w, h
        u32.SetWindowPos(self.hwnd, -1, x, y, w, h, 0x10 | 0x40 | 0x4)  # NOACTIVATE|SHOWWINDOW|NOZORDER
        self.ctl.Bounds = self._Rect(0, 0, w, h)
        self.ctl.NotifyParentWindowPositionChanged()
        self.dcomp.commit()

    def set_activatable(self, on: bool):
        ex = u32.GetWindowLongPtrW(self.hwnd, -20)
        ex = (ex & ~WS_EX_NOACTIVATE) if on else (ex | WS_EX_NOACTIVATE)
        u32.SetWindowLongPtrW(self.hwnd, -20, ex)

    def focus(self):
        try:
            fg = u32.GetForegroundWindow()
            cur = k32.GetCurrentThreadId()
            tid = u32.GetWindowThreadProcessId(fg, None) if fg else 0
            if tid and tid != cur:
                u32.AttachThreadInput(tid, cur, True)
            u32.SetForegroundWindow(self.hwnd)
            u32.SetFocus(self.hwnd)
            if tid and tid != cur:
                u32.AttachThreadInput(tid, cur, False)
            self.ctl.MoveFocus(self._FocusReason.Programmatic)
        except Exception as e:  # noqa: BLE001
            log.debug("focus: %s", e)

    def show(self, visible: bool):
        u32.ShowWindow(self.hwnd, 4 if visible else 0)
        self.ctl.IsVisible = bool(visible)

    # ---------------------------------------------------------------- message loop
    def _wait(self, task, timeout: float = 20.0):
        end = time.perf_counter() + timeout
        while not task.IsCompleted:
            self.pump(0.01)
            if time.perf_counter() > end:
                raise TimeoutError("WebView2 async operation timed out")
        if task.IsFaulted:
            raise RuntimeError(str(task.Exception))

    def pump(self, wait_s: float = 0.0) -> bool:
        """Process pending window messages; returns False when WM_QUIT was seen."""
        msg = wt.MSG()
        if wait_s > 0:
            u32.MsgWaitForMultipleObjects(0, None, False, int(wait_s * 1000), 0x04FF)  # QS_ALLINPUT
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == 0x12:  # WM_QUIT
                return False
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        return True

    def run(self, idle=None):
        """Blocking loop until the window is destroyed. `idle()` is called ~every 20 ms."""
        while self.pump(0.02):
            if idle:
                idle()

    def close(self):
        try:
            self.ctl.Close()
        except Exception:  # noqa: BLE001
            pass
        u32.DestroyWindow(self.hwnd)
