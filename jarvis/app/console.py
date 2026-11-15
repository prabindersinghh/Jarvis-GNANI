"""Console window (tkinter, ships with Python — no extra runtime). Shows status, transcript, streaming replies,
tool activity, a mic level bar, and a text box for typed commands. Closing the window hides it to the tray."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from .. import config
from . import icon as _icon

BG, FG, DIM, ACC = "#0b1220", "#dbe7f5", "#7f93ad", "#00c8ff"
STATUS_TXT = {
    "starting": "Starting…", "connecting": "Connecting to Claude Code…", "idle": "Ready", "listening": "● Listening",
    "transcribing": "Transcribing…", "thinking": "Thinking…", "acting": "Acting…", "error": "Error", "off": "Off",
}


class Console:
    def __init__(self, app):
        self.app = app
        self.root = tk.Tk()
        self.root.title(f"{app.brand} Console")
        self.root.configure(bg=BG)
        self.root.geometry("760x560+120+120")
        self.root.minsize(520, 360)
        try:
            ico = config.ROOT / "launcher" / "jarvis.ico"
            if not ico.exists():
                _icon.write_ico(ico)
            self.root.iconbitmap(str(ico))
        except Exception:  # noqa: BLE001
            pass
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._assistant_open = False
        self.root.after(60, self._pump)

    def _build(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        self.status_dot = tk.Canvas(top, width=14, height=14, bg=BG, highlightthickness=0)
        self.status_dot.pack(side="left")
        self._dot = self.status_dot.create_oval(2, 2, 12, 12, fill=ACC, outline="")
        self.status_lbl = tk.Label(top, text="Starting…", fg=FG, bg=BG, font=("Segoe UI", 11, "bold"))
        self.status_lbl.pack(side="left", padx=8)
        self.level = ttk.Progressbar(top, length=120, maximum=100, mode="determinate")
        self.level.pack(side="right")
        tk.Label(top, text=f"PTT {config.SETTINGS['hotkey']}", fg=DIM, bg=BG, font=("Segoe UI", 9)).pack(side="right", padx=10)
        self.mode_var = tk.StringVar(value=str(config.SETTINGS.get("response_mode", "concise")))
        mode = ttk.Combobox(top, textvariable=self.mode_var, values=["silent", "concise", "conversational"], width=13, state="readonly")
        mode.pack(side="right", padx=6)
        mode.bind("<<ComboboxSelected>>", lambda e: self.app.set_mode(self.mode_var.get()))

        self.text = tk.Text(self.root, bg="#0f1a2e", fg=FG, insertbackground=FG, wrap="word", relief="flat",
                            font=("Segoe UI", 10), padx=10, pady=8, state="disabled")
        self.text.pack(fill="both", expand=True, padx=12, pady=4)
        self.text.tag_config("user", foreground="#ffd166", font=("Segoe UI", 10, "bold"))
        self.text.tag_config("assistant", foreground=FG)
        self.text.tag_config("tool", foreground="#7fdc9a", font=("Consolas", 9))
        self.text.tag_config("meta", foreground=DIM, font=("Segoe UI", 8))
        self.text.tag_config("system", foreground=ACC, font=("Segoe UI", 9, "italic"))
        self.text.tag_config("error", foreground="#ff6b6b")

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=12, pady=(4, 12))
        self.entry = tk.Entry(bottom, bg="#0f1a2e", fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda e: self._send())
        tk.Button(bottom, text="Send", command=self._send, bg="#16305a", fg=FG, relief="flat", padx=12).pack(side="left", padx=(8, 0))
        self.listen_btn = tk.Button(bottom, text="🎤 Listen", command=self.app.toggle_listening, bg="#16305a", fg=FG, relief="flat", padx=10)
        self.listen_btn.pack(side="left", padx=(8, 0))
        tk.Button(bottom, text="⛔ Stop", command=self.app.emergency_stop, bg="#5a1616", fg=FG, relief="flat", padx=10).pack(side="left", padx=(8, 0))

    # ---------------------------------------------------------------- API used from other threads (via queue)
    def _pump(self):
        try:
            while True:
                kind, payload = self.app.ui_q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        if not self.app._quit.is_set():
            self.root.after(50, self._pump)

    def _handle(self, kind, payload):
        if kind == "status":
            txt = STATUS_TXT.get(payload, payload)
            if payload == "listening" and self.app.in_conversation:
                txt += " (hands-free)"
            self.status_lbl.config(text=txt)
            self.status_dot.itemconfig(self._dot, fill="#%02x%02x%02x" % _icon.COLORS.get(payload, _icon.COLORS["idle"]))
            self._update_listen_btn(payload)
            return
        if kind == "conversation":
            self._update_listen_btn(self.app.status)
            return
        if kind == "level":
            self.level["value"] = min(100, float(payload) * 800)
            return
        self.text.config(state="normal")
        if kind == "assistant_delta":
            if not self._assistant_open:
                self.text.insert("end", f"\n{self.app.brand}: ", "assistant")
                self._assistant_open = True
            self.text.insert("end", payload, "assistant")
        else:
            self._assistant_open = False
            prefix = {"user": "\n\nYou: ", "tool": "\n", "meta": "\n", "system": "\n", "error": "\n⚠ "}.get(kind, "\n")
            self.text.insert("end", prefix + str(payload), kind)
        self.text.see("end")
        self.text.config(state="disabled")

    def _update_listen_btn(self, status: str):
        if self.app.in_conversation:
            self.listen_btn.config(text="■ End conversation", bg="#5a3a16")
        elif status == "listening":
            self.listen_btn.config(text="■ Stop listening", bg="#16305a")
        else:
            self.listen_btn.config(text="🎤 Listen", bg="#16305a")

    def _send(self):
        t = self.entry.get().strip()
        if t:
            self.entry.delete(0, "end")
            self.app.submit(t)

    def confirm_dialog(self, title: str, message: str, timeout_s: int) -> bool:
        """Runs on any thread; marshals a modal yes/no onto the Tk thread. Times out → False."""
        result = {"ok": False}
        done = threading.Event()

        def _ask():
            try:
                from tkinter import messagebox
                self.show()
                result["ok"] = bool(messagebox.askyesno(title, message, icon="warning", parent=self.root))
            finally:
                done.set()

        self.root.after(0, _ask)
        done.wait(timeout_s)
        return result["ok"]

    def show(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
        except Exception:  # noqa: BLE001
            pass

    def hide(self):
        self.root.withdraw()

    def close(self):
        try:
            self.root.after(0, self.root.destroy)
        except Exception:  # noqa: BLE001
            pass

    def run(self):
        self.root.mainloop()
