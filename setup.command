#!/bin/bash
# Agape Aftershot - setup for macOS. Double-click it once.
# The same job as setup.cmd on Windows: a private Python environment in
# ~/.cullkit/venv, the libraries, and a check that it runs.
cd "$(dirname "$0")" || exit 1
echo "Agape Aftershot setup"
echo

VENV="$HOME/.cullkit/venv"
PY="$(command -v python3.12 || command -v python3)"
if [ -z "$PY" ]; then
  echo "No Python found."
  echo "Install it first:  brew install python@3.12   (or from python.org)"
  echo "Then run this again."
  exit 1
fi

echo "Using: $PY"
echo "Creating the environment in $VENV"
"$PY" -m venv "$VENV" || { echo "Could not create the environment."; exit 1; }

echo "Installing libraries. This downloads about 250 MB and takes a few minutes."
"$VENV/bin/python" -m pip install --upgrade pip --quiet
"$VENV/bin/python" -m pip install -r requirements.txt || {
  echo
  echo "Library install failed."
  echo "If the message above mentions a compiler, install Apple's tools and try again:"
  echo "    xcode-select --install"
  exit 1
}

echo
echo "Checking it works..."
PYTHONPATH="$PWD" "$VENV/bin/python" -m cullkit.cli drives --fast || {
  echo "Something is wrong - it did not run."; exit 1; }

chmod +x "Agape Aftershot.command" cull 2>/dev/null
echo
echo "Done. Start it by double-clicking  Agape Aftershot.command"
echo "Set your folders once:"
echo "    ./cull config --archive-root \"/Volumes/Photos/Archive\" --work-root \"/Volumes/Photos/Current\""
