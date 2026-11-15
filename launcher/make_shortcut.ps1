# Creates %USERPROFILE%\Desktop\JARVIS.lnk pointing at the packaged launcher (no VS Code, no terminal).
# Default target: the venv's pythonw.exe (Authenticode-signed by the Python Software Foundation) running the app
# module, because Windows Smart App Control blocks freshly built unsigned executables such as dist\JARVIS.exe.
# Pass -UseExe to target dist\JARVIS.exe instead (fine on machines without Smart App Control).
param([string]$Root = "D:\JARVIS", [string]$Name = "JARVIS", [switch]$UseExe)
$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "dist\JARVIS.exe"
$pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$ico = Join-Path $Root "launcher\jarvis.ico"
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Name.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
if ($UseExe -and (Test-Path $exe)) {
    $s.TargetPath = $exe
    $s.Arguments = ""
} else {
    $s.TargetPath = $pyw
    $s.Arguments = "-m jarvis.app"
}
$s.WorkingDirectory = $Root
$s.IconLocation = "$ico,0"
$s.Description = "$Name - personal Windows computer agent"
$s.WindowStyle = 7
$s.Save()
Write-Output "created $lnk -> $($s.TargetPath) $($s.Arguments)"
