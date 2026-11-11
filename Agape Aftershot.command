#!/bin/bash
# Agape Aftershot - start the app on macOS. Double-click it.
# Keep the window open while you use it; close it (or Ctrl-C) when done.
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/.cullkit/venv/bin/python"

if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Agape Aftershot is already running."
  open "http://localhost:8765"
  exit 0
fi

if [ ! -x "$PY" ]; then
  echo "Agape Aftershot is not set up on this Mac yet."
  echo "Double-click setup.command once, in this same folder, then try again."
  read -r -p "Press Return to close."
  exit 1
fi

echo "Starting Agape Aftershot. Your browser will open in a moment."
cd "$DIR" || exit 1
PYTHONPATH="$DIR" "$PY" -m cullkit.cli app --port 8765
