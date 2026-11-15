# JARVIS installer / rebuilder. Run from anywhere:  powershell -ExecutionPolicy Bypass -File D:\JARVIS\launcher\install.ps1
# Creates the venv, installs pinned deps, downloads the Whisper model, builds dist\JARVIS.exe, trusts the project for
# Claude Code, and creates Desktop\JARVIS.lnk. Idempotent.
param([string]$Root = "D:\JARVIS", [switch]$SkipExe)
$ErrorActionPreference = "Stop"
Set-Location $Root
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) { python -m venv "$Root\.venv" }
$py = "$Root\.venv\Scripts\python.exe"
# keep pip's download cache on the project (D:) drive, not C:\Users\...\AppData\Local\pip\cache
$env:PIP_CACHE_DIR = "$Root\.cache\pip"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r "$Root\requirements.txt"
$env:HF_HOME = "$Root\models\hf"
& $py -c "from jarvis.voice.whisper import engine; print('whisper:', engine().ensure_downloaded())"
& $py -m jarvis.app.icon "$Root\launcher\jarvis.ico" | Out-Null
& $py -m jarvis.ui.build | Out-Null   # inline the thinking-orbs engine into the orb page
if (-not $SkipExe) {
    Push-Location "$Root\launcher"
    & $py -m PyInstaller --noconfirm --onefile --noconsole --name JARVIS --icon "$Root\launcher\jarvis.ico" `
        --distpath "$Root\dist" --workpath "$Root\dist\build" --specpath "$Root\dist\build" jarvis_launcher.py | Out-Null
    Pop-Location
}
# Claude Code: trust the workspace so .claude/settings.json permissions and .mcp.json apply.
& $py - @'
import json, os
p = os.path.expanduser("~/.claude.json")
d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
for key in ("D:/JARVIS", "D:\JARVIS"):
    proj = d.setdefault("projects", {}).setdefault(key, {})
    proj["hasTrustDialogAccepted"] = True
    s = proj.setdefault("enabledMcpjsonServers", [])
    if "jarvis" not in s: s.append("jarvis")
json.dump(d, open(p, "w", encoding="utf-8"), indent=2)
print("claude: workspace trusted")
'@
& powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\launcher\make_shortcut.ps1" -Root $Root
if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) {
    Write-Output "Claude Code CLI not found. Install: irm https://claude.ai/install.ps1 | iex   (then run 'claude' once to log in)"
}
Write-Output "JARVIS installed. Double-click Desktop\JARVIS.lnk."
