"""Privacy controls: what may leave the machine, and redaction of everything that does.

Settings (data/config/settings.json → "privacy"):
  screenshots: "allow" | "confirm" | "never"     whether computer_screen may return pixels to Claude (OCR text is still local-first)
  redact_cloud: true                              redact secrets / credential values in all tool results and transcripts
  mask_fields: true                               never expose the value of password/PIN/OTP/card fields in observations
  max_text_chars: 6000                            cap on text sent per tool result
  blocked_titles: ["Password Manager", ...]      windows whose content is never observed/captured (titles, substring match)
Audio never leaves the machine (Whisper is local). Raw screenshots are kept in memory only; downscaled PNGs go to
Claude only when the screenshot policy allows.
"""
from __future__ import annotations

import re

from .. import config
from . import policy

DEFAULTS = {"screenshots": "allow", "redact_cloud": True, "mask_fields": True, "max_text_chars": 6000,
            "blocked_titles": ["1Password", "Bitwarden", "KeePass", "LastPass", "Credential Manager", "Private Browsing", "InPrivate"]}

_SENSITIVE_NAME = re.compile(r"(?i)password|passcode|\bpin\b|otp|one[- ]time|verification code|cvv|card number|security code|ssn|secret")
_PII = [
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[card-number]"),                        # card-like digit runs
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    (re.compile(r"(?i)\b(bearer\s+)[a-z0-9._-]{16,}"), r"\1***"),
]


def settings() -> dict:
    s = dict(DEFAULTS)
    s.update(config.SETTINGS.get("privacy") or {})
    return s


def screenshot_policy() -> str:
    return str(settings().get("screenshots", "allow")).lower()


def window_blocked(title: str | None) -> bool:
    t = (title or "").lower()
    return any(b.lower() in t for b in settings().get("blocked_titles", []))


def is_sensitive_field(name: str | None) -> bool:
    return bool(_SENSITIVE_NAME.search(name or ""))


def redact_text(text: str) -> str:
    """Secrets + obvious PII shapes. Applied to everything that goes to a cloud model."""
    if not text or not settings().get("redact_cloud", True):
        return text
    out = policy.redact(text)
    for rx, rep in _PII:
        out = rx.sub(rep, out)
    return out


def scrub_elements(elements: list[dict]) -> list[dict]:
    """Mask values of credential-like fields in an observation."""
    if not settings().get("mask_fields", True):
        return elements
    for e in elements:
        if "value" in e and is_sensitive_field(e.get("name")):
            e["value"] = "***"
    return elements


def scrub_result(obj):
    """Recursive redaction of a tool result before it is serialized for Claude."""
    if not settings().get("redact_cloud", True):
        return obj
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: scrub_result(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_result(x) for x in obj]
    return obj


def cap_text(text: str) -> str:
    n = int(settings().get("max_text_chars", 6000))
    return text if len(text) <= n else text[:n] + f"… [truncated {len(text) - n} chars]"
