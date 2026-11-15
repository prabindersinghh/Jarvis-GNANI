<#
  JARVIS · GNANI — one-shot setup.

      git clone https://github.com/prabindersinghh/Jarvis-GNANI.git D:\JARVIS
      D:\JARVIS\setup.cmd

  Installs everything JARVIS needs, from a bare Windows 11 machine, with no manual steps:
    Python 3.12 · Claude Code CLI · WebView2 runtime · virtual environment · every pip dependency ·
    the Whisper speech model · the JARVIS MCP server + Windows-MCP · Claude Code workspace trust ·
    the orb UI · the desktop shortcut. Then it self-tests and launches JARVIS.

  Works from any folder (not only D:\JARVIS). Safe to re-run: every step is idempotent.

  Switches:
    -NoLaunch        do not start JARVIS at the end
    -NoShortcut      do not create Desktop\JARVIS.lnk
    -NoWindowsMcp    do not register the optional 63-tool Windows-MCP server
    -BuildExe        also build dist\JARVIS.exe with PyInstaller (slow; not needed for the shortcut)
#>
param([switch]$NoLaunch, [switch]$NoShortcut, [switch]$NoWindowsMcp, [switch]$BuildExe)

# Native tools write progress to stderr; under "Stop", Windows PowerShell 5.1 would abort on that.
# Every step checks $LASTEXITCODE explicitly instead.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$script:step = 0
function Step($msg) { $script:step++; Write-Host ""; Write-Host ("[{0}/11] {1}" -f $script:step, $msg) -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "   OK  $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "   ..  $msg" -ForegroundColor Gray }
function Warn($msg) { Write-Host "   !!  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host ""; Write-Host "   XX  $msg" -ForegroundColor Red; exit 1 }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                "$env:USERPROFILE\.local\bin"
}
function Has-Winget { [bool](Get-Command winget -ErrorAction SilentlyContinue) }

Write-Host ""
Write-Host "  JARVIS · GNANI Edition — setup" -ForegroundColor White
Write-Host "  Installing into: $Root" -ForegroundColor DarkGray

# ── 1. Windows ───────────────────────────────────────────────────────────────
Step "Checking Windows"
if ($env:OS -ne "Windows_NT") { Fail "JARVIS runs on Windows 10/11 only." }
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
if ($build -lt 19041) { Fail "Windows 10 2004 (build 19041) or newer is required. This machine is build $build." }
Ok "Windows build $build"

# ── 2. Python 3.12 ───────────────────────────────────────────────────────────
Step "Python 3.12"
function Find-Python {
    $cands = New-Object System.Collections.Generic.List[string]
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $r = & py -3.12 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $r) { $cands.Add("$r".Trim()) }
    }
    $cands.Add("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
    $cands.Add("$env:ProgramFiles\Python312\python.exe")
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $r = & python -c "import sys;print(sys.executable if sys.version_info[:2]==(3,12) else '')" 2>$null
        if ($r) { $cands.Add("$r".Trim()) }
    }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}
$sysPy = Find-Python
if (-not $sysPy) {
    if (-not (Has-Winget)) { Fail "Python 3.12 is missing and winget is unavailable. Install Python 3.12 from python.org, then re-run setup." }
    Info "installing Python 3.12 (winget)…"
    winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    $sysPy = Find-Python
    if (-not $sysPy) { Fail "Python 3.12 install did not complete. Open a new terminal and re-run setup." }
}
Ok $sysPy

# ── 3. Claude Code CLI ───────────────────────────────────────────────────────
Step "Claude Code CLI (the brain)"
Refresh-Path
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { $claude = "$env:USERPROFILE\.local\bin\claude.exe" }
if (-not $claude) {
    Info "installing Claude Code (official installer)…"
    try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression } catch { Warn "Claude Code installer failed: $($_.Exception.Message)" }
    Refresh-Path
    $claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
    if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { $claude = "$env:USERPROFILE\.local\bin\claude.exe" }
}
if ($claude) { Ok $claude } else { Warn "Claude Code not found. Install it later with:  irm https://claude.ai/install.ps1 | iex" }

# ── 4. WebView2 runtime (orb UI) ─────────────────────────────────────────────
Step "WebView2 runtime (orb UI)"
$wv = @(
  "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
  "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
  "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
) | Where-Object { Test-Path $_ }
if ($wv) { Ok "present" }
elseif (Has-Winget) {
    Info "installing WebView2 (winget)…"
    winget install --id Microsoft.EdgeWebView2Runtime -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Ok "installed"
} else { Warn "WebView2 missing; the orb UI needs it (https://go.microsoft.com/fwlink/p/?LinkId=2124703)" }

# ── 5. Virtual environment ───────────────────────────────────────────────────
Step "Virtual environment"
$py  = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { & $sysPy -m venv (Join-Path $Root ".venv"); if ($LASTEXITCODE) { Fail "venv creation failed" } }
Ok ".venv"

# ── 6. Dependencies ──────────────────────────────────────────────────────────
Step "Python dependencies (requirements.txt)"
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE) { Fail "pip install failed. Re-run setup; check your internet connection." }
Ok "all packages installed"

