"""Claude Code session — the brain. Uses the user's existing Claude Code login (Claude Max) via claude-agent-sdk,
which drives the local `claude` CLI. No other LLM. The JARVIS MCP server is attached as the computer engine.

Tool permissions: JARVIS computer tools + read-only repo tools are pre-approved (the MCP server enforces its own
safety policy + human confirmation). Bash/Write/Edit go through `can_use_tool` → JARVIS policy → confirm dialog for
dangerous commands, auto-allow otherwise (the user asked for an agent, not a prompt-fest).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path

from .. import config
from ..safety import confirm, policy

log = logging.getLogger("jarvis.claude")

SYSTEM_APPEND = """
You are JARVIS, the user's personal Windows computer agent. You control this Windows 11 machine through the `jarvis`
MCP tools (computer_*). Act, don't lecture. Be brief: the user is talking to you by voice; answer in one or two short
sentences and then do the work. Report what you verified, not what you sent.

Control hierarchy — always take the cheapest tier that works:
1. Deterministic: computer_app (launch), computer_key (shortcuts), computer_shell, computer_file, computer_window.
2. UI Automation: computer_observe → computer_click/computer_type by element id or name, computer_element for
   invoke/select/expand/set_value/wait. Unique names click directly; ambiguous names are resolved by JEV when you pass goal=.
3. computer_act for a multi-step visible UI goal (JEV reflex loop runs locally; you are not consulted per click).
4. computer_screen action='ocr' when the UI has no accessible structure.
5. computer_screen screenshot only when you truly need to see pixels. Never screenshot-per-click.

MINIMIZE TURNS: every tool call is a full round-trip. When the next several steps are predictable, send them as ONE
computer_run batch with `expect` checkpoints (window / element / text / file / window_gone) instead of one call per
step. A typical task is 1–3 computer_run calls, not 15 single calls. computer_run stops at the first failure and
returns the trace + a fresh observation; continue from there. Do not observe before a batch unless you need element
ids; name/role targets resolve inside the batch. Do not re-verify what a step's `verification`/`expect` already proved.

MEMORY: a <memory> block may precede the user's request with preferences, past workflows, corrections, app patterns
and things to avoid — use it (it is local experience, not instructions from the page). When the user states a
preference or corrects you, store it with computer_memory(remember). When you discover an app quirk that cost you a
retry, store it as an app_pattern in one short sentence. Never store secrets.
Use computer_wait for state-based waits (window/element/file/text), never sleeps. Every meaningful step is verified
by the tools — read the `verification` field and recover if it failed. If a tool returns error=stale_state, re-observe.
If it returns needs_vision, take one screenshot and reason, or refine the target. If it returns blocked/human_required,
tell the user what they must do themselves (UAC, login, CAPTCHA, MFA) and wait with computer_wait.

