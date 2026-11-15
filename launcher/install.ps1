# Kept for backwards compatibility. The real installer is setup.ps1 at the repo root (or just double-click setup.cmd).
param([string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)), [switch]$SkipExe)
$args2 = @()
if (-not $SkipExe) { $args2 += "-BuildExe" }
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "setup.ps1") -NoLaunch @args2
