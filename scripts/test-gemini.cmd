@echo off
rem Smoke-test the local Gemini CLI.  Same three checks as
rem test-claude.cmd, applied to the gemini binary.
rem
rem If the headless call fails for auth reasons, run `gemini` once
rem interactively to log in.  Credentials persist after that.

echo ================ Gemini CLI test ================
echo.

where gemini >nul 2>&1
if errorlevel 1 (
    echo gemini: NOT FOUND on PATH.
    echo.
    echo Install with:
    echo     npm install -g @google/gemini-cli
    echo Then run this test again.
    echo.
    echo Press any key to close.
    pause >nul
    exit /b 1
)

echo --- gemini --version ---
gemini --version
echo.

echo --- gemini -p "say hi in 5 words" ---
echo (waiting up to 30s for a reply...)
echo.
gemini -p "say hi in 5 words"
set GEMINI_EXIT=%ERRORLEVEL%
echo.

if %GEMINI_EXIT%==0 (
    echo Gemini CLI: OK — auth works, headless mode replied.
) else (
    echo Gemini CLI: returned exit %GEMINI_EXIT%.
    echo If the error mentioned auth or login, run:
    echo     gemini
    echo and complete the sign-in.  Credentials persist.  Then
    echo re-run this test.
)

echo.
echo ================ End test ================
echo.
echo Press any key to close.
pause >nul

