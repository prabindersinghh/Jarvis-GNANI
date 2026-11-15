"""Incident 2026-09-21: a batch scoped to 'WhatsApp' clicked a pixel and pasted text while the user had switched to
VS Code — the click landed in VS Code and the text was pasted there. Pixel clicks and focus-typing that name a window
must now bring that window forward and refuse (WrongWindow) if it cannot take the foreground."""
from __future__ import annotations

import pytest

from jarvis.orchestrator import router


class FakeWinfind:
    def __init__(self, windows, foreground, can_focus=True):
        self.windows, self._fg, self.can_focus = windows, foreground, can_focus
        self.focused = []

    def list_windows(self, visible_only=True):
        return list(self.windows)

    def find_windows(self, query):
        q = str(query).lower()
        return [w for w in self.windows if q in w["title"].lower() or q in w["process"].lower()]

    def foreground_window(self):
        return self._fg

    def foreground(self, hwnd):
        self.focused.append(hwnd)
        if self.can_focus:
            self._fg = hwnd
        return self.can_focus


WINS = [{"hwnd": 11, "title": "(256) WhatsApp - Google Chrome", "process": "chrome.exe"},
        {"hwnd": 22, "title": "JARVIS build directive - Visual Studio Code", "process": "Code.exe"}]


def test_ensure_window_noop_when_already_foreground(monkeypatch):
    wf = FakeWinfind(WINS, foreground=11)
    monkeypatch.setattr(router, "winfind", wf)
    assert router.ensure_window("WhatsApp")["hwnd"] == 11
    assert wf.focused == []  # never re-activates an already-front window (Chrome focus reset)


def test_ensure_window_brings_named_window_forward(monkeypatch):
    wf = FakeWinfind(WINS, foreground=22)
    monkeypatch.setattr(router, "winfind", wf)
    assert router.ensure_window("WhatsApp")["hwnd"] == 11
    assert wf.focused == [11]


def test_ensure_window_refuses_when_user_holds_foreground(monkeypatch):
    wf = FakeWinfind(WINS, foreground=22, can_focus=False)
    monkeypatch.setattr(router, "winfind", wf)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)
    with pytest.raises(router.WrongWindow) as ei:
        router.ensure_window("WhatsApp")
    assert "Visual Studio Code" in str(ei.value)


def test_ensure_window_unknown_window(monkeypatch):
    monkeypatch.setattr(router, "winfind", FakeWinfind(WINS, foreground=22))
    with pytest.raises(LookupError):
        router.ensure_window("Notepad")
    assert router.ensure_window(None) is None


def test_point_click_refuses_instead_of_clicking_other_window(monkeypatch):
    wf = FakeWinfind(WINS, foreground=22, can_focus=False)
    monkeypatch.setattr(router, "winfind", wf)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(router, "_click_point", lambda *a, **k: clicks.append(a))
    with pytest.raises(router.WrongWindow):
        router.execute_click({"x": 377, "y": 247}, window="WhatsApp", verify_change=False)
    assert clicks == []  # nothing was clicked


def test_focus_typing_refuses_instead_of_pasting_into_other_window(monkeypatch):
    wf = FakeWinfind(WINS, foreground=22, can_focus=False)
    monkeypatch.setattr(router, "winfind", wf)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)
    typed = []
    monkeypatch.setattr(router, "_type_via", lambda text, clip: typed.append(text) or "paste")
    with pytest.raises(router.WrongWindow):
        router.execute_type("Rihan", window="WhatsApp")
    assert typed == []  # nothing was typed
