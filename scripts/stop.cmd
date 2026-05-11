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

rem Exact match (no trailing *) so we don't accidentally also kill
rem "AgentOrchestra Ops Panel" — the panel is the host that's
rem running THIS script; killing it leaves the operator stranded
rem with no UI to click further commands.  See annotation about
rem Update + Restart not re-opening the panel.
taskkill /FI "WINDOWTITLE eq AgentOrchestra" /F /T >nul 2>&1
taskkill /FI "WINDOWTITLE eq AgentOrchestra Service*" /F /T >nul 2>&1

rem Kill anything listening on the orchestrator port.  Skips the
rem header line, picks the PID column (5th token), kills it.
for /f "tokens=5" %%P in (
    'netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"'
) do (
    echo   killing PID %%P (listening on :8765)
    taskkill /F /PID %%P >nul 2>&1
)

rem Belt-and-braces: kill orphan service processes whose command
rem line includes apps.service.main, regardless of port binding.
rem Mirrors restart.cmd — same root cause for both: a stale service
rem from a previous session can survive a port-only kill.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine -like '*apps.service.main*' } | ForEach-Object { Write-Host \"  killing orphan service PID $($_.ProcessId)\"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done.
echo.
echo Press any key to close this window.
pause >nul

