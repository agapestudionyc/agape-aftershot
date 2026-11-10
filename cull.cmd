@echo off
setlocal
set "PYTHONPATH=%~dp0"
"%USERPROFILE%\.cullkit\venv\Scripts\python.exe" -m cullkit.cli %*
