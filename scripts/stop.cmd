@echo off
rem Stop AgentOrchestra: closes the Service and GUI windows opened
rem by launch.cmd.  Matches by window title so unrelated python.exe
rem processes (e.g. Jupyter, other apps) are left alone.

echo Stopping AgentOrchestra...

taskkill /FI "WINDOWTITLE eq AgentOrchestra Service*" /F /T >nul 2>&1
taskkill /FI "WINDOWTITLE eq AgentOrchestra GUI*"     /F /T >nul 2>&1

echo Done.
echo This window will close in a moment.
timeout /t 2 /nobreak >nul
