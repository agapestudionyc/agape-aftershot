"""Where a long job listens for "stop".

A job learns it has been asked to stop the next time it prints - the check
lives in the server's `_Tee.write`. That was fine for stages that print every
file, and useless for the ones that do not: grouping by who is in each group
prints once per group, and one group is fourteen previews and fourteen face
passes. A stop pressed at the start of one waited for the end of it, and a
Separate Projects stopped at 02:25 actually ended at 02:37. Every reload in
between found it still running and took you straight back into it.

`checkpoint()` is a print of nothing. It costs nothing outside the app, and
inside one it reaches the same check every other line does - so a loop that
calls it once per file stops between files, which is the promise the app makes
("the stop lands between files, never inside one") kept for every stage.
"""
from __future__ import annotations

import sys


def checkpoint():
    """Stop here if the job running this has been asked to stop."""
    sys.stdout.write("")
