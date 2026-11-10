@echo off
title Agape Aftershot
setlocal
set "PYTHONPATH=%~dp0"
set "PY=%USERPROFILE%\.cullkit\venv\Scripts\python.exe"

rem "background" is the scheduled task starting this at login, with nobody
rem watching. Two things have to change for that: no browser window (you did
rem not ask for one, you just turned the computer on), and never `pause` on an
rem error - a task waiting on a keypress nobody will press waits forever, and
rem the restart-on-failure never fires because it has not technically failed.
set "OPEN="
set "HOLD=pause"
if /i "%~1"=="background" (
  set "OPEN=--no-open"
  set "HOLD=rem"
)

rem Already up? Then this is a second starter. The desktop shortcut, the
rem Startup folder and the scheduled task can all fire within a second of one
rem another at login, and without this the loser dies on "address already in
rem use" and throws a console full of traceback at you for no reason.
netstat -an | findstr ":8765" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo   Agape Aftershot is already running.
  echo.
  exit /b 0
)

if not exist "%PY%" (
  echo.
  echo   Agape Aftershot is not set up on this machine yet.
  echo   Run setup.cmd once, in this same folder, then try again.
  echo.
  %HOLD%
  exit /b 1
)

echo.
echo   Starting Agape Aftershot...
echo   Your browser will open in a moment.
echo.
echo   Keep this window open while you use it.
echo   Close it (or press Ctrl+C) when you are done.
echo.
"%PY%" -m cullkit.cli app --port 8765 %OPEN%

if errorlevel 1 (
  echo.
  echo   Something went wrong. The message above says what.
  %HOLD%
  rem Pass the failure up. Task Scheduler only restarts a task it believes
  rem failed, and without this cmd returns 0 - the server is dead and Windows
  rem is satisfied. The repeating trigger is the real safety net, but this
  rem makes the faster one work too.
  exit /b 1
)
