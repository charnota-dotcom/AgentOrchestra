@echo off
rem Stop AgentOrchestra.  Closes the GUI window (which atexit-kills
rem the supervised service) and any orphaned service started directly.

echo Stopping AgentOrchestra...

rem GUI window (titled "AgentOrchestra") and any of its child
rem python.exe processes (the service the GUI spawned).
taskkill /FI "WINDOWTITLE eq AgentOrchestra*" /F /T >nul 2>&1

rem Any service started by hand from a separate cmd that didn't
rem inherit the GUI window title.
taskkill /FI "WINDOWTITLE eq AgentOrchestra Service*" /F /T >nul 2>&1

echo Done.
echo.
echo Press any key to close this window.
pause >nul
