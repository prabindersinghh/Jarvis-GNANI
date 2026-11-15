# Optional: Windows-MCP (63 admin tools)

`third_party/windows-mcp/WindowsMcp.exe` is the prebuilt, MIT-licensed [Windows-MCP](https://github.com/danielsimonjr/Windows-MCP)
server (commit 2a74b3e). JARVIS does **not** load it by default — 63 extra tools cost Claude context on every turn and
overlap with `computer_*`. Enable it when you need registry/services/firewall/event-log/WMI/scheduled-task tooling:

```json
// add to D:\JARVIS\.mcp.json → "mcpServers"
"windows-mcp": { "type": "stdio", "command": "D:/JARVIS/third_party/windows-mcp/WindowsMcp.exe", "args": [] }
```

Its destructive tools require `confirm: true`; JARVIS's own safety policy does not wrap them, so keep it off unless needed.
First launch extracts native libs to `%TEMP%\.net\WindowsMcp\` (3–5 s).
