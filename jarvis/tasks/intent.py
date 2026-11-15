"""Classify an utterance that arrives WHILE JARVIS is busy (OpenMuse integration §8).

Barge-in (built earlier) stops JARVIS and takes the latest instruction. But sometimes the user is not correcting — they
are QUEUEING more work: "after that, open Gmail." Those must not be conflated. This is a small local, deterministic
classifier (no model): FOLLOWUP → add to the queue and let the current task finish; INTERRUPT → correct/replace now;
NEW → a fresh command. Conservative by design: only clear follow-up/interrupt cues change the default.
"""
from __future__ import annotations

import enum
import re


class Intent(str, enum.Enum):
    FOLLOWUP = "followup"    # queue it after the current work
    INTERRUPT = "interrupt"  # stop and do this instead / correct
    NEW = "new"              # a normal new command


_FOLLOWUP = re.compile(r"\b(after that|afterwards|then|next|also|and then|once (you're |you are )?done|"
                       r"when (you're|you are) (done|finished)|follow up|followup|queue|add to the (list|queue))\b", re.I)
_INTERRUPT = re.compile(r"\b(actually|no wait|wait|stop|cancel|never ?mind|instead|forget (it|that)|"
                        r"hold on|scratch that|change of plans|do this first)\b", re.I)


def classify(text: str) -> Intent:
    t = (text or "").strip().lower()
    if not t:
        return Intent.NEW
    # an explicit interrupt cue near the start wins (the user is correcting course)
    if _INTERRUPT.search(t[:40]):
        return Intent.INTERRUPT
    if _FOLLOWUP.search(t):
        return Intent.FOLLOWUP
    return Intent.NEW
