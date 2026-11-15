"""Immediate local acknowledgement (Performance/UX directive §9, §30).

The single biggest *perceived*-latency win: the moment the user stops speaking, JARVIS answers with a tiny local phrase
("On it.") — no Claude round-trip — for tasks that will take a while, then the real work proceeds. For instant
deterministic work ("Open Chrome.") it stays silent and just acts, so the assistant never becomes chatty (§30).

`ack_for(transcript)` returns a short phrase to speak immediately, or None to stay silent. Deterministic, offline, and
rotates through the vocabulary so repeated commands do not get the same filler every time (§30: non-repetitive).
"""
from __future__ import annotations

import itertools

# spoken the instant the turn is finalized, before Claude is consulted — only for tasks worth a "working on it"
_ACKS = ("On it.", "Sure.", "Got it.", "One moment.", "Okay.", "Right away.")
_cycle = itertools.cycle(_ACKS)

# verbs whose whole job is one deterministic action — acting is faster than talking, so say nothing (§9, §30)
_INSTANT_VERBS = ("open", "close", "launch", "start", "quit", "exit", "mute", "unmute", "stop", "pause", "play",
                  "minimize", "maximize", "switch", "focus", "volume", "screenshot", "copy", "paste", "undo", "redo")
# cues that the task is multi-step / will take a moment → a short ack reassures the user work has begun
_WORK_CUES = ("search", "find", "look", "organize", "sort", "download", "install", "send", "email", "message",
              "summarize", "write", "compose", "fill", "book", "schedule", "research", "compare", "check", "and then",
              "then ", "after that", "all the", "every ")


def _first_word(text: str) -> str:
    for w in text.strip().lower().replace(",", " ").split():
        return w.strip(".!?")
    return ""


def ack_for(transcript: str, min_words_for_ack: int = 7) -> str | None:
    """Return a short spoken acknowledgement, or None to act silently.

    Silent (act immediately, no filler) when the utterance is a short single deterministic command. Acknowledge when it
    names multi-step work or is long enough that the user benefits from immediate reassurance.
    """
    t = (transcript or "").strip().lower()
    if not t:
        return None
    words = t.split()
    has_work_cue = any(cue in t for cue in _WORK_CUES)
    is_instant = _first_word(t) in _INSTANT_VERBS
    # short, instant, no multi-step cue → stay silent and just do it
    if is_instant and len(words) <= 6 and not has_work_cue:
        return None
    if has_work_cue or len(words) >= min_words_for_ack:
        return next(_cycle)
    return None
