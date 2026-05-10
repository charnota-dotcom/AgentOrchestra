@echo off
rem Show whatever subscription / usage info the local CLIs expose.
rem
rem Real-time per-message quota isn't queryable headlessly for either
rem Claude Code or Gemini — both gate that info behind their
rem interactive `/status` flow or the official web dashboards.  This
rem script runs every public status command we know about and prints
rem links to the dashboards so you can drill in if needed.

setlocal

echo ================ Subscription / usage check ================
echo Date:    %DATE% %TIME%
echo.

echo --- Claude Code ---
where claude >nul 2>&1
if errorlevel 1 (
    echo claude: NOT FOUND on PATH.  Run scripts\test-claude.cmd first.
) else (
    claude --version
    echo.
    echo Trying ``claude status`` ^(may not exist in older versions^):
    claude status 2>&1
    echo.
    echo Per-message remaining-quota requires the interactive ``/status``
    echo flow ^(piping ``/status`` into ``claude`` does NOT work — slash
    echo commands are only honoured in interactive mode^).  Open a
    echo terminal, run:  claude   then type:  /status
    echo.
    echo Pro / Max plan dashboard:
    echo   https://claude.ai/settings/usage
)
echo.

echo --- Gemini CLI ---
where gemini >nul 2>&1
if errorlevel 1 (
    echo gemini: NOT FOUND on PATH.  Run scripts\test-gemini.cmd first.
) else (
    gemini --version
    echo.
    echo Trying ``gemini status`` ^(may not exist in older versions^):
    gemini status 2>&1
    echo.
    echo Per-tier quota: see the dashboards below.  ``gemini /quota``
    echo is not a real command and the older script versions calling
    echo it produced misleading "command not found" output.
    echo.
    echo Subscription dashboard:
    echo   https://aistudio.google.com/app/apikey
    echo   https://gemini.google.com/  ^(plan + usage^)
)
echo.

echo --- Note ---
echo Per-message remaining-quota numbers are not reliably available
echo from headless CLI calls for either provider.  Use the interactive
echo ``claude`` then type ``/status`` for a live readout, or check the
echo dashboards above.
echo.

echo ================ End check ================
echo.
echo Press any key to close this window.
pause >nul

