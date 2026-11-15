"""JARVIS application: desktop orb (primary UI) + tray + optional log console + voice → Claude Code (brain) →
JARVIS MCP (hands). Orb visual state is driven ONLY by real runtime events (status, tool use, streaming, TTS, results).

Threads:
  main      the orb's WebView2 loop (pywebview must own the main thread); the tkinter console runs on demand in its own thread
  asyncio   ClaudeSession (claude-agent-sdk subprocess, streaming)
  voice     Recorder + Gnani Prisma (hotkey callbacks come from the pynput listener thread)
  tray      pystray icon (run_detached)
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from .. import config
from ..safety import confirm, policy
from . import icon as _icon
from .claude_session import ClaudeSession

log = logging.getLogger("jarvis.app")
STOP_HOTKEY = "<ctrl>+<alt>+x"


def _setup_logging():
    config.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(config.SETTINGS.get("log_level", "INFO")).upper(), logging.INFO))
    fh = RotatingFileHandler(config.LOGS / "jarvis.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(sh)


def _single_instance(name: str) -> bool:
    """True if we are the first instance. Otherwise bring the existing console to the front and return False."""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, f"Local\\{name}-singleton")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        u32 = ctypes.windll.user32
        hwnd = u32.FindWindowW(None, f"{name} Console")
        if hwnd:
            u32.ShowWindow(hwnd, 9)
            u32.SetForegroundWindow(hwnd)
        return False
    return True


class JarvisApp:
    def __init__(self):
        self.brand = config.SETTINGS.get("brand", "JARVIS")
        self.status = "starting"
        self.ui_q: queue.Queue = queue.Queue(maxsize=2000)   # (kind, payload) → optional log console
        self.prompt_q: asyncio.Queue | None = None  # user turns → Claude loop
        self.loop: asyncio.AbstractEventLoop | None = None
        self.session = ClaudeSession(on_text=self._on_text, on_event=self._on_event, on_status=self._on_claude_status)
        self.tray = None
        self.console = None
        self.recorder = None
        self.prisma = None
        self.tts = None
        self.speaker = None                 # StreamingSpeaker: clause-by-clause TTS + resume decision (§12)
        self._ack_spoken_this_turn = False  # a local ack ("On it.") was spoken before Claude replied (§9)
        self.ptt = None
        self.stop_hotkey = None
        self._listening = False
        self._voice_lock = threading.Lock()
        # Hands-free conversation: set by the Listen button. While set, JARVIS re-arms the mic after every turn
        # (after it finishes speaking) so the user never has to press Listen again. Cleared by the button/Stop.
        self._conversation = threading.Event()
        self._resume_after_tts = False
        self._quit = threading.Event()
        self.orb = None
        self._orb_state = "starting"
        self._responding = False
        self._flash_timer = None
        self._last_status_push = {}
        # daily conversation history (one date = one conversation; sessions are markers within the day)
        from ..history import history
        self.history = history()
        self.learning = None               # perpetual-learning manager (installed in run())
        self.graph = None                  # local private memory graph (installed in run())
        self.tasks = None                  # durable task engine + follow-up queue + receipts (installed in run())
        self.notify = None                 # local notifications (installed in run())
        self.watches = None                # recurring watches (installed in run())
        self._bus = None                   # normalized runtime event stream (AG-UI-compatible)
        self._task_id = None               # id of the task for the turn in flight
        self._turn_had_tool_error = False  # did any tool this turn report an error? (real verification signal)
        self._last_src = "text"            # source of the pending user turn (voice|text) for the DB
        self._browse_date = None           # None = today; set when the user pages back through history in the HUD

    # ---------------------------------------------------------------- status/UI plumbing
    def set_status(self, s: str):
        self.status = s
        self._uiq(("status", s))
        if self.tray:
            self.tray.set_status(s)
        self._orb_set(s)

    # orb state = the real runtime status; "responding"/"speaking"/"success"/"error" are short-lived overlays
    def _orb_set(self, state: str, label: str | None = None):
        self._orb_state = state
        if self.orb is not None:
            self.orb.push(state=state, label=label)
        self._sync_bargein()          # keep voice barge-in armed whenever JARVIS is busy (§10/§13)

    def _sync_bargein(self):
        """Arm/disarm voice barge-in from JARVIS's real busy-state. While busy — thinking, working a tool, OR
        speaking — the mic monitors for the user cutting in, so they can interrupt or guide him mid-task at ANY time
        (not only while he is speaking). While actively listening we are already capturing them, so the monitor is off.
        Net effect: from the moment a conversation starts, JARVIS can always hear the user — there is no deaf window."""
        if self.recorder is None or not config.SETTINGS.get("bargein_enabled", True):
            return
        busy = (self._orb_state in ("thinking", "responding", "acting", "searching", "speaking")
                or (self.tts is not None and self.tts.speaking.is_set()))
        want = busy and not self._listening and config.SETTINGS.get("bargein_while_busy", True)
        try:
            self.recorder.set_monitor(want)
        except Exception:  # noqa: BLE001
            pass

    def _orb_flash(self, state: str, seconds: float):
        self._orb_set(state)
        if self._flash_timer:
            self._flash_timer.cancel()
        self._flash_timer = threading.Timer(seconds, lambda: self._orb_set("idle" if self.status in ("idle", "error") else self.status))
        self._flash_timer.daemon = True
        self._flash_timer.start()

    def _uiq(self, item):
        try:
            self.ui_q.put_nowait(item)
        except queue.Full:
            try:
                self.ui_q.get_nowait()
                self.ui_q.put_nowait(item)
            except Exception:  # noqa: BLE001
                pass

    def say(self, kind: str, text: str):
        self._uiq((kind, text))
        if self.orb is None:
            return
        if kind == "user":
            self.orb.push(user=text, activity="")
        elif kind == "system":
            self.orb.push(activity=text.splitlines()[0][:110] if text else "")  # ephemeral hint, not transcript
        elif kind == "error":
            self.orb.push(error=text)
        elif kind == "tool":
            self.orb.push(activity=text.lstrip("▸ ").strip())

    def _on_text(self, delta: str):
        self._uiq(("assistant_delta", delta))
        if not self._responding:
            self._responding = True
            self._orb_set("responding")
        if self.orb is not None and self._browse_date is None:  # don't stream into a past-date view
            self.orb.push(delta=delta)
        if self.speaker is not None:  # speak clause-by-clause as the reply streams (§12)
            self.speaker.on_text(delta)

    SEARCH_TOOLS = ("search", "fetch", "find", "ocr", "screen", "grep", "glob")

    # ---------------------------------------------------------------- daily conversation persistence
    def _record_user(self, text: str):
        """Append a user turn to today's conversation and show it live (unless browsing a past date)."""
        at = time.strftime("%H:%M")
        self._turn_src = self._last_src
        self._last_user_text = text
        try:
            self.history.append("user", text, source=self._last_src)
        except Exception as e:  # noqa: BLE001
            log.debug("history user append failed: %s", e)
        self._last_src = "text"
        self._uiq(("user", text))
        if self.orb is not None and self._browse_date is None:
            self.orb.push(user=text, at=at)
        if self.speaker is not None:
            keep = self._ack_spoken_this_turn      # a local ack is playing → the first streamed chunk must not purge it
            self._ack_spoken_this_turn = False
            self.speaker.begin_turn(keep_prefix=keep)

    def _maybe_ack(self, text: str):
        """Speak an immediate local acknowledgement for longer tasks — no Claude round-trip (§9). Short deterministic
        commands stay silent and just execute, so JARVIS never becomes chatty (§30)."""
        self._ack_spoken_this_turn = False
        if not config.SETTINGS.get("ack_enabled", True) or self.tts is None or self.tts.mode == "silent":
            return
        try:
            from ..voice.ack import ack_for
            phrase = ack_for(text)
            if phrase and self.tts.speak(phrase):
                self._ack_spoken_this_turn = True
        except Exception as e:  # noqa: BLE001
            log.debug("ack failed: %s", e)

    def _commit_assistant(self, text: str) -> str:
        at = time.strftime("%H:%M")
        self.history.append("jarvis", text, source=getattr(self, "_turn_src", "text"))
        return at

    def _push_transcript(self, d: str | None = None):
        if self.orb is None:
            return
        try:
            tl = self.history.timeline(d)
            dates = [x["date"] for x in self.history.dates()]
            cur = tl["date"]
            idx = dates.index(cur) if cur in dates else 0
            tl["can_prev"] = idx < len(dates) - 1        # dates are newest-first; "prev" = older
            tl["can_next"] = idx > 0
            self.orb.push(transcript=tl)
        except Exception as e:  # noqa: BLE001
            log.debug("push transcript failed: %s", e)

    def history_nav(self, direction: int):
        """Page through past days in the HUD. direction -1 = older, +1 = newer. Newest = today (live)."""
        try:
            dates = [x["date"] for x in self.history.dates()]
            today = time.strftime("%Y-%m-%d")
            if today not in dates:
                dates = [today] + dates
            cur = self._browse_date or today
            idx = dates.index(cur) if cur in dates else 0
            idx = max(0, min(len(dates) - 1, idx + (1 if direction < 0 else -1)))  # older = higher index
            self._browse_date = None if dates[idx] == today else dates[idx]
            self._push_transcript(self._browse_date)
        except Exception as e:  # noqa: BLE001
            log.debug("history_nav failed: %s", e)

    def _on_event(self, kind: str, payload: dict):
        if kind == "tool_use":
            name = payload["name"].replace("mcp__jarvis__", "")
            args = payload.get("input") or {}
            brief = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in list(args.items())[:3])
            self.say("tool", f"▸ {name}({brief})")
            self._responding = False
            self._orb_set("searching" if any(k in name.lower() for k in self.SEARCH_TOOLS) else "acting")
            self._emit("TOOL_CALL_START", task_id=self._task_id, name=name)
        elif kind == "tool_result" and payload.get("is_error"):
            self._turn_had_tool_error = True     # real verification signal for the task receipt (§11)
            self.say("tool", f"  ✗ {payload.get('content', '')[:160]}")
            self._emit("TOOL_CALL_RESULT", task_id=self._task_id, is_error=True)
        elif kind == "result":
            ms = payload.get("claude_ms")
            self._uiq(("meta", f"— {payload.get('num_turns')} turns · {ms} ms" + (" · ERROR" if payload.get("is_error") else "")))
            self._log_metric("claude_ms", ms)
            self._responding = False
            self._orb_flash("error" if payload.get("is_error") else "success", 3.0 if payload.get("is_error") else 1.4)
            if self.orb is not None:
                self.orb.push(activity="")
            self.push_runtime_status()
            self._record_graph(not payload.get("is_error"))
            self._finish_task(payload)          # complete/fail the durable task + receipt, then advance the queue
            final = payload.get("result") or ""
            # persist the assistant turn ONCE (committing the streamed message; never a second entry when it is spoken)
            if final and not payload.get("is_error"):
                try:
                    at = self._commit_assistant(final)
                    if self.orb is not None and self._browse_date is None:
                        self.orb.push(commit={"text": final, "at": at})
                except Exception as e:  # noqa: BLE001
                    log.debug("history commit failed: %s", e)
            elif self.orb is not None:
                self.orb.push(commit={"text": None})   # drop the empty pending bubble
            spoke = False
            if self.tts is not None and not payload.get("is_error"):
                resume = self._conversation.is_set()
                if self.speaker is not None:
                    # StreamingSpeaker flushes any un-spoken remainder and resumes listening itself when audio ends
                    spoke = self.speaker.on_result(final, resume)
                else:
                    self._resume_after_tts = resume
                    spoke = self.tts.speak(final)
            if not spoke:
                self._resume_after_tts = False
                self._resume_listening()  # nothing to say (silent mode / error) → listen again right away
        elif kind == "error":
            self.say("error", str(payload.get("error")))
            self._responding = False
            self._orb_flash("error", 3.0)

    def _on_claude_status(self, s: str):
        if s == "thinking":
            self._responding = False
            self.set_status(s)
        elif s == "acting":
            self.status = s  # the orb shows working/searching per tool (set in _on_event)
            if self.tray:
                self.tray.set_status(s)
        elif s == "ready" and self.status not in ("listening", "transcribing"):
            self.set_status("idle")

    # ---------------------------------------------------------------- orb: status strip / menu / activation
    def runtime_status(self) -> dict:
        """Local runtime health only — never a model/API call."""
        from ..jev import engine as jev_engine
        from ..jev.budget import budget
        jev = jev_engine()
        b = budget().snapshot()
        claude_ok = self.session.client is not None
        mem_dir = config.DATA / "memory"
        return {
            "claude": {"ok": claude_ok, "text": "connected" if claude_ok else ("connecting" if self.status == "connecting" else "offline"),
                       "detail": self.session.cli_path or ""},
            "jev": {"ok": (not b["exhausted"]) if jev.available else None,
                    "text": (f"₹{b['spend_inr']:.2f} / ₹{b['limit_inr']:.0f}" + (" · limit reached" if b["exhausted"] else "")) if jev.available else "no key",
                    "detail": f"month {b['month']} · {b['calls']} calls · enforced at ₹{b['enforced_inr']:.0f} · resets {b['resets']}"},
            "memory": {"ok": mem_dir.exists(), "text": "ready", "detail": str(mem_dir)},
            "voice": {"ok": bool(self.prisma is not None and self.prisma.available),
                      "text": (self.prisma.model_name if self.prisma else "loading"),
                      "detail": f"tts={'on' if self.tts and self.tts.available else 'off'} · mode={config.SETTINGS.get('response_mode')}"},
            "computer": {"ok": True, "text": "uia · input", "detail": "MCP engine (spawned per Claude session)"},
            "ocr": {"ok": None, "text": "windows ocr", "detail": "probed on first use"},
            "safety": {"ok": True, "text": "policy on", "detail": "confirm / blocked classes active"},
            "verification": {"ok": True, "text": "on", "detail": "every action verified"},
            "learning": self._learning_status(),
            "tasks": self._tasks_status(),
        }

    def _tasks_status(self) -> dict:
        if getattr(self, "tasks", None) is None:
            return {"ok": None, "text": "off"}
        try:
            st = self.tasks.status()
            cur = st["current"]["goal"][:40] if st["current"] else "idle"
            q = len(st["queue"])
            return {"ok": True, "text": (f"{cur}" + (f" · {q} queued" if q else "")),
                    "detail": f"today={st['today']} · background={st['background']}", "queue": st["queue"],
                    "current": st["current"]}
        except Exception:  # noqa: BLE001
            return {"ok": None, "text": "…"}

    def _learning_status(self) -> dict:
        if getattr(self, "learning", None) is None:
            return {"ok": None, "text": "off"}
        try:
            st = self.learning.status()
            txt = f"v{st['production_version']} · {st['experiences']} exp"
            if st["jev_avoided"]:
                txt += f" · {st['jev_avoided']} JEV saved"
            return {"ok": True, "text": txt,
                    "detail": f"contexts={st['contexts']} promotions={st['promotions']} rollbacks={st['rollbacks']} worker={st['worker']}"}
        except Exception:  # noqa: BLE001
            return {"ok": None, "text": "…"}

    def _learning_orb_loop(self):
        """Reflect real background-learning activity as a brief 'learning' orb accent (never a fake animation)."""
        was = False
        while not self._quit.is_set():
            time.sleep(0.5)
            try:
                now = bool(self.learning and self.learning.learning)
            except Exception:  # noqa: BLE001
                now = False
            if now and not was and self.status in ("idle", "off") and not self._responding:
                self._orb_set("learning")
            elif was and not now and self._orb_state == "learning":
                self._orb_set(self.status if self.status != "off" else "idle")
            was = now

    def push_runtime_status(self):
        if self.orb is None:
            return
        try:
            st = self.runtime_status()
        except Exception as e:  # noqa: BLE001
            log.warning("runtime_status failed: %s", e)
            return
        if st != self._last_status_push:
            self._last_status_push = st
            log.info("status → claude=%s jev=%s voice=%s", st["claude"]["ok"], st["jev"]["text"], st["voice"]["ok"])
            self.orb.push(status=st, mode=str(config.SETTINGS.get("response_mode")),
                          mute_label="Unmute voice" if config.SETTINGS.get("response_mode") == "silent" else "Mute voice")

    def _status_loop(self):
        while not self._quit.is_set():
            time.sleep(5)
            try:
                self.push_runtime_status()
            except Exception as e:  # noqa: BLE001
                log.debug("status push: %s", e)

    def on_orb_ready(self):
        self._orb_set(self._orb_state)
        self.push_runtime_status()
        self.orb.push(activity=f"Hold {config.SETTINGS['hotkey'].replace('<', '').replace('>', '').title()} to talk · mic for hands-free")

    def on_orb_active(self, active: bool):
        log.info("orb %s", "activated" if active else "collapsed")
        if active:
            self._browse_date = None          # always default to TODAY on open
            self._push_transcript(None)

    def orb_menu(self, action: str):
        if action == "listen":
            self.toggle_listening()
        elif action == "mute":
            self.set_mode("silent" if config.SETTINGS.get("response_mode") != "silent" else "concise")
            self.push_runtime_status()
        elif action == "settings":
            config.ensure_dirs()
            f = config.CONFIG_DIR / "settings.json"
            if not f.exists():
                config.save_settings(config.SETTINGS)
            os.startfile(str(f))
        elif action == "console":
            self.show_console()
        elif action == "hide":
            self.orb.hide()
            self.say("system", "Orb hidden — the tray icon brings it back.")
        elif action == "quit":
            self.quit()

    def show_console(self):
        if self.console is not None:
            try:
                self.console.root.after(0, self.console.show)
            except Exception:  # noqa: BLE001
                pass
            return

        def _run():
            from .console import Console
            try:
                self.console = Console(self)
                self.console.run()
            finally:
                self.console = None
        threading.Thread(target=_run, name="console", daemon=True).start()

    def _log_metric(self, key: str, ms):
        try:
            from ..orchestrator.metrics import metrics
            if ms is not None:
                metrics.add(key, ms)
                metrics.flush("app")
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, event: str, **data):
        """Publish a normalized runtime event (AG-UI-compatible) onto the bus — the single source for orb/HUD state
        and the future read-only client contract. Guarded: never breaks the runtime (a bad emit is swallowed, and the
        event name is the first positional arg so `data` may legitimately carry keys like `name`)."""
        if self._bus is None:
            return
        try:
            from ..tasks.events import EventType
            self._bus.emit(EventType[event], **data)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- Claude loop (asyncio thread)
    def _claude_thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.prompt_q = asyncio.Queue()
        self.loop.run_until_complete(self._claude_main())

    async def _claude_main(self):
        try:
            self.set_status("connecting")
            await self.session.connect(resume_previous=True)
            self.say("system", f"Claude Code connected ({os.path.basename(self.session.cli_path or 'claude')}).")
            self.set_status("idle")
        except Exception as e:  # noqa: BLE001
            log.exception("claude connect failed")
            self.say("error", f"Claude Code failed to start: {e}\nRun `claude` once in a terminal to log in, then restart {self.brand}.")
            self.set_status("error")
        while not self._quit.is_set():
            try:
                text = await asyncio.wait_for(self.prompt_q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if text is None:
                break
            if self._browse_date is not None and self.orb is not None:
                self._browse_date = None      # a new turn snaps the transcript back to today (live)
                self._push_transcript(None)
            self._record_user(text)
            self._turn_had_tool_error = False
            if self.tasks is not None:
                try:
                    self._task_id = self.tasks.begin(text, source=getattr(self, "_turn_src", "text"),
                                                     session_id=self.session.session_id).id
                except Exception as e:  # noqa: BLE001
                    log.debug("task begin failed: %s", e)
                    self._task_id = None
            self._emit("RUN_STARTED", run_id=self.session.session_id, task_id=self._task_id, goal=text)
            try:
                r = await self.session.ask(text)
                if r.get("is_error") and r.get("error"):
                    self.say("error", r["error"])
            except Exception as e:  # noqa: BLE001
                log.exception("ask failed")
                self.say("error", str(e))
                if self.tasks is not None and self._task_id:
                    try:
                        self.tasks.fail(self._task_id, str(e)[:120])
                    except Exception:  # noqa: BLE001
                        pass
                    self._task_id = None
        await self.session.disconnect()

    def submit(self, text: str, force: bool = False):
        text = (text or "").strip()
        if not text:
            return
        # follow-up queue (§8): a "after that / then …" said WHILE a task runs is queued, not run now and not a barge-in.
        # `force` bypasses this (used when the queue itself dispatches the next item, so it never re-queues in a loop).
        if not force and self.tasks is not None and self._task_id is not None:
            try:
                from ..tasks.intent import Intent, classify
                if classify(text) is Intent.FOLLOWUP:
                    self.tasks.enqueue(text, source=self._last_src, session_id=self.session.session_id)
                    self.say("system", f"Queued: {text}")
                    return
            except Exception as e:  # noqa: BLE001
                log.debug("followup classify failed: %s", e)
        if self.loop is None or self.prompt_q is None:
            return
        self.loop.call_soon_threadsafe(self.prompt_q.put_nowait, text)

    def emergency_stop(self):
        self.say("system", "⛔ STOP — interrupting.")
        self._end_conversation(quiet=True)
        if self.recorder is not None:
            self.recorder.set_monitor(False)
            if self.recorder.recording:
                self.recorder.stop()
        if self.speaker is not None:
            self.speaker.cancel()
        if self.tts is not None:
            self.tts.stop()
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.session.interrupt(), self.loop)
        self.set_status("idle")

    # ---------------------------------------------------------------- voice
    def set_mode(self, mode: str):
        if self.tts is not None:
            self.tts.set_mode(mode)
        else:
            config.SETTINGS["response_mode"] = mode
            config.save_settings(config.SETTINGS)
        self.say("system", f"Response mode: {mode} (applies to speech now; new sessions get the matching prompt).")

    def _voice_init(self):
        try:
            from ..voice.tts import StreamingSpeaker, engine as tts_engine
            self.tts = tts_engine()
            self.tts.on_start = self._on_tts_start
            self.tts.on_end = self._on_tts_end
            self.speaker = StreamingSpeaker(self.tts, mode_getter=lambda: self.tts.mode,
                                            resume_cb=self._resume_listening,
                                            enabled_getter=lambda: config.SETTINGS.get("stream_tts", True))
        except Exception as e:  # noqa: BLE001
            log.warning("TTS init failed: %s", e)
        try:
            from ..voice.mic import Recorder
            from ..voice.prisma import engine
            self.recorder = Recorder(suppress=lambda: self.tts is not None and self.tts.speaking.is_set(),
                                     on_bargein=self._on_bargein)
            self.prisma = engine()
            self.say("system", f"Connecting Gnani Prisma {self.prisma.model_name} …")
            self.prisma.load()
            try:
                self.recorder.open()  # pre-warm the input stream (first open costs ~0.8 s)
            except Exception as e:  # noqa: BLE001
                log.warning("mic pre-open failed: %s", e)
            self.say("system", f"Voice ready — click Listen for a hands-free conversation, or hold {config.SETTINGS['hotkey']} "
                               f"to talk once (tap to toggle). {STOP_HOTKEY} = emergency stop.")
        except Exception as e:  # noqa: BLE001
            log.exception("voice init failed")
            self.say("error", f"Voice unavailable: {e}. You can still type commands.")

    def start_listening(self):
        log.info("ptt press / listen request (listening=%s, conversation=%s)", self._listening, self._conversation.is_set())
        if self.tts is not None and self.tts.speaking.is_set():
            self.tts.stop()  # barge-in: silence JARVIS first, then listen
            time.sleep(0.2)
        with self._voice_lock:
            if self._listening or self.recorder is None:
                return
            self._listening = True
            self.recorder.start()  # arm NOW (synchronously): a release that arrives before the thread runs still stops it
        self.set_status("listening")
        if self.orb is not None:
            self.orb.push(listening=True)
        threading.Thread(target=self._capture_and_transcribe, daemon=True).start()

    def stop_listening(self, tapped: bool = False):
        # tapped → toggle mode: keep recording until VAD silence; held → stop now
        log.info("ptt release (tapped=%s) listening=%s", tapped, self._listening)
        if self.recorder is None:
            return
        if tapped:
            self.say("system", "…listening (auto-stop on silence)")
            return
        self.recorder.stop()

    def toggle_listening(self):
        """Listen button / tray: starts a hands-free conversation, or ends the one in progress."""
        if self._conversation.is_set():
            self._end_conversation()
        elif self._listening:
            self.recorder.stop()
        else:
            self._start_conversation()

    @property
    def in_conversation(self) -> bool:
        return self._conversation.is_set()

    def _start_conversation(self):
        if self.recorder is None:
            self.say("error", "Voice is not available.")
            return
        self._conversation.set()
        self._uiq(("conversation", True))
        self.say("system", "Conversation started — just talk; I keep listening after each reply. Click End conversation or Stop to finish.")
        self.start_listening()

    def _end_conversation(self, quiet: bool = False):
        was_on = self._conversation.is_set()
        self._conversation.clear()
        self._resume_after_tts = False
        if not was_on:
            return
        self._uiq(("conversation", False))
        if self.recorder is not None and self._listening:
            self.recorder.stop()  # the capture thread sees the cleared flag and will not re-arm
        if not quiet:
            self.say("system", "Conversation ended.")
            if self.status == "listening":
                self.set_status("idle")

    def _resume_listening(self):
        """Re-arm the mic if a hands-free conversation is active (called after a turn / after TTS finishes)."""
        if self._conversation.is_set() and not self._quit.is_set():
            self.start_listening()

    def _on_tts_start(self):
        self._orb_set("speaking")             # _orb_set arms barge-in via _sync_bargein (JARVIS is now busy)
        if self.orb is not None:
            self.orb.push(speaking=True)
        self._emit("TTS_STARTED")

    def _on_tts_end(self):
        if self.orb is not None:
            self.orb.push(speaking=False)
        if self._orb_state == "speaking":
            self._orb_set(self.status if self.status not in ("acting",) else "idle")
        self._sync_bargein()                  # speech is done → disarm unless JARVIS is still busy working
        self._emit("TTS_FINISHED")
        if self.speaker is not None:
            self.speaker.on_speech_end()      # resumes listening once ALL queued speech has drained
        elif self._resume_after_tts:
            self._resume_after_tts = False
            self._resume_listening()

    def _on_bargein(self):
        """Called from the audio thread when the user speaks over JARVIS. Return fast — hand off to a worker so the
        0.2 s settle in start_listening never blocks the audio callback."""
        log.info("barge-in detected")
        threading.Thread(target=self._handle_bargein, name="bargein", daemon=True).start()

    def _handle_bargein(self):
        if self.speaker is not None:
            self.speaker.cancel()            # stop speaking the rest of the (now obsolete) reply
        if self.tts is not None:
            self.tts.stop()                  # clear queued/playing audio immediately
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.session.interrupt(), self.loop)  # cancel obsolete generation
        self._uiq(("system", "…listening"))
        self.start_listening()               # capture the user's new utterance right away

    def _capture_and_transcribe(self):
        submitted = False
        hands_free = self._conversation.is_set()
        try:
            audio = self.recorder.record(auto_stop=True, on_level=lambda lv: self._uiq(("level", lv)))
            with self._voice_lock:
                self._listening = False
            if len(audio) < 16000 * 0.3 or (hands_free and not self.recorder.spoke):
                # hands-free: a whole window of silence is normal — skip transcription and simply listen again
                if not hands_free:
                    self.set_status("idle")
                return
            self.set_status("transcribing")
            r = self.prisma.transcribe(audio)
            self._log_metric("voice_ms", r["voice_ms"])
            text = r["text"].strip()
            self._uiq(("level", 0.0))
            if not text or r.get("no_speech", 0) > 0.85:
                if not hands_free:
                    self.say("system", "(nothing heard)")
                    self.set_status("idle")
                return
            self.say("meta", f"🎤 {r['audio_s']}s audio → {r['voice_ms']} ms")
            self._last_src = "voice"          # tag this turn as voice for the daily conversation
            self._maybe_ack(text)             # immediate "On it." for longer tasks, before Claude is consulted (§9)
            self.submit(text)
            submitted = True
        except Exception as e:  # noqa: BLE001
            log.exception("voice capture failed")
            self.say("error", f"voice: {e}")
            self._end_conversation(quiet=True)  # do not spin on a broken mic
            self.set_status("idle")
        finally:
            with self._voice_lock:
                self._listening = False
            self._sync_bargein()          # capture ended → re-arm barge-in if JARVIS is (still) busy
            if self.orb is not None:
                self.orb.push(listening=False)
            if not submitted:
                self._resume_listening()  # no-op unless a conversation is active

    def _finish_task(self, payload: dict):
        """Complete or fail the durable task for this turn, using REAL signals (tools + tool-error), store a receipt,
        emit lifecycle events, then run the next queued follow-up (§8, §11)."""
        if self.tasks is None or not self._task_id:
            return
        tid, self._task_id = self._task_id, None
        try:
            tools = dict(self.session.last_metrics.get("tool_calls") or {})
            self.tasks.record_tools(tid, tools)
            if payload.get("is_error"):
                self.tasks.fail(tid, "turn error")
            else:
                verified = (not self._turn_had_tool_error) if tools else None   # real: actions ran and none errored
                t = self.tasks.complete(tid, verified=verified, result=payload.get("result") or "")
                if t is not None and tools:      # a task that did real computer work → compact proof-of-work receipt
                    n = sum(tools.values())
                    vtxt = " · verified ✓" if verified else (" · NOT verified" if verified is False else "")
                    self.say("system", f"✓ {n} action{'s' if n != 1 else ''}{vtxt}")
                    if self.orb is not None:
                        try:
                            from ..tasks.receipts import format_receipt
                            self.orb.push(receipt={"text": format_receipt(t), "goal": t.goal, "verified": verified})
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as e:  # noqa: BLE001
            log.debug("finish task failed: %s", e)
        self._emit("RUN_FINISHED", run_id=self.session.session_id, task_id=tid)
        self._advance_queue()

    def _advance_queue(self):
        """After a turn finishes, execute the next queued follow-up as a normal turn (§8)."""
        if self.tasks is None:
            return
        try:
            nxt = self.tasks.pop_next()
            if nxt is not None:
                self.tasks.dequeue(nxt.id)
                self.say("system", f"Next up: {nxt.goal}")
                self.submit(nxt.goal, force=True)
        except Exception as e:  # noqa: BLE001
            log.debug("advance queue failed: %s", e)

    def _record_graph(self, verified: bool):
        """Link this turn into JARVIS's local memory graph for recall (best-effort, off the audio path)."""
        if self.graph is None:
            return
        try:
            tools = dict(self.session.last_metrics.get("tool_calls") or {})
            app = None
            try:
                from ..computer.engine import winfind
                fg = winfind.foreground_window()
                app = next((w["process"] for w in winfind.list_windows() if w["hwnd"] == fg), None)
            except Exception:  # noqa: BLE001
                pass
            self.graph.record_turn(getattr(self, "_last_user_text", ""), session_id=self.session.session_id,
                                   app=app, tools=tools, verified=verified)
            self.graph.save()
        except Exception as e:  # noqa: BLE001
            log.debug("graph record failed: %s", e)

    def _warm_jev(self):
        """Pre-establish the JEV TLS/HTTP2 connection at startup so the first real decision skips the handshake
        (§22, §37). Token-free — never touches the ₹1,000/month budget."""
        try:
            from ..jev import engine as jev_engine
            jev_engine().warm()
        except Exception as e:  # noqa: BLE001
            log.debug("jev warm failed: %s", e)

    def _hotkeys(self):
        try:
            from ..voice.ptt import PushToTalk
            self.ptt = PushToTalk(on_press=self.start_listening, on_release=lambda tapped: self.stop_listening(tapped)).start()
            self.stop_hotkey = PushToTalk(hotkey=STOP_HOTKEY, on_press=self.emergency_stop).start()
        except Exception as e:  # noqa: BLE001
            log.exception("hotkeys failed")
            self.say("error", f"hotkey unavailable: {e}")

    # ---------------------------------------------------------------- lifecycle
    def quit(self):
        if self._quit.is_set():
            return
        self._quit.set()
        try:
            if self.loop is not None and self.prompt_q is not None:
                self.loop.call_soon_threadsafe(self.prompt_q.put_nowait, None)
        except Exception:  # noqa: BLE001
            pass
        for h in (self.ptt, self.stop_hotkey):
            try:
                h and h.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.recorder and self.recorder.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.tts and self.tts.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.history.end_session("shutdown")   # close today's session marker (the day's conversation persists)
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "learning", None) is not None:
                self.learning.daily_summary()      # local structured learning summary for today
                self.learning.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "graph", None) is not None:
                self.graph.save()
                self.graph.export_graphify()        # refresh the graphify-compatible graph.json (local only)
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "tasks", None) is not None:
                self.tasks.stop()
            if getattr(self, "watches", None) is not None:
                self.watches.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.tray:
            self.tray.stop()
        if self.console:
            self.console.close()
        if self.orb:
            self.orb.close()

    def run(self):
        _setup_logging()
        if not _single_instance(self.brand):
            return 0
        log.info("%s starting — root=%s", self.brand, config.ROOT)
        try:
            self.history.start_session("startup")   # opens/resumes today's conversation (same day = same conversation)
        except Exception as e:  # noqa: BLE001
            log.warning("history session start failed: %s", e)
        try:
            from ..learning import install as _install_learning
            self.learning = _install_learning()     # register the learned-policy hook + start the background worker
            threading.Thread(target=self._learning_orb_loop, name="learn-orb", daemon=True).start()
        except Exception as e:  # noqa: BLE001
            log.warning("learning install failed: %s", e)
        if config.SETTINGS.get("graph_enabled", True):
            try:
                from ..graph import graph as _graph
                self.graph = _graph()               # local private memory graph of JARVIS's own experience
            except Exception as e:  # noqa: BLE001
                log.warning("memory graph init failed: %s", e)
        if config.SETTINGS.get("tasks_enabled", True):
            try:
                from ..tasks import bus as _bus, manager as _tasks
                from ..tasks.notify import notifications as _notif
                from ..tasks.watch import watches as _watches
                self._bus = _bus()                  # normalized runtime event stream (feeds orb/HUD + future clients)
                self.tasks = _tasks()               # durable task engine + follow-up queue + receipts
                self.tasks.start()
                self.notify = _notif()              # local, deduplicated notifications
                self.watches = _watches()           # recurring watches (local file/page conditions)
                self.watches.start()
            except Exception as e:  # noqa: BLE001
                log.warning("task engine init failed: %s", e)
        from ..ui.orb import OrbWindow
        from .tray import Tray
        self.orb = OrbWindow(self)
        confirm.set_hook(self._confirm)
        self.tray = Tray(self)
        self.tray.start()
        threading.Thread(target=self._claude_thread, name="claude", daemon=True).start()
        threading.Thread(target=self._voice_init, name="voice-init", daemon=True).start()
        threading.Thread(target=self._warm_jev, name="jev-warm", daemon=True).start()
        threading.Thread(target=self._status_loop, name="status", daemon=True).start()
        self._hotkeys()
        self.set_status("starting")
        try:
            self.orb.run()  # blocks on the WebView2 loop (main thread)
        finally:
            self.quit()
        return 0

    def _confirm(self, title: str, message: str, timeout_s: int) -> bool:
        """Safety confirmations: glass card in the orb; native message box if the orb is not ready."""
        try:
            return self.orb.confirm(title, message, timeout_s)
        except Exception as e:  # noqa: BLE001
            log.warning("orb confirm unavailable (%s); using message box", e)
            fn = ctypes.windll.user32.MessageBoxTimeoutW
            fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_ushort, ctypes.c_uint]
            return fn(None, message, title, 0x4 | 0x30 | 0x40000 | 0x10000, 0, int(timeout_s * 1000)) == 6


def main() -> int:
    return JarvisApp().run()


_ = (policy, _icon, time)
