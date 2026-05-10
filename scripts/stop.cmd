@echo off
rem Stop AgentOrchestra.  Three-pass kill so nothing orphans:
rem
rem 1. Whatever window title we set on the GUI cmd host
rem    (`AgentOrchestra` from launch.cmd / ops.cmd).
rem 2. Whatever window title we set on a manually-started service
rem    cmd window (`AgentOrchestra Service`).
rem 3. Whatever process is actually listening on 127.0.0.1:8765.
rem    This catches the supervisor-spawned service which has
rem    ``CREATE_NO_WINDOW`` and so has NO window title for taskkill
rem    to match.  Bulletproof, regardless of how the service was
rem    started.

echo Stopping AgentOrchestra...

taskkill /FI "WINDOWTITLE eq AgentOrchestra*" /F /T >nul 2>&1
taskkill /FI "WINDOWTITLE eq AgentOrchestra Service*" /F /T >nul 2>&1

rem Kill anything listening on the orchestrator port.  Skips the
rem header line, picks the PID column (5th token), kills it.
for /f "tokens=5" %%P in (
    'netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"'
) do (
    echo   killing PID %%P (listening on :8765)
    taskkill /F /PID %%P >nul 2>&1
)

echo Done.
echo.
echo Press any key to close this window.
pause >nul
