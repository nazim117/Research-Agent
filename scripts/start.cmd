@echo off
REM Double-clickable wrapper for start.ps1 — bypasses PowerShell's default
REM execution-policy restriction on unsigned local scripts, without needing
REM you to change that policy system-wide.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
