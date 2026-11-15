@echo off
rem JARVIS - GNANI Edition: one-click setup. Double-click this file, or run it from any terminal.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
