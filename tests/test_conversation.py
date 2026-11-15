"""Hands-free conversation mode: Listen starts a conversation that re-arms the mic after every reply and ends only
when the user asks. Uses fake recorder/Prisma/TTS — no audio devices, no Claude."""
from __future__ import annotations

import threading
import time

import numpy as np

from jarvis.app.main import JarvisApp


class FakeRecorder:
    def __init__(self):
        self.recording = False
        self.spoke = True
        self.starts = 0
        self.script: list[np.ndarray] = []  # audio returned by successive record() calls
        self._stop = threading.Event()

    def start(self):
        self.starts += 1
        self.recording = True
        self._stop.clear()

    def stop(self):
        self._stop.set()

    monitor = False  # last set_monitor() value (barge-in armed?)

    def set_monitor(self, on):
        self.monitor = bool(on)

    def record(self, auto_stop=True, on_level=None):
        if self.script:
            audio = self.script.pop(0)
            self.spoke = bool(len(audio)) and float(np.abs(audio).max()) > 0.012
        else:  # nothing scripted → behave like a mic that hears nothing until stopped
            self._stop.wait(2.0)
            audio, self.spoke = np.zeros(0, dtype=np.float32), False
        self.recording = False
        return audio

    def close(self):
        pass


class FakePrisma:
    def transcribe(self, audio):
        return {"text": "hello jarvis", "voice_ms": 1, "audio_s": 1.0, "no_speech": 0.0}


class FakeTTS:
    def __init__(self):
        self.speaking = threading.Event()
        self.on_end = None
        self.mode = "concise"
        self.available = True
        self.spoken: list[str] = []

    def speak(self, text, mode=None, interrupt=True):
        self.spoken.append(text)
        threading.Timer(0.05, self._done).start()
        return True

    def speak_chunk(self, text, first=False):
        self.spoken.append(text)
        threading.Timer(0.05, self._done).start()
        return True

    def pending(self):
        return False

    def _done(self):
        self.on_end and self.on_end()

    def stop(self):
        pass


def _app():
    app = JarvisApp()
    app.recorder, app.prisma, app.tts = FakeRecorder(), FakePrisma(), FakeTTS()
    app.tts.on_end = app._on_tts_end
    app.submitted: list[str] = []
    app.submit = lambda t: app.submitted.append(t)  # type: ignore[method-assign]
    return app


