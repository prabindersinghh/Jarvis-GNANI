"""Safety policy: classify every operation as SAFE / CONFIRM / BLOCKED before execution.

BLOCKED = deliberate security boundaries JARVIS never bypasses (UAC secure desktop,
lock screen, MFA, CAPTCHA, biometrics, credential prompts).
CONFIRM = high-impact operations that need a human yes (destructive deletion,
destructive git, firewall/registry/security changes, credentials, money, publishing).
Secrets are redacted from anything logged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SAFE, CONFIRM, BLOCKED = "safe", "confirm", "blocked"


@dataclass
class Verdict:
    level: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.level == SAFE


# --- shell / command classification ------------------------------------------------
_CONFIRM_SHELL = [
    (r"\b(rm|del|erase|rmdir|rd)\b.*(-r|-rf|/s|/q|-Recurse|-Force)", "recursive/forced deletion"),
    (r"\bRemove-Item\b.*(-Recurse|-Force)", "recursive/forced deletion"),
    (r"\bformat(\.com)?\b\s+[a-z]:", "disk format"),
    (r"\bdiskpart\b", "disk partitioning"),
    (r"\bgit\s+(push\s+.*(--force|-f)\b|reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D|checkout\s+--\s|restore\s+\.)", "destructive git"),
    (r"\bgit\s+push\b", "git push (publishing)"),
    (r"\b(netsh\s+advfirewall|New-NetFirewallRule|Set-NetFirewallProfile|Remove-NetFirewallRule)\b", "firewall change"),
    (r"\b(reg\s+(add|delete)|Set-ItemProperty\s+.*HK|New-ItemProperty\s+.*HK|Remove-Item\s+.*HK)", "registry change"),
    (r"\b(net\s+user|Set-LocalUser|Add-LocalGroupMember|cmdkey|runas)\b", "credential/account change"),
    (r"\b(Set-MpPreference|Add-MpPreference|Set-ExecutionPolicy)\b", "security setting change"),
    (r"\b(shutdown|Restart-Computer|Stop-Computer)\b", "power action"),
    (r"\b(Stop-Process|taskkill)\b.*(-Force|/f)", "forced process kill"),
    (r"\b(Invoke-WebRequest|Invoke-RestMethod|iwr|irm|curl|wget)\b.*\|\s*(iex|Invoke-Expression|sh|bash|powershell)", "remote script execution"),
    (r"\|\s*(iex|Invoke-Expression)\b", "piped script execution"),
    (r"\bschtasks\b.*/create|Register-ScheduledTask", "scheduled task creation"),
    (r"\b(bcdedit|sfc|dism)\b", "system repair/boot change"),
]
_BLOCKED_SHELL = [
    (r"\b(cipher\s+/w|sdelete)\b", "secure wipe"),
]

# --- UI/window classification ------------------------------------------------------
_BLOCKED_WINDOWS = [
    (r"user account control", "UAC secure desktop"),
    (r"windows security|windows hello|sign in|sign-in|log in to windows", "authentication prompt"),
    (r"\bcaptcha\b|i'm not a robot|verify you are human", "CAPTCHA"),
    (r"two-factor|2fa|authenticator|verification code", "MFA"),
    (r"credential manager|enter your credentials", "credential prompt"),
]
_BLOCKED_PROCS = {"consent.exe", "logonui.exe", "credentialuibroker.exe", "lockapp.exe"}

# --- file classification -----------------------------------------------------------
_PROTECTED_PATHS = [r"^[a-z]:\\windows\b", r"^[a-z]:\\program files", r"^[a-z]:\\users\\[^\\]+\\appdata\\roaming\\microsoft\\credentials",
                    r"\\\.ssh\\", r"\\\.gnupg\\", r"\\\.aws\\credentials", r"\\\.claude\\\.credentials"]

_SECRET_RE = re.compile(r"(?i)((api[_-]?key|token|secret|password|passwd|pwd|authorization|bearer|credential)[\"'\s:=]+)([^\s\"',;]{6,})")
_KEY_SHAPES = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|ts_[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})\b")


def redact(text: str) -> str:
    if not text:
        return text
    text = _SECRET_RE.sub(lambda m: m.group(1) + "***", text)
    return _KEY_SHAPES.sub("***", text)


def classify_shell(command: str) -> Verdict:
    c = command or ""
    for pat, why in _BLOCKED_SHELL:
        if re.search(pat, c, re.I):
            return Verdict(BLOCKED, why)
    for pat, why in _CONFIRM_SHELL:
        if re.search(pat, c, re.I):
            return Verdict(CONFIRM, why)
    return Verdict(SAFE)


def classify_file(action: str, path: str, dest: str | None = None) -> Verdict:
    p = (path or "").replace("/", "\\").lower()
    if action in ("delete", "move", "rename", "write") and any(re.search(pp, p, re.I) for pp in _PROTECTED_PATHS):
        return Verdict(BLOCKED if "credential" in p or ".ssh" in p or ".gnupg" in p or ".aws" in p or ".claude" in p
                       else CONFIRM, f"protected path: {path}")
    if action == "delete":
        return Verdict(CONFIRM, f"delete {path}")
    if action == "write" and dest is None and p.endswith((".exe", ".dll", ".sys", ".bat", ".cmd", ".ps1", ".vbs")):
        return Verdict(CONFIRM, f"writing executable content: {path}")
    return Verdict(SAFE)


def classify_window(title: str | None, process: str | None) -> Verdict:
    t = (title or "").lower()
    pr = (process or "").lower()
    if pr in _BLOCKED_PROCS:
        return Verdict(BLOCKED, f"security process {process}")
    for pat, why in _BLOCKED_WINDOWS:
        if re.search(pat, t):
            return Verdict(BLOCKED, why)
    return Verdict(SAFE)


def classify_text_input(text: str, target_name: str | None = None) -> Verdict:
    """Typing into a password/PIN/OTP field is a credential action → confirm (never silently)."""
    n = (target_name or "").lower()
    if any(k in n for k in ("password", "passcode", "pin", "otp", "verification code", "cvv", "card number")):
        return Verdict(CONFIRM, f"typing into credential/payment field '{target_name}'")
    return Verdict(SAFE)


def classify_app(name_or_path: str) -> Verdict:
    n = (name_or_path or "").lower()
    if any(k in n for k in ("regedit", "gpedit", "secpol", "wf.msc", "services.msc", "diskmgmt")):
        return Verdict(CONFIRM, f"launching system administration tool {name_or_path}")
    return Verdict(SAFE)


def classify_click(el: dict | None, window: dict | None) -> Verdict:
    if window:
        v = classify_window(window.get("title"), window.get("process"))
        if not v.ok:
            return v
    name = ((el or {}).get("name") or "").lower()
    for k, why in (("delete permanently", "permanent deletion"), ("empty recycle bin", "permanent deletion"),
                   ("format", "disk format"), ("pay now", "payment"), ("place order", "purchase"), ("buy now", "purchase"),
                   ("confirm purchase", "purchase"), ("send money", "money transfer"), ("transfer", "money transfer"),
                   ("publish", "publishing"), ("post", "publishing"), ("send", "sending"), ("uninstall", "uninstall"),
                   ("factory reset", "reset"), ("reset this pc", "reset"), ("turn off firewall", "firewall")):
        if name == k or name.startswith(k + " ") or name.endswith(" " + k):
            return Verdict(CONFIRM, f"clicking '{el.get('name')}' ({why})")
    return Verdict(SAFE)
