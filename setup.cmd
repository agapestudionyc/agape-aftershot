@echo off
setlocal
echo cullkit setup
echo.

set "VENV=%USERPROFILE%\.cullkit\venv"
set "PY="

for /f "delims=" %%P in ('where py 2^>nul') do set "PY=py -3"
if not defined PY (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  )
)
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do set "PY=%%P"
)

if not defined PY (
  echo No Python found.
  echo Install it first:  winget install --id Python.Python.3.12 --scope user
  echo Then run this again.
  exit /b 1
)

echo Using: %PY%
echo Creating virtual environment in %VENV%
%PY% -m venv "%VENV%"
if errorlevel 1 (
  echo Could not create the virtual environment.
  exit /b 1
)

echo Installing libraries. This downloads about 250 MB and takes a few minutes.
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo Library install failed.
  exit /b 1
)

echo.
echo Checking it works...
set "PYTHONPATH=%~dp0"
"%VENV%\Scripts\python.exe" -m cullkit.cli drives --fast
if errorlevel 1 (
  echo Something is wrong - cullkit did not run.
  exit /b 1
)

echo.
echo Done. Run it with:  "%~dp0cull.cmd"
echo Face models download by themselves the first time you use 'people' or 'quality'.
echo.
echo Set your folders once:
echo    "%~dp0cull.cmd" config --archive-root "D:\Photos\Archive" --work-root "D:\Photos\Current"
