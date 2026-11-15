"""System tray icon (pystray): status color, Listen, Stop, Show console, New conversation, Quit."""
from __future__ import annotations

import logging

from . import icon as _icon

log = logging.getLogger("jarvis.tray")


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = None
        self._images = {}

    def _img(self, status: str):
        if status not in self._images:
            self._images[status] = _icon.render(64, status)
        return self._images[status]

    def start(self):
        import pystray
        menu = pystray.Menu(
            pystray.MenuItem(lambda item: f"{self.app.brand} — {self.app.status}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show / hide orb", lambda: self.app.orb.toggle_hidden(), default=True),
            pystray.MenuItem("Show log window", lambda: self.app.show_console()),
            pystray.MenuItem(lambda item: "■ End conversation" if self.app.in_conversation else "🎤 Listen (hands-free)",
                             lambda: self.app.toggle_listening()),
            pystray.MenuItem("⛔ Emergency stop", lambda: self.app.emergency_stop()),
            pystray.MenuItem("Response mode", pystray.Menu(
                *[pystray.MenuItem(m, (lambda m=m: (lambda: self.app.set_mode(m)))(), checked=(lambda m=m: (lambda item: self._mode() == m))(), radio=True)
                  for m in ("silent", "concise", "conversational")])),
            pystray.MenuItem("New conversation", self._new_conversation),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open logs folder", self._open_logs),
            pystray.MenuItem("Quit", lambda: self.app.quit()),
        )
        self.icon = pystray.Icon(self.app.brand, self._img("idle"), f"{self.app.brand} — starting", menu)
        self.icon.run_detached()

    def _mode(self) -> str:
        from .. import config
        return str(config.SETTINGS.get("response_mode", "concise"))

    def _new_conversation(self):
        self.app.session.forget()
        self.app.say("system", "New conversation will start on next restart (session cleared).")

    def _open_logs(self):
        import os
        from .. import config
        os.startfile(str(config.LOGS))

    def set_status(self, status: str):
        if self.icon is None:
            return
        try:
            self.icon.icon = self._img(status if status in _icon.COLORS else "idle")
            self.icon.title = f"{self.app.brand} — {status}"
        except Exception as e:  # noqa: BLE001
            log.debug("tray update failed: %s", e)

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self.icon = None
