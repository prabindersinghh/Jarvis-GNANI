"""Configurable global push-to-talk hotkey (pynput). Hold = record while held; tap = toggle with VAD auto-stop."""
from __future__ import annotations

import logging
import threading
import time

from .. import config

log = logging.getLogger("jarvis.voice.ptt")


def parse_hotkey(spec: str):
    """'<ctrl>+<alt>+j' → set of pynput keys. Accepts pynput's HotKey syntax."""
    from pynput import keyboard
    return set(keyboard.HotKey.parse(spec))


class PushToTalk:
    """Calls on_press() when the combo goes down and on_release() when any key of it comes up.
    `tap_s` distinguishes a tap (toggle mode) from a hold."""

    def __init__(self, hotkey: str | None = None, on_press=None, on_release=None, tap_s: float = 0.35):
        self.spec = hotkey or config.SETTINGS["hotkey"]
        self.keys = parse_hotkey(self.spec)
        self.on_press = on_press
        self.on_release = on_release
        self.tap_s = tap_s
        self._down: set = set()
        self._active = False
        self._t_down = 0.0
        self._listener = None
        self._lock = threading.Lock()

    def _norm(self, key):
        from pynput import keyboard
        # canonicalize so ctrl_l/ctrl_r match <ctrl>
        if isinstance(key, keyboard.KeyCode) and self._listener is not None:
            return self._listener.canonical(key)
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            return keyboard.Key.ctrl
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
            return keyboard.Key.alt
        if key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            return keyboard.Key.shift
        if key in (keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            return keyboard.Key.cmd
        return key

    def _press(self, key):
        k = self._norm(key)
        with self._lock:
            self._down.add(k)
            if not self._active and self.keys <= self._down:
                self._active = True
                self._t_down = time.perf_counter()
                if self.on_press:
                    threading.Thread(target=self.on_press, daemon=True).start()

    def _release(self, key):
        k = self._norm(key)
        with self._lock:
            self._down.discard(k)
            if self._active and k in self.keys:
                self._active = False
                held = time.perf_counter() - self._t_down
                if self.on_release:
                    threading.Thread(target=self.on_release, args=(held < self.tap_s,), daemon=True).start()

    def start(self):
        from pynput import keyboard
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()
        log.info("push-to-talk hotkey: %s", self.spec)
        return self

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None