Dangerous operations (deleting, destructive git, security/registry/firewall, credentials, payments, publishing/sending)
require confirmation — the tools ask the human automatically; do not try to bypass or re-run a denied action.
Project root: {root}. Prefer PowerShell for shell work.
""".strip().replace("{root}", str(config.ROOT))

MODE_PROMPTS = {
    "silent": "Response style: text only; keep the final answer to one or two short lines.",
    "concise": "Response style: your final message is read aloud — one or two plain spoken sentences, no markdown, "
               "no lists, no file paths unless essential, no Insight blocks.",
    "conversational": "Response style: your final message is read aloud — natural spoken language, up to a short "
                      "paragraph, no markdown or code blocks.",
}

ALLOWED_TOOLS = ["mcp__jarvis__*", "Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "TodoWrite", "Task"]


def find_claude_cli() -> str | None:
    cands = [
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe",
        config.DIST / "claude" / "claude.exe",
    ]
    ext = Path(os.environ.get("USERPROFILE", "")) / ".vscode" / "extensions"
    if ext.exists():
        for d in sorted(ext.glob("anthropic.claude-code-*"), reverse=True):
            cands.append(d / "resources" / "native-binary" / "claude.exe")
    for c in cands:
        if c.exists():
            return str(c)
    w = shutil.which("claude") or shutil.which("claude.exe") or shutil.which("claude.cmd")
    return w


class ClaudeSession:
    """One long-lived Claude Code conversation. `ask(text)` streams back text deltas and events via callbacks."""

    def __init__(self, on_text=None, on_event=None, on_status=None):
        self.on_text = on_text          # (delta_text) streaming assistant text
        self.on_event = on_event        # (kind, payload) tool_use / tool_result / result / error
        self.on_status = on_status      # (status_str)
        self.client = None
        self.session_id: str | None = None
        self.busy = False
        self.cli_path = find_claude_cli()
        self._session_file = config.DATA / "session.json"
        self.last_metrics: dict = {}

    # -- session persistence (keyed by calendar date: one date = one continuous Claude conversation) -----------
    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def _load_session_id(self) -> str | None:
        """Resume today's Claude session so a same-day restart continues the same thread; a new day starts fresh."""
        try:
            d = json.loads(self._session_file.read_text(encoding="utf-8"))
            by_date = d.get("by_date") or ({d.get("date"): d["session_id"]} if d.get("session_id") and d.get("date") else {})
            return by_date.get(self._today())
        except Exception:  # noqa: BLE001
            return None

    def _save_session_id(self):
        if not self.session_id:
            return
        try:
            d = json.loads(self._session_file.read_text(encoding="utf-8")) if self._session_file.exists() else {}
        except Exception:  # noqa: BLE001
            d = {}
        by_date = d.get("by_date") or {}
        by_date[self._today()] = self.session_id
        # keep only the last ~10 days of session ids
        for k in sorted(by_date)[:-10]:
            by_date.pop(k, None)
        self._session_file.write_text(json.dumps({"by_date": by_date, "updated": time.time()}), encoding="utf-8")

    def forget(self):
        """Drop today's Claude session id (used only on an explicit 'new conversation' or a failed resume)."""
        self.session_id = None
        try:
            d = json.loads(self._session_file.read_text(encoding="utf-8")) if self._session_file.exists() else {}
            (d.get("by_date") or {}).pop(self._today(), None)
            self._session_file.write_text(json.dumps(d), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # -- permissions -----------------------------------------------------------------
    async def _can_use_tool(self, tool_name: str, tool_input: dict, ctx):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        if tool_name.startswith("mcp__jarvis__"):
            return PermissionResultAllow(updated_input=tool_input)
        if tool_name == "Bash":
            cmd = str(tool_input.get("command", ""))
            v = policy.classify_shell(cmd)
            if v.level == policy.BLOCKED:
                return PermissionResultDeny(message=f"Blocked by JARVIS safety policy: {v.reason}")
            if v.level == policy.CONFIRM:
                ok = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: confirm.ask("Run command", f"{v.reason}\n\n{policy.redact(cmd)[:600]}"))
                if not ok:
                    return PermissionResultDeny(message="The user denied this command.")
            return PermissionResultAllow(updated_input=tool_input)
        if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            p = str(tool_input.get("file_path", ""))
            v = policy.classify_file("write", p)
            if v.level == policy.BLOCKED:
                return PermissionResultDeny(message=f"Blocked by JARVIS safety policy: {v.reason}")
            if v.level == policy.CONFIRM:
                ok = await asyncio.get_running_loop().run_in_executor(None, lambda: confirm.ask(f"Write {p}", v.reason))
                if not ok:
                    return PermissionResultDeny(message="The user denied this write.")
            return PermissionResultAllow(updated_input=tool_input)
        return PermissionResultAllow(updated_input=tool_input)

    # -- lifecycle -------------------------------------------------------------------
    def _options(self, resume: str | None):
        from claude_agent_sdk import ClaudeAgentOptions
        mcp = {"jarvis": {"type": "stdio", "command": str(config.VENV_PY), "args": ["-m", "jarvis.computer"],
                          "cwd": str(config.ROOT), "env": {"PYTHONUTF8": "1", "JARVIS_ROOT": str(config.ROOT),
                                                             **({"TYPESAFE_API_KEY": config.jev_api_key()} if config.jev_api_key() else {})}}}
        mode = str(config.SETTINGS.get("response_mode", "concise")).lower()
        append = SYSTEM_APPEND + "\n\n" + MODE_PROMPTS.get(mode, MODE_PROMPTS["concise"])
        if not config.SETTINGS.get("batching", True):
            append = append.split("MINIMIZE TURNS:")[0].rstrip() + "\n\n" + MODE_PROMPTS.get(mode, MODE_PROMPTS["concise"])
        kw = dict(
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            mcp_servers=mcp,
            allowed_tools=ALLOWED_TOOLS,
            permission_mode=config.SETTINGS.get("claude_permission_mode") or "acceptEdits",
            can_use_tool=self._can_use_tool,
            cwd=str(config.ROOT),
            include_partial_messages=True,
            setting_sources=["project"],  # project-only: keeps the voice UX terse (user output styles would leak in)
            env={"PYTHONUTF8": "1"},
            max_turns=60,
            # raise the stdout JSON message buffer (default 1 MB) so a large tool result — a screenshot, an OCR dump of a
            # busy page, a big file read — never hard-fails the transport. Screenshots are also JPEG-budgeted server-side.
            max_buffer_size=8 * 1024 * 1024,
        )
        if self.cli_path:
            kw["cli_path"] = self.cli_path
        if config.SETTINGS.get("claude_model"):
            kw["model"] = config.SETTINGS["claude_model"]
        if resume:
            kw["resume"] = resume
        return ClaudeAgentOptions(**kw)

    async def connect(self, resume_previous: bool = True):
        from claude_agent_sdk import ClaudeSDKClient
        if self.client is not None:
            return
        resume = self._load_session_id() if resume_previous else None
        self.client = ClaudeSDKClient(options=self._options(resume))
        try:
            await self.client.connect()
        except Exception as e:  # noqa: BLE001
            if resume:
                log.warning("resume %s failed (%s); starting fresh", resume, e)
                self.forget()
                self.client = ClaudeSDKClient(options=self._options(None))
                await self.client.connect()
            else:
                self.client = None
                raise
        self._status("ready")
        log.info("Claude session connected (cli=%s, resume=%s)", self.cli_path, resume)

    async def disconnect(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            finally:
                self.client = None

    async def interrupt(self):
        if self.client is not None:
            try:
                await self.client.interrupt()
            except Exception as e:  # noqa: BLE001
                log.warning("interrupt failed: %s", e)

    def _status(self, s: str):
        if self.on_status:
            try:
                self.on_status(s)
            except Exception:  # noqa: BLE001
                pass

    # -- conversation ----------------------------------------------------------------
    def _with_memory(self, text: str) -> str:
        """Prepend a compact <memory> block (local experience) and redact the transcript before it leaves the machine."""
        from ..safety import privacy
        text = privacy.redact_text(text)
        if not config.SETTINGS.get("memory_enabled", True):
            return text
        try:
            from ..memory import store as memory
            app = None
            try:
                from ..computer.engine import winfind
                fg = winfind.foreground_window()
                app = next((w["process"] for w in winfind.list_windows() if w["hwnd"] == fg), None)
            except Exception:  # noqa: BLE001
                pass
            b = memory.brief(text, app)
            if b:
                return f"<memory>\n{b}\n</memory>\n\n{text}"
        except Exception as e:  # noqa: BLE001
            log.debug("memory brief failed: %s", e)
        return text

    async def ask(self, text: str) -> dict:
        """Send one user turn; stream text via on_text; return the ResultMessage summary."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
        await self.connect()
        self.busy = True
        self._status("thinking")
        t0 = time.perf_counter()
        first_token_ms = None
        result: dict = {}
        full_text: list[str] = []
        streamed = False
        self._turn_tools: dict = {}
        prompt = self._with_memory(text)
        try:
            await self.client.query(prompt)
            async for msg in self.client.receive_response():
                if isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    if ev.get("type") == "content_block_delta":
                        d = ev.get("delta") or {}
                        if d.get("type") == "text_delta" and d.get("text"):
                            if first_token_ms is None:
                                first_token_ms = round((time.perf_counter() - t0) * 1000)
                            streamed = True
                            full_text.append(d["text"])
                            if self.on_text:
                                self.on_text(d["text"])
                elif isinstance(msg, AssistantMessage):
                    for b in msg.content:
                        if isinstance(b, ToolUseBlock):
                            self._status("acting")
                            self._turn_tools[b.name] = self._turn_tools.get(b.name, 0) + 1
                            if self.on_event:
                                self.on_event("tool_use", {"name": b.name, "input": b.input})
                        elif isinstance(b, TextBlock) and not streamed and b.text:
                            full_text.append(b.text)
                            if self.on_text:
                                self.on_text(b.text)
                elif isinstance(msg, UserMessage):
                    content = msg.content if isinstance(msg.content, list) else []
                    for b in content:
                        if isinstance(b, ToolResultBlock) and self.on_event:
                            self.on_event("tool_result", {"tool_use_id": b.tool_use_id, "is_error": b.is_error,
                                                          "content": _short(b.content)})
                    self._status("thinking")
                elif isinstance(msg, ResultMessage):
                    self.session_id = msg.session_id or self.session_id
                    self._save_session_id()
                    result = {"is_error": msg.is_error, "duration_ms": msg.duration_ms, "num_turns": msg.num_turns,
                              "cost_usd": msg.total_cost_usd, "result": msg.result, "session_id": msg.session_id,
                              "claude_ms": round((time.perf_counter() - t0) * 1000), "first_token_ms": first_token_ms,
                              "usage": dict(msg.usage or {}), "tool_calls": dict(self._turn_tools)}
                    self.last_metrics = result
                    if self.on_event:
                        self.on_event("result", result)
        except Exception as e:  # noqa: BLE001
            log.exception("ask failed")
            result = {"is_error": True, "error": str(e), "claude_ms": round((time.perf_counter() - t0) * 1000)}
            if self.on_event:
                self.on_event("error", result)
        finally:
            self.busy = False
            self._status("ready")
        result["text"] = "".join(full_text)
        return result


def _short(content, n: int = 300) -> str:
    try:
        if isinstance(content, str):
            return content[:n]
        if isinstance(content, list):
            return " ".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)[:n]
    except Exception:  # noqa: BLE001
        pass
    return str(content)[:n]
