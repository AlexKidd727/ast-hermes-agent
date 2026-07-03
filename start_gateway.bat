@echo off
setlocal

REM Always run from the project directory (same folder as this BAT).
cd /d "%~dp0"

REM Enable UTF-8 in Windows console.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo [1/2] Starting Hermes Gateway...
echo.

REM Resolve an explicit Python interpreter to avoid PATH/current-dir shims.
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
) else (
    for %%I in (python.exe) do set "PYTHON_EXE=%%~$PATH:I"
)

if not defined PYTHON_EXE (
    echo Could not find python.exe
    echo.
    echo Press any key to close this window...
    pause >nul
    exit /b 1
)

REM Run directly with explicit python for verbose logging.
echo   Using: %PYTHON_EXE% -m hermes_cli.main gateway run -v
echo.
"%PYTHON_EXE%" -m hermes_cli.main gateway run -v --replace

REM Save exit code for diagnostics.
set GATEWAY_EXIT_CODE=%errorlevel%

echo.
echo [2/2] Gateway exited. Exit code: %GATEWAY_EXIT_CODE%
echo.

echo Press any key to close this window...
pause >nul

endlocal
