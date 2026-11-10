"""Test-suite setup that has to happen before anything imports the app.

The suite runs `server.start_job` for real, and the app now writes a line to
its activity journal every time a job starts, finishes or fails. That journal
lives in the user's own `~/.cullkit`, next to the record of a machine with real
photographs on it — so without this the tests would file their fixtures'
comings and goings as if they were his afternoon's work, and an audit of a real
fault would be reading test debris.

Set before any import of `cullkit`, because the switch is read once when the
journal module is first loaded.
"""
import os

os.environ.setdefault("CULLKIT_NO_JOURNAL", "1")