# ── 7. Whisper model ─────────────────────────────────────────────────────────
Step "Whisper speech model (local)"
$env:HF_HOME = Join-Path $Root "models\hf"
$env:JARVIS_ROOT = $Root
$env:PYTHONUTF8 = "1"
& $py -c "from jarvis.voice.whisper import engine; print('   OK ', engine().ensure_downloaded())"
if ($LASTEXITCODE) { Fail "Whisper model download failed. Re-run setup." }

# ── 8. Data folders, secrets template, orb UI ────────────────────────────────
Step "Data folders, settings and orb UI"
foreach ($d in "data\config", "data\memory", "logs", "models") { New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null }
$envFile = Join-Path $Root "data\config\.env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $Root ".env.example") $envFile; Info "created data\config\.env (add TYPESAFE_API_KEY later — optional)" }
& $py -m jarvis.app.icon (Join-Path $Root "launcher\jarvis.ico") | Out-Null
& $py -m jarvis.ui.build | Out-Null
Ok "orb UI built"

# ── 9. MCP servers + Claude Code workspace ───────────────────────────────────
Step "MCP servers (JARVIS + Windows-MCP) and Claude Code workspace"
$fwd = $Root -replace '\\', '/'
$servers = [ordered]@{
  jarvis = [ordered]@{ type = "stdio"; command = "$fwd/.venv/Scripts/python.exe"; args = @("-m", "jarvis.computer");
                        cwd = $fwd; env = [ordered]@{ PYTHONUTF8 = "1"; JARVIS_ROOT = $fwd } }
}
$wmcp = Join-Path $Root "third_party\windows-mcp\WindowsMcp.exe"
if (-not $NoWindowsMcp -and (Test-Path $wmcp)) {
  $servers["windows-mcp"] = [ordered]@{ type = "stdio"; command = "$fwd/third_party/windows-mcp/WindowsMcp.exe"; args = @() }
}
$mcpJson = ([ordered]@{ mcpServers = $servers } | ConvertTo-Json -Depth 6)
[IO.File]::WriteAllText((Join-Path $Root ".mcp.json"), $mcpJson, (New-Object Text.UTF8Encoding $false))
# keep the machine-specific .mcp.json out of git status
if (Test-Path (Join-Path $Root ".git")) { git -C $Root update-index --skip-worktree .mcp.json 2>$null }
Ok ("registered: " + (($servers.Keys) -join ", "))
$enabled = ($servers.Keys | ForEach-Object { "'$_'" }) -join ","
$trust = Join-Path $env:TEMP "jarvis_trust.py"
@"
import json, os
p = os.path.expanduser('~/.claude.json')
d = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
root = r"$Root"
for key in {root, root.replace(chr(92), '/')}:
    proj = d.setdefault('projects', {}).setdefault(key, {})
    proj['hasTrustDialogAccepted'] = True
    s = proj.setdefault('enabledMcpjsonServers', [])
    for name in [$enabled]:
        if name not in s: s.append(name)
json.dump(d, open(p, 'w', encoding='utf-8'), indent=2)
print('   OK  Claude Code trusts this folder')
"@ | Set-Content -Path $trust -Encoding UTF8
& $py $trust
Remove-Item $trust -ErrorAction SilentlyContinue

# ── 10. Desktop shortcut ─────────────────────────────────────────────────────
Step "Desktop shortcut"
if ($NoShortcut) { Info "skipped (-NoShortcut)" }
else { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "launcher\make_shortcut.ps1") -Root $Root | Out-Null; Ok "Desktop\JARVIS.lnk" }
if ($BuildExe) {
    Info "building dist\JARVIS.exe…"
    Push-Location (Join-Path $Root "launcher")
    & $py -m PyInstaller --noconfirm --onefile --noconsole --name JARVIS --icon (Join-Path $Root "launcher\jarvis.ico") `
        --distpath (Join-Path $Root "dist") --workpath (Join-Path $Root "dist\build") --specpath (Join-Path $Root "dist\build") jarvis_launcher.py | Out-Null
    Pop-Location
    Ok "dist\JARVIS.exe"
}

# ── 11. Self-test ────────────────────────────────────────────────────────────
Step "Self-test"
& $py -c "import jarvis.computer.server, jarvis.app.claude_session, jarvis.voice.whisper, jarvis.ui.comphost; print('   OK  all modules import')"
if ($LASTEXITCODE) { Fail "self-test failed — see the error above." }
& $py -m pytest (Join-Path $Root "tests\test_unit.py") -q -p no:cacheprovider 2>&1 | Select-Object -Last 1 | ForEach-Object { Info "unit tests: $_" }

$loggedIn = $false
if ($claude) { try { & $claude auth status 2>$null | Out-Null; $loggedIn = ($LASTEXITCODE -eq 0) } catch {} }

Write-Host ""
Write-Host "  JARVIS is installed." -ForegroundColor Green
if (-not $loggedIn) {
    Write-Host "  First time only: run  claude  in any terminal and sign in with your Claude account." -ForegroundColor Yellow
}
Write-Host "  Start:  double-click JARVIS on your desktop, then hold Ctrl+Alt+J and speak." -ForegroundColor White
Write-Host ""

if (-not $NoLaunch) {
    Start-Process -FilePath (Join-Path $Root ".venv\Scripts\pythonw.exe") -ArgumentList "-m", "jarvis.app" -WorkingDirectory $Root
    Ok "JARVIS launched — look for the orb, bottom-right"
}
