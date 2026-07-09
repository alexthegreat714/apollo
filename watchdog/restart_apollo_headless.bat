@echo off
setlocal
set "HELPER=%~dp0..\..\tools\restart_agent_headless.ps1"
if not exist "%HELPER%" exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%HELPER%" -AgentName "Apollo" -EntryMode script -EntryTarget "app.py" -LogSubdir "logs"
endlocal & exit /b %ERRORLEVEL%
