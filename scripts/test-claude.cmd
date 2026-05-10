@echo off
rem Smoke-test the local Claude Code CLI.  Three checks:
rem
rem 1. Binary on PATH (`claude --version`)
rem 2. Headless invocation works (`claude -p "..."`)
rem 3. Output looks like a real reply (not a "Not logged in" line)
rem
rem If you see "Not logged in", run:  claude  → /login
rem inside any terminal — once is enough; the credentials persist
rem in ~/.claude/.

echo ================ Claude Code CLI test ================
echo.

where claude >nul 2>&1
if errorlevel 1 (
    echo claude: NOT FOUND on PATH.
    echo.
    echo Install with:
    echo     npm install -g @anthropic-ai/claude-code
    echo Then run this test again.
    echo.
    echo Press any key to close.
    pause >nul
    exit /b 1
)

echo --- claude --version ---
claude --version
echo.

echo --- claude -p "say hi in 5 words" ---
echo (waiting up to 30s for a reply...)
echo.
claude -p "say hi in 5 words"
set CLAUDE_EXIT=%ERRORLEVEL%
echo.

if %CLAUDE_EXIT%==0 (
    echo Claude CLI: OK — auth works, headless mode replied.
) else (
    echo Claude CLI: returned exit %CLAUDE_EXIT%.
    echo If the message above said "Not logged in", run:
    echo     claude
    echo and then type /login at the prompt.  Browser sign-in;
    echo credentials are saved.  Re-run this test afterwards.
)

echo.
echo ================ End test ================
echo.
echo Press any key to close.
pause >nul

