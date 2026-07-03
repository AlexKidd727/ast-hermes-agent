@echo off
setlocal

REM Run from the project directory next to this BAT.
cd /d "%~dp0"

REM Prefer UTF-8 output in Windows console.
chcp 65001 >nul
set "PYTHONUTF8=1"

REM Hermes CLI launcher.
REM Examples:
REM   hermes_cli.bat setup
REM   hermes_cli.bat gateway setup
REM   hermes_cli.bat gateway run --verbose

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
    exit /b 1
)

"%PYTHON_EXE%" -m hermes_cli.main %*

endlocal