def _wait(pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_listen_button_starts_conversation_and_rearms_after_reply():
    app = _app()
    speech = np.ones(16000, dtype=np.float32) * 0.1
    app.recorder.script = [speech]

    app.toggle_listening()
    assert app.in_conversation
    assert _wait(lambda: app.submitted == ["hello jarvis"])
    # the turn completes; JARVIS speaks; when speech ends the mic is armed again without any click
    app._on_event("result", {"result": "Hi there.", "num_turns": 1, "claude_ms": 5})
    assert _wait(lambda: app.recorder.starts >= 2)
    assert app.tts.spoken == ["Hi there."]

    # ending the conversation stops the loop
    app.toggle_listening()
    assert not app.in_conversation
    starts = app.recorder.starts
    time.sleep(0.3)
    assert app.recorder.starts == starts
    assert not app._listening


def test_silence_keeps_listening_without_transcribing():
    app = _app()
    silence = np.zeros(16000, dtype=np.float32)
    app.recorder.script = [silence, silence]
    app.toggle_listening()
    assert _wait(lambda: app.recorder.starts >= 3)  # two silent windows skipped, third capture armed
    assert app.submitted == []
    app.emergency_stop()
    assert not app.in_conversation


def test_single_listen_without_conversation_is_unchanged_for_ptt():
    app = _app()
    app.recorder.script = [np.ones(16000, dtype=np.float32) * 0.1]
    app.start_listening()  # PTT path: one utterance, no conversation
    assert not app.in_conversation
    assert _wait(lambda: app.submitted == ["hello jarvis"])
    time.sleep(0.2)
    assert app.recorder.starts == 1


def test_bargein_armed_whenever_jarvis_is_busy():
    """The user can cut in by voice any time JARVIS is busy — thinking, working a tool, or speaking — but not while
    JARVIS is already capturing them, and not when idle. (User request: guide him mid-task.)"""
    app = _app()
    for state in ("thinking", "responding", "acting", "searching", "speaking"):
        app._orb_set(state)
        assert app.recorder.monitor is True, f"barge-in should be armed while {state}"
    # while actively listening we are already capturing the user → monitor off
    app._listening = True
    app._sync_bargein()
    assert app.recorder.monitor is False
    app._listening = False
    # idle → off
    app._orb_set("idle")
    assert app.recorder.monitor is False


def test_bargein_while_busy_setting_disables_it():
    from jarvis import config
    app = _app()
    old = config.SETTINGS.get("bargein_while_busy", True)
    config.SETTINGS["bargein_while_busy"] = False
    try:
        app._orb_set("acting")
        assert app.recorder.monitor is False   # setting off → never arms during work
    finally:
        config.SETTINGS["bargein_while_busy"] = old


def _with_tasks(app, tmp_path):
    from jarvis.tasks.events import EventBus
    from jarvis.tasks.manager import TaskManager
    from jarvis.tasks.store import TaskStore
    app.tasks = TaskManager(store=TaskStore(path=tmp_path / "t.db"), event_bus=EventBus())
    app._bus = app.tasks.bus
    return app


def test_turn_creates_task_and_receipt(tmp_path):
    """A turn creates a durable task; on result it completes with a receipt built from REAL tool signals (§5, §11)."""
    app = _with_tasks(_app(), tmp_path)
    t = app.tasks.begin("open notepad and type hello", source="voice")   # as _claude_main does
    app._task_id, app._turn_had_tool_error = t.id, False
    app.session.last_metrics = {"tool_calls": {"mcp__jarvis__computer_app": 1, "computer_type": 1}}
    app._on_event("result", {"result": "opened notepad and typed hello", "num_turns": 4, "claude_ms": 120})
    done = app.tasks.store.get(t.id)
    assert done.state == "completed" and done.verified is True
    assert done.receipt["action_total"] == 2
    assert app._task_id is None


def test_followup_is_queued_while_a_task_runs(tmp_path):
    """"after that, open Gmail" said while a task runs is QUEUED (not run now, not a barge-in) (§8)."""
    app = _with_tasks(_app(), tmp_path)
    app.submit = JarvisApp.submit.__get__(app)          # restore the real submit (the _app helper stubs it)
    app._task_id = app.tasks.begin("research three tools").id
    app.submit("after that, open Gmail and write the summary")
    assert [t.goal for t in app.tasks.queue()] == ["after that, open Gmail and write the summary"]
    # an ordinary command while busy is NOT queued (it would dispatch; loop is None in the test so it simply no-ops)
    app.submit("open notepad")
    assert len(app.tasks.queue()) == 1


def test_tool_use_event_emits_without_crashing(tmp_path):
    """Regression: _emit's first param was named 'name', colliding with data key 'name' on TOOL_CALL_START and
    crashing every tool-using turn (surfaced as repeated 'got multiple values for argument name' errors)."""
    app = _with_tasks(_app(), tmp_path)
    seen = []
    app._bus.subscribe(lambda e: seen.append(e))
    app._task_id = "t1"
    app._on_event("tool_use", {"name": "mcp__jarvis__computer_app", "input": {"path": "notepad"}})   # must not raise
    assert any(e.type.value == "tool_call_start" and e.data.get("name") == "computer_app" for e in seen)


def test_failed_turn_fails_the_task(tmp_path):
    app = _with_tasks(_app(), tmp_path)
    t = app.tasks.begin("do something")
    app._task_id, app._turn_had_tool_error = t.id, False
    app.session.last_metrics = {"tool_calls": {}}
    app._on_event("result", {"result": "", "is_error": True, "num_turns": 1, "claude_ms": 50})
    assert app.tasks.store.get(t.id).state == "failed"
