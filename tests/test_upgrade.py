"""Unit tests for the upgrade: memory store, privacy redaction, batch step parsing, TTS text cleaning, mode prompts."""
from __future__ import annotations

import json
import time

import pytest

from jarvis.memory import store as memory
from jarvis.safety import privacy
from jarvis.voice import tts


@pytest.fixture(autouse=True)
def _tmp_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")


# ----------------------------------------------------------------------------- memory
def test_memory_remember_recall_and_dedupe():
    memory.remember("app_patterns", {"app": "Notepad", "pattern": "press ctrl+n for a fresh document before typing"})
    memory.remember("app_patterns", {"app": "Notepad", "pattern": "press ctrl+n for a fresh document before typing"})
    memory.remember("workflows", {"goal": "save note as file", "app": "Notepad", "steps_brief": "app:notepad; key:ctrl+shift+s"})
    memory.remember("failures", {"goal": "open downloads", "app": "explorer", "lesson": "clicking 'Downloads' in the toolbar opens a menu, not the folder"})
    memory.set_preference("research_folder", r"D:\Research")
    r = memory.recall("save a note in notepad as a file", app="Notepad")
    kinds = [e["kind"] for e in r]
    assert "workflows" in kinds and "app_patterns" in kinds
    # dedupe increments uses instead of duplicating
    pats = memory._load("app_patterns")
    assert len(pats) == 1 and pats[0].get("uses") == 1
    b = memory.brief("save note notepad", "Notepad")
    assert "research_folder" in b and "ctrl+n" in b and len(b) <= 700
    assert memory.forget("app_patterns", "ctrl+n")["removed"] == 1


def test_memory_never_stores_secrets_or_credential_values():
    memory.remember("workflows", {"goal": "login", "app": "Chrome", "steps": [
        {"type": "hunter2", "target": {"name": "Password"}, "note": "api_key=sk-abcdefghijklmnopqrstuvwxyz1234"},
    ], "steps_brief": "type:*** into Password"})
    raw = (memory.MEMORY_DIR / "workflows.jsonl").read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnop" not in raw
    e = memory._load("workflows")[0]
    assert e["steps"][0]["note"].startswith("api_key=***") or "***" in e["steps"][0]["note"]


def test_learn_batch_records_success_and_failure():
    steps = [{"app": "notepad.exe"}, {"key": "ctrl+n"}]
    memory.learn_batch("open notepad fresh", "Notepad", {"status": "done", "trace": [], "total_ms": 500}, steps)
    memory.learn_batch("open notepad fresh", "Notepad", {"status": "failed", "trace": [{"step": 2, "action": "key", "arg": "ctrl+n", "error": "Timeout"}]}, steps)
    assert memory._load("workflows")[0]["steps_brief"] == "app:notepad.exe; key:ctrl+n"
    assert "Timeout" in memory._load("failures")[0]["lesson"]


# ----------------------------------------------------------------------------- privacy
def test_privacy_redaction_and_masking():
    els = [{"id": 1, "role": "edit", "name": "Password", "value": "hunter2"}, {"id": 2, "role": "edit", "name": "Search", "value": "cats"}]
    privacy.scrub_elements(els)
    assert els[0]["value"] == "***" and els[1]["value"] == "cats"
    t = privacy.redact_text("card 4111 1111 1111 1111 token=ghp_ABCDEFGHIJKLMNOPQRSTUVWX ssn 123-45-6789 Bearer abcdefghijklmnopqrstu")
    assert "4111" not in t and "ghp_" not in t and "123-45-6789" not in t and "abcdefghijklmnopqrstu" not in t
    out = privacy.scrub_result({"stdout": "password=hunter22", "nested": [{"x": "api_key=sk-abcdefghijklmnopqrstuvwxyz"}]})
    assert "hunter22" not in json.dumps(out) and "sk-abcdefghijklmnop" not in json.dumps(out)
    assert privacy.window_blocked("1Password - Vault") and not privacy.window_blocked("Untitled - Notepad")
    assert privacy.cap_text("x" * 7000).endswith("chars]")


# ----------------------------------------------------------------------------- batch parsing (no desktop)
def test_batch_bare_expect_and_unknown_step():
    from jarvis.orchestrator import batch

    class FakeTools:
        pass

    r = batch.run([{"expect": {"file": __file__}}, {"bogus": 1}], FakeTools(), stop_on_fail=True)
    assert r["trace"][0]["action"] == "expect" and r["trace"][0]["ok"] is True
    assert r["status"] == "failed" and "no action" in r["trace"][1]["error"]


# ----------------------------------------------------------------------------- tts cleaning
def test_tts_cleaning_modes():
    raw = ("Done — saved `D:\\JARVIS\\data\\demo.txt` (25 bytes). It matched in both places.\n\n```\ncode\n```\n"
           "`★ Insight ─────────────────────────────────────`\n- blah\n`─────────────────────────────────────────────────`\n"
           "See https://example.com/x for more. Third sentence here. Fourth one.")
    c = tts.clean_for_speech(raw, "concise")
    assert "Insight" not in c and "```" not in c and "https" not in c and "D:\\" not in c
    assert c.count(".") <= 3
    full = tts.clean_for_speech(raw, "conversational")
    assert "Third sentence" in full


def test_mode_prompt_selection():
    from jarvis.app import claude_session as cs
    assert set(cs.MODE_PROMPTS) == {"silent", "concise", "conversational"}
    assert "computer_run" in cs.SYSTEM_APPEND and "MINIMIZE TURNS" in cs.SYSTEM_APPEND


_ = time
