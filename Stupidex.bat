@echo off
REM Stupidex launcher - starts the .exe with no console window.
REM Double-click to start; closes immediately. Server runs hidden in background.
setlocal
set "EXE=%~dp0Stupidex.exe"
if not exist "%EXE%" (
    echo Stupidex.exe not found at: %EXE%
    pause
    exit /b 1
)
start "" /B "%EXE%"
endlocal
