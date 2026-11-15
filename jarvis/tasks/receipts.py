"""Task receipts / proof-of-work (OpenMuse integration §11, §38).

A concise, inspectable record of what a task actually did — built from JARVIS's REAL runtime signals (the tools that
ran, the verification result, retries/failures), never from the model's self-claim (§11: "use existing verification
rather than trusting the model's claim"). Stored on the task; the HUD can open it (§38).
"""
from __future__ import annotations

from .store import Task, TaskState

# a few tool names → human phrasing (unknown tools fall back to the raw name)
_TOOL_VERB = {
    "computer_app": "launched an app", "computer_key": "sent shortcuts", "computer_shell": "ran a command",
    "computer_file": "file operation", "computer_window": "managed a window", "computer_click": "clicked a control",
    "computer_type": "typed text", "computer_observe": "inspected the UI", "computer_run": "ran a batch",
    "computer_act": "drove a UI goal", "computer_screen": "read the screen", "computer_wait": "waited on state",
}


def build_receipt(task: Task) -> dict:
    """Structured receipt from real signals. `actions` come from the tool-call counts the runtime recorded."""
    tools = task.tools or {}
    actions = []
    for name, count in tools.items():
        short = str(name).replace("mcp__jarvis__", "")
        verb = _TOOL_VERB.get(short, short)
        actions.append({"tool": short, "count": count, "label": f"{verb} ×{count}" if count > 1 else verb})
    return {
        "goal": task.goal,
        "actions": actions,
        "action_total": sum(tools.values()),
        "verified": task.verified,                       # None = not applicable; True/False from real verification
        "retries": task.retry_count,
        "failure": task.failure_reason,
        "result": (task.result or "").strip()[:600] or None,
        "state": task.state,
        "duration_ms": task.duration_ms,
    }


def format_receipt(task: Task) -> str:
    """Plain-text receipt for the HUD / console (§38). Compact, honest, no fabricated claims."""
    r = task.receipt or build_receipt(task)
    ok = task.state == TaskState.COMPLETED.value
    head = "TASK COMPLETE ✓" if ok else ("TASK CANCELLED" if task.state == TaskState.CANCELLED.value else "TASK FAILED ✗")
    lines = [head, "", f"Goal: {r['goal']}"]
    if r["actions"]:
        lines.append("Actions:")
        lines += [f"  • {a['label']}" for a in r["actions"]]
    elif r["action_total"] == 0:
        lines.append("Actions: (answered directly — no computer actions)")
    if r["verified"] is True:
        lines.append("Verification: ✓ verified")
    elif r["verified"] is False:
        lines.append("Verification: ✗ NOT verified")
    if r["retries"]:
        lines.append(f"Retries: {r['retries']}")
    if r["failure"]:
        lines.append(f"Failure: {r['failure']}")
    if r["duration_ms"] is not None:
        secs = r["duration_ms"] / 1000
        lines.append(f"Duration: {secs:.1f}s")
    if r["result"]:
        lines += ["", r["result"]]
    return "\n".join(lines)
