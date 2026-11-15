r"""Product paths and settings. Everything lives under D:\JARVIS.

Settings come from (lowest → highest precedence):
  1. defaults below
  2. data/config/settings.json
  3. data/config/.env  (secrets such as TYPESAFE_API_KEY — never committed)
  4. process environment
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("JARVIS_ROOT") or Path(__file__).resolve().parent.parent)
DATA = ROOT / "data"
CONFIG_DIR = DATA / "config"
MODELS = ROOT / "models"
LOGS = ROOT / "logs"
DIST = ROOT / "dist"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"

BRAND = "JARVIS"  # working name; keep configurable (settings.json: "brand")

DEFAULTS: dict = {
    "brand": BRAND,
    "hotkey": "<ctrl>+<alt>+j",          # push-to-talk (pynput syntax)
    "prisma_model": "prisma-v3.0",
    "prisma_language": "indic-auto",
    "timbre_model": "timbre-v2.5",
    "timbre_voice": "Kaveri",
    "timbre_language": "en-IN",
    "gnani_timeout_s": 30.0,
    "mic_device": None,                  # sounddevice index or None = default
    "vad_energy_threshold": 0.012,       # RMS floor: above = speech (endpointer + recorder)
    "max_utterance_s": 30,
    # low-latency voice (Performance/UX 2026-09-22): adaptive end-of-turn, barge-in, streaming TTS, instant ack
    "adaptive_endpointing": True,        # §6 context-dependent trailing-silence instead of a fixed 0.9 s
    "endpoint_fast_silence_s": 0.55,     # trailing silence to finalize a short fluent command
    "endpoint_base_silence_s": 0.8,      # default trailing silence
    "endpoint_max_silence_s": 1.3,       # tolerated once the speaker has shown hesitation
    "endpoint_short_utt_s": 1.4,         # utterances shorter than this (and fluent) → fast finalize
    "bargein_enabled": True,             # §10/§13 let the user interrupt JARVIS by voice mid-reply
    "bargein_while_busy": True,          # arm barge-in the WHOLE time JARVIS is busy (speaking OR working a task), not just during speech
    "bargein_threshold": 0.035,          # RMS to count as over-speech — above the VAD floor AND above TTS self-echo/ambient (raise in a noisy room)
    "bargein_sustain_s": 0.22,           # over-speech must persist this long (§41 no cough/click false trigger; raise if it triggers too eagerly)
    "stream_tts": True,                  # §12 speak clause-by-clause as Claude streams, not after the whole reply
    "ack_enabled": True,                 # §9 immediate local "On it." for longer tasks (no Claude round-trip)
    "jev_model": "jev-latest",
    "jev_timeout_s": 4.0,
    "jev_retries": 1,
    "jev_min_confidence": 0.55,
    "jev_monthly_limit_inr": 1000.0,     # HARD ceiling per calendar month (non-negotiable)
    "jev_reserve_fraction": 0.10,        # guard stops at 90 % of the ceiling
    "jev_usd_inr": 95.0,                 # conservative FX for local accounting
    "jev_usd_per_mtok": 0.042,           # TypeSafe list price (input tokens; output free)
    "act_max_steps": 12,
    "observe_max_elements": 120,
    "claude_model": None,                # None = user's default (Claude Max)
    "claude_permission_mode": "acceptEdits",
    "log_level": "INFO",
    "response_mode": "concise",          # silent | concise | conversational (spoken replies)
    "memory_enabled": True,              # inject relevant local memory into each prompt
    "batching": True,
    "learning_enabled": True,           # perpetual learning: bandit policy over the router hook (§RL)
    "graph_enabled": True,              # local private memory graph of JARVIS's own experience (jarvis/graph)
    "tasks_enabled": True,              # durable task engine + follow-up queue + receipts + event bus (jarvis/tasks)
    "task_max_retries": 2,             # bounded retry for a failed task (§7)
    "privacy": {"screenshots": "allow", "redact_cloud": True, "mask_fields": True, "max_text_chars": 6000},
}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def ensure_dirs() -> None:
    for d in (DATA, CONFIG_DIR, MODELS, LOGS, DIST):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    ensure_dirs()
    _load_env_file(CONFIG_DIR / ".env")
    s = dict(DEFAULTS)
    f = CONFIG_DIR / "settings.json"
    if f.exists():
        try:
            s.update(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    # env overrides: JARVIS_<KEY>
    for k in list(s):
        ev = os.environ.get("JARVIS_" + k.upper())
        if ev is not None:
            s[k] = ev
    return s


def save_settings(s: dict) -> None:
    ensure_dirs()
    (CONFIG_DIR / "settings.json").write_text(json.dumps(s, indent=2), encoding="utf-8")


SETTINGS = load_settings()


def jev_api_key() -> str | None:
    return os.environ.get("TYPESAFE_API_KEY") or None


def gnani_api_key() -> str | None:
    return os.environ.get("GNANI_API_KEY") or None
