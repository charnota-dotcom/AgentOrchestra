@echo off
rem WIPE LOCAL STATE.  Removes:
rem
rem  * the SQLite store (~/.local/share/agentorchestra/agentorchestra.sqlite)
rem  * the first-run sentinel (so the wizard runs again)
rem  * the annotation log + annotation data dir
rem
rem Does NOT touch:
rem  * your repo source files
rem  * git history
rem  * Claude Code or Gemini CLI auth (your subscriptions stay
rem    signed in)
rem
rem Use this when something's gone weird with the local DB and you
rem want a clean slate.

setlocal
set DATA=%USERPROFILE%\.local\share\agentorchestra

echo This will delete: %DATA%
echo.
echo It will NOT touch your repo, git history, or CLI auth.
echo.
choice /C YN /M "Continue"
if errorlevel 2 (
    echo Aborted.
    exit /b 0
)

if exist "%DATA%" (
    rmdir /S /Q "%DATA%" || (
        echo [reset] Failed to remove %DATA%.  Close any AgentOrchestra windows first.
        pause
        exit /b 1
    )
)
echo [reset] Done.  Next launch will run the first-run wizard.
pause

