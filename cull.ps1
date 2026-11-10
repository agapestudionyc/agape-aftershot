$ErrorActionPreference = "Stop"
$env:PYTHONPATH = $PSScriptRoot
& "$env:USERPROFILE\.cullkit\venv\Scripts\python.exe" -m cullkit.cli @args
