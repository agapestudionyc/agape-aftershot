"""What the program did, written down as it happens.

Almost every fault fixed in this app was found by looking at a screen. That
works right up until the moment it matters: a job that ran overnight, a
notification that did not arrive, a task that behaved differently on the phone
than on the laptop. By the time anybody looks, the screen has moved on and the
only record of what happened is a memory of what it looked like.

So the program keeps its own account. One line per event, appended as it
happens, on the machine that did the work:

    {"t": "2026-08-23 00:11:02", "kind": "job.finish", "what": "Sifting",
     "ok": true, "rc": 0, "secs": 72.4, "argv": ["quality", "..."], ...}

Three rules, and they are the whole design:

**It never breaks anything.** Every entry point swallows its own failures. A
full disk, a locked file, a value that will not serialise - none of them may
take down a job that is moving photographs. A missing line in the journal is a
gap in the record; a raised exception here would be a lost afternoon.

**It never forgets on its own.** When a file grows past the cap it is rolled
aside, not truncated - `journal-1.jsonl`, `journal-2.jsonl`, and so on. The
only thing that removes an entry is somebody asking for it to be removed.

**It is readable by a person.** JSON Lines: greppable from a terminal, one
event per line, no parser needed to find the word "error".

Read it with `cullkit journal`, or from the app's Overview. Clear it from the
same confirmation box that clears the caches, where it is listed by name, by
count and by size, next to everything else that is about to go.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

HOME = Path.home() / ".cullkit"
NAME = "journal.jsonl"

# A rolled file, not a truncated one. 4 MB is roughly 20,000 events - months of
# ordinary use - and rolling keeps any single read from having to hold a huge
# file in memory.
MAX_BYTES = 4 * 1024 * 1024

_lock = threading.Lock()

# Recording is on unless the environment says otherwise. The switch exists for
# the test suite, which must never write into the real journal of a machine
# with real photographs on it.
#
# Read on every write rather than once at import. Cached at import it looked
# correct and was not: `unittest discover` loads test modules without importing
# the package's `__init__`, so the line that sets this never ran and a full
# suite filed sixteen fixtures' worth of jobs in the real record. A switch that
# only works depending on how the tests were invoked is not a switch.
_off = False


def _recording() -> bool:
    return not _off and os.environ.get("CULLKIT_NO_JOURNAL") != "1"


def path() -> Path:
    return HOME / NAME


def _files() -> list:
    """Every journal file, oldest first.

    `journal-2` is older than `journal-1`, which is older than the live file:
    rolling renames the current one to `-1` and pushes the rest down, so a
    higher number means further back in time.
    """
    try:
        rolled = sorted(
            (p for p in HOME.glob("journal-*.jsonl")),
            key=lambda p: int(p.stem.split("-")[1]) if p.stem.split("-")[1].isdigit()
            else 0,
            reverse=True,
        )
    except (OSError, ValueError):
        rolled = []
    live = path()
    return rolled + ([live] if live.exists() else [])


def _roll() -> None:
    """Push the current file down a number and start a fresh one.

    Called with `_lock` already held. Nothing is deleted: the oldest file keeps
    the highest number and stays there until somebody clears the journal.
    """
    try:
        existing = []
        for p in HOME.glob("journal-*.jsonl"):
            part = p.stem.split("-")[1]
            if part.isdigit():
                existing.append(int(part))
        for n in sorted(existing, reverse=True):
            (HOME / f"journal-{n}.jsonl").rename(HOME / f"journal-{n + 1}.jsonl")
        path().rename(HOME / "journal-1.jsonl")
    except OSError:
        # Could not roll - a file open in another program, most likely. Leave
        # the live file alone and keep appending to it. An oversized journal is
        # a nuisance; a lost one is the thing this module exists to prevent.
        pass


def write(kind: str, **fields) -> None:
    """Record one event. Never raises, whatever happens.

    `kind` is a dotted name - `job.start`, `push.fail`, `ui.error` - so that a
    whole family can be picked out with a prefix and grep still works on the
    raw file.
    """
    if not _recording():
        return
    try:
        now = time.time()
        row = {"t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
               "ts": round(now, 3), "kind": str(kind)}
        for k, v in fields.items():
            if v is None:
                continue
            # Anything that will not serialise is described rather than
            # dropped: a truncated fact beats a missing line.
            try:
                json.dumps(v)
                row[k] = v
            except (TypeError, ValueError):
                row[k] = repr(v)[:500]
        line = json.dumps(row, ensure_ascii=False)
        with _lock:
            HOME.mkdir(parents=True, exist_ok=True)
            p = path()
            try:
                if p.exists() and p.stat().st_size >= MAX_BYTES:
                    _roll()
            except OSError:
                pass
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        # Deliberately total. This module is a witness, never a participant.
        pass


def read(limit: int = 200, kind: str = "", contains: str = "",
         since: float = 0.0) -> list:
    """The most recent entries, newest last, oldest file first.

    `kind` matches on prefix, so `job` finds `job.start` and `job.finish` both.
    Unparseable lines are handed back as `journal.unreadable` rather than
    skipped - a corrupted line is itself worth knowing about.
    """
    out = []
    try:
        for f in _files():
            try:
                with f.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            row = {"t": "", "ts": 0, "kind": "journal.unreadable",
                                   "raw": line[:400]}
                        if since and row.get("ts", 0) < since:
                            continue
                        if kind and not str(row.get("kind", "")).startswith(kind):
                            continue
                        if contains and contains.lower() not in line.lower():
                            continue
                        out.append(row)
            except OSError:
                continue
    except Exception:
        return out[-limit:] if limit else out
    return out[-limit:] if limit else out


def summary() -> dict:
    """How much there is, and where it lives.

    Used by the deletion confirmation, which may not ask you to agree to
    something it has not shown you. Counting the lines is honest work on a file
    this size and beats guessing from the byte count.
    """
    files, events, size = [], 0, 0
    for f in _files():
        try:
            n = 0
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for _ in fh:
                    n += 1
            size += f.stat().st_size
            events += n
            files.append({"path": str(f), "name": f.name, "events": n,
                          "bytes": f.stat().st_size})
        except OSError:
            continue
    # The *first* entry, which is what "the record starts" means. `read` hands
    # back the most recent, so asking it for one row gave the newest and
    # labelled it the oldest - a summary that was confidently wrong.
    first = ""
    for f in _files():
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        first = json.loads(line).get("t", "")
                    except ValueError:
                        continue
                    break
        except OSError:
            continue
        if first:
            break
    return {"dir": str(HOME), "files": files, "events": events,
            "bytes": size, "oldest": first}


def clear() -> dict:
    """Throw the whole record away, on purpose.

    The one thing here that removes anything. It reports exactly what it
    removed, because a cleanup that says "done" and nothing else is how you end
    up unsure whether it ran.
    """
    removed, freed = [], 0
    for f in _files():
        try:
            freed += f.stat().st_size
            f.unlink()
            removed.append(f.name)
        except OSError:
            continue
    # The first entry in the new journal is the fact that the old one was
    # thrown away, so a gap in the record is never silent.
    write("journal.cleared", files=removed, bytes=freed)
    return {"files": removed, "bytes": freed}
