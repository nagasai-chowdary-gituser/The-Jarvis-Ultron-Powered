@echo off
REM ============================================================
REM  JARVIS — type `jarvis` in any command prompt to start.
REM
REM  Three processes, because they fail independently:
REM    main.py    the doors (phone, Telegram, desk) + the HUD socket
REM    worker.py  the process that waits, so jobs survive a closed window
REM    hud_gl.py  the screen. A display client - closing it stops nothing.
REM
REM  Usage:
REM    jarvis              all three, typed desk input in this window
REM    jarvis --voice      also open the desk microphone
REM    jarvis --windowed   HUD in a window rather than fullscreen
REM    jarvis --headless   no HUD at all
REM ============================================================
setlocal

set "JARVIS_HOME=%~dp0"
set "JARVIS_PY=%JARVIS_HOME%.venv\Scripts\python.exe"

if not exist "%JARVIS_PY%" (
    echo.
    echo   The Jarvis environment is missing.
    echo   Expected: %JARVIS_PY%
    echo.
    echo   Create it with:
    echo     py -3.11 -m venv "%JARVIS_HOME%.venv"
    echo     "%JARVIS_PY%" -m pip install -r "%JARVIS_HOME%requirements.txt"
    echo.
    exit /b 1
)

if not exist "%JARVIS_HOME%.env" (
    echo.
    echo   No .env file found in %JARVIS_HOME%
    echo   Copy .env.example to .env and put your OpenAI key in it.
    echo.
    exit /b 1
)

REM Run from the install directory so relative paths resolve, whatever
REM directory the user happened to be in when they typed `jarvis`.
pushd "%JARVIS_HOME%"

REM The worker first: it is what keeps running when everything else is shut.
start "jarvis worker" /min "%JARVIS_PY%" -u worker.py

REM The screen, unless asked otherwise. It reconnects on its own, so it does
REM not matter that main.py is not listening yet.
echo %* | find /i "--headless" >nul
if errorlevel 1 (
    echo %* | find /i "--windowed" >nul
    if errorlevel 1 (
        start "jarvis hud" "%JARVIS_PY%" -u hud_gl.py
    ) else (
        start "jarvis hud" "%JARVIS_PY%" -u hud_gl.py --windowed
    )
)

REM main.py stays in THIS window, because the typed desk door reads from here.
"%JARVIS_PY%" -u main.py %*
set "RC=%ERRORLEVEL%"
popd

endlocal & exit /b %RC%
