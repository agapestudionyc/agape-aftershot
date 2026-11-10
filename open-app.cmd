@echo off
rem Open Agape Aftershot: start the server first if it is not already up,
rem then open the app window. This is what the desktop shortcut runs, so one
rem click always works - even when the server was stopped or never started.
setlocal

netstat -an | findstr ":8765" | findstr "LISTENING" >nul
if not errorlevel 1 goto open

start "" /min "%~dp0Agape Aftershot.cmd"
for /l %%i in (1,1,20) do (
  timeout /t 1 /nobreak >nul
  netstat -an | findstr ":8765" | findstr "LISTENING" >nul
  if not errorlevel 1 goto open
)

:open
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --app=http://localhost:8765 --start-fullscreen
