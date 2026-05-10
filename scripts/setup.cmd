@echo off
rem One-time setup for AgentOrchestra on Windows.  Idempotent: safe
rem to re-run any time you've changed the venv, upgraded Python, or
rem rebased your local checkout.
rem
rem 1. Verifies python is on PATH.
rem 2. Creates .venv if missing.
rem 3. Installs the project + its [gui] extras inside .venv.
rem 4. Installs the optional pyside6_annotator package if its source
rem    folder lives at the standard sibling location next to this
rem    repo (e.g. ...\GitHub\Annotator\pyside6_annotator_pkg).

setlocal
set REPO=%~dp0..

where python >nul 2>&1
if errorlevel 1 (
    echo [setup] python is not on PATH.
    echo [setup] Install Python 3.12 from https://www.python.org/downloads/
    echo [setup] During install, tick "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist "%REPO%\.venv\Scripts\activate.bat" (
    echo [setup] Creating virtual environment at %REPO%\.venv ...
    python -m venv "%REPO%\.venv" || goto :err
)

echo [setup] Installing project + [gui] extras (this can take a few minutes)...
call "%REPO%\.venv\Scripts\activate.bat"
python -m pip install --upgrade pip || goto :err
pip install -e "%REPO%[gui]" || goto :err

rem Optional: install the local pyside6_annotator if the operator
rem keeps it as a sibling repo.  Looks in ..\Annotator\pyside6_annotator_pkg
rem (the layout pyside6_annotator ships with).
set ANNOTATOR=%REPO%\..\Annotator\pyside6_annotator_pkg
if exist "%ANNOTATOR%\pyproject.toml" (
    echo [setup] Found local annotator at %ANNOTATOR% — installing.
    pip install -e "%ANNOTATOR%" || echo [setup] Annotator install failed; the GUI still works without the overlay.
) else (
    echo [setup] No annotator at %ANNOTATOR% — skipping (optional).
)

echo.
echo [setup] Done.  Double-click launch.cmd to start AgentOrchestra.
echo.
pause
exit /b 0

:err
echo.
echo [setup] Failed.  Read the error above, fix it, then re-run setup.cmd.
echo.
pause
exit /b 1
