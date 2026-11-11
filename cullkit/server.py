"""A local web interface for Agape Aftershot.

Runs on the machine that has the photos. The browser talks to it over
localhost, or over the local network from a phone on the same wifi. Long jobs
run in a background thread and the page polls their log, so a 15 GB import does
not hold the connection open.

Standard library only - no web framework - so nothing new to install.
"""
from __future__ import annotations

import ctypes
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import traceback
import urllib.parse

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import journal

WEB = Path(__file__).parent / "web"

_jobs: dict = {}
_lock = threading.Lock()


# A bare "412 / 654" on its own line, and the rule of dashes that a command
# prints under the name of a stage it is starting. Between them they are the
# whole of what a job says about how far along it is.
PROG_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
RULE_RE = re.compile(r"^-{6,}$")

# A label, and then a colon that really does end it - one with a space after
# it, or the end of the line. That is what tells a label from a drive letter:
# cutting at the first colon of any kind turned "reading C:\Users\..." into a
# stage called "reading C", and that is the name the phone was given.
LABEL_RE = re.compile(r"^(.*?):(?:\s|$)")


def _phase_name(line):
    """The name of a stage, taken from the line above its rule of dashes.

    "verdict: 261 keep, 6 flagged" is a stage called verdict, and
    "previews: 47 frames -> C:\\...\\_cullkit" is a stage called previews. A
    line with no label keeps all of itself, trimmed to something that fits on
    a notification rather than running off the end of it.
    """
    line = (line or "").strip()
    m = LABEL_RE.match(line)
    name = (m.group(1) if m else line).strip()
    return name if len(name) <= 70 else name[:69].rstrip() + "…"


class Job:
    """A command running in a thread, and how far along it says it is.

    The progress used to be worked out in the browser, by reading the log as it
    scrolled past. That is fine while somebody is looking, and useless the
    moment they are not: close the page and nothing on this machine knows
    whether the job is at 3% or nearly done. Since the whole point is to be
    told about a job you are *not* watching, the counting had to move here.
    """

    def __init__(self, jid, argv):
        self.id = jid
        self.argv = argv
        self.lines: list = []
        self.done = False
        self.rc = None
        self.started = time.time()
        self.cancelled = False   # you pressed OK on "leave and stop it"
        self.cancelled_at = 0.0  # when, so every screen can say for how long
        self.abandoned = False   # stuck past the stop and let go (see _abandon)
        self.forced = False      # you pressed Force stop (see force)
        self._ident = 0          # the job's thread, so a forced stop can reach it
        self._ident_lock = threading.Lock()
        self.result = ""         # what came of it, in one sentence, once it ends
        self.device = ""         # which browser started it, so coming back to
                                 # your own work is not mistaken for a handover
        self.phase = ""          # the stage it is in, as the command named it
        self.cur = 0
        self.of = 0              # 0 while a stage has no count to give
        self.phase_at = self.started
        self._prev = ""
        self.on_phase = None     # set by start_job, so a change can be told
        self.on_progress = None

    def note(self, line):
        """Read one line of output for what it says about progress."""
        line = line.strip()
        m = PROG_RE.match(line)
        if m:
            cur, of = int(m.group(1)), int(m.group(2))
            # A count that runs past its own total, or a total that changes
            # under us, means two stages have been mixed up. Trust neither.
            if of and cur <= of:
                self.cur, self.of = cur, of
                if self.on_progress:
                    self.on_progress(self)
        elif RULE_RE.match(line) and self._prev:
            phase = _phase_name(self._prev)
            if phase and phase != self.phase:
                self.phase = phase
                self.cur = self.of = 0
                self.phase_at = time.time()
                if self.on_phase:
                    self.on_phase(self)
        self._prev = line

    def cancel(self):
        if not self.cancelled:
            self.cancelled = True
            self.cancelled_at = time.time()
            changed()
            t = threading.Timer(ABANDON_AFTER, self._abandon)
            t.daemon = True
            t.start()

    def force(self):
        """Stop now - don't wait for the end of the file it is on.

        The ordinary stop is polite: it is heard at the next print, between
        files. A stage stuck in one long step never prints, and pressing Stop
        then meant two minutes behind the curtain before the app let it go.
        This is the button for when you will not wait.

        It throws the stop straight into the job's own thread, which lands at
        its next line of Python rather than its next print. Nothing it can
        interrupt is left looking finished: every copy and every preview is
        written to a temporary name and only renamed once whole, so the worst
        a forced stop leaves is a `.part` file the next run writes over. If
        the thread is inside something that never comes back to Python at
        all, the app lets it go after FORCE_GRACE seconds instead of two
        minutes.
        """
        if self.done:
            return
        self.cancel()
        self.forced = True
        journal.write("job.force", id=self.id, what=_job_label(self.argv),
                      phase=self.phase, cur=self.cur, of=self.of)
        with self._ident_lock:
            if self._ident:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(self._ident), ctypes.py_object(Cancelled))
        t = threading.Timer(FORCE_GRACE, self._abandon)
        t.daemon = True
        t.start()

    def _disarm(self):
        """The job is ending on its own; a forced stop must not land in its tidy-up."""
        with self._ident_lock:
            if self._ident:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(self._ident), None)
            self._ident = 0

    def _abandon(self):
        """Let go of a job that will not answer the stop.

        Every stage listens between files now, so a stop lands in seconds. One
        that has not landed after two minutes is not finishing a file - it is
        stuck inside something that never returns, and it will not hear the
        stop however long it is given. On 25 October one sat at "stopping" for
        an hour and a half with the whole app behind the curtain.

        So the app stops waiting for it: it is counted as ended, every screen
        is told, and a new task can start. Its thread cannot be killed from
        here and is left where it is; if it ever does wake up, it ends at its
        next line the way any stopped job does. A restart of the program
        clears it for good.
        """
        if self.done:
            return
        self.abandoned = True
        self.done = True
        # Out of the router's list of live logs, or the next job's pool threads
        # would see two and print to the console instead of its own log.
        tee = getattr(self, "_tee", None)
        if _router is not None and tee in _router.live:
            try:
                _router.live.remove(tee)
            except ValueError:
                pass
        self.rc = 130
        what = _job_label(self.argv)
        where = f" at {self.phase}" if self.phase else ""
        self.result = ((f"was force-stopped{where}. Nothing on the card was "
                        "touched; restart the program to clear it completely")
                       if self.forced else
                       (f"did not answer the stop{where} and was let go. Nothing "
                        "was moved; restart the program to clear it completely"))
        self.lines.append("")
        self.lines.append("  It did not answer the stop and was let go.")
        journal.write("job.abandoned", id=self.id, what=what, argv=self.argv,
                      phase=self.phase, cur=self.cur, of=self.of,
                      waited=round(time.time() - self.cancelled_at, 1))
        with _state_lock:
            LAST_DONE.update({"what": what, "argv": self.argv, "rc": 130,
                              "ok": False, "stopped": True,
                              "took": _tidy(time.time() - self.started),
                              "secs": round(time.time() - self.started, 1),
                              "result": self.result, "at": time.time(),
                              "id": self.id, "device": self.device})
        changed()

    def pct(self):
        return round(self.cur / self.of * 100) if self.of else None

    def progress(self):
        # `stopping` is the state between asking and landing. Without it a page
        # that loaded in that gap saw an ordinary running job and took you
        # straight back into the task you had just stopped.
        return {"phase": self.phase, "cur": self.cur, "of": self.of,
                "pct": self.pct(), "stopping": self.cancelled,
                "stopping_for": (round(time.time() - self.cancelled_at, 1)
                                 if self.cancelled else 0)}

    def as_json(self, since=0):
        return {
            "id": self.id, "done": self.done, "rc": self.rc,
            "result": self.result,
            "argv": self.argv, "lines": self.lines[since:],
            "total": len(self.lines),
            "elapsed": round(time.time() - self.started, 1),
            "progress": self.progress(),
        }


ABANDON_AFTER = 120.0    # seconds a stop may take before the job is let go
FORCE_GRACE = 3.0        # ...and a forced one


class Cancelled(BaseException):
    """Raised inside a job's own thread to stop it where it stands.

    Deliberately a BaseException, for the same reason KeyboardInterrupt is: the
    codebase is full of `except Exception` around file operations, and a stop
    that gets quietly swallowed by one of them is worse than no stop at all.
    """


class _Tee(io.TextIOBase):
    """Collects printed output into the job, line by line.

    It is also where a job finds out it has been asked to stop. That sounds
    odd for an output stream, and it is the right place anyway: every long
    operation in this program reports progress as it goes, and it reports it
    *between* files - after one is copied and verified, before the next is
    started. So the print is already a safe point, and using it means no loop
    anywhere had to learn about cancelling.
    """

    def __init__(self, job):
        self.job = job
        self.buf = ""

    def write(self, s):
        if self.job.cancelled:
            raise Cancelled()
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.job.lines.append(line)
            self.job.note(line)
        return len(s)

    def flush(self):
        # Never raises: this runs in the `finally` that tidies up *after* a
        # stop, and a second Cancelled thrown there would lose the log.
        if self.buf:
            self.job.lines.append(self.buf)
            self.job.note(self.buf)
            self.buf = ""


class _Router(io.TextIOBase):
    """Send each thread's printed output to that thread's own job.

    redirect_stdout swaps a global, so two jobs running at once clobber each
    other's redirection: their logs interleave and lines tear mid-write, which
    produced a nonsense "175/757" progress line from two unrelated jobs.
    Routing per thread keeps every job's log its own.
    """

    def __init__(self, fallback):
        self.fallback = fallback
        self.local = threading.local()
        # Every tee currently collecting for a job. Almost always one, because
        # one job at a time is a rule of this program and not an accident.
        self.live = []

    def _target(self):
        """This thread's tee - or, failing that, the only job in the house.

        The thread-local alone was not enough, and what it missed was the
        longest part of the work. Extracting previews runs on a pool of eight
        threads; none of them is the job's thread, so every line they printed
        fell through to the console the operator cannot see. Two consequences,
        both of them things that were actually reported:

        - the progress bar showed nothing at all through the preview pass,
          which on a full card is most of the wait. A job you cannot see
          moving is indistinguishable from a job that has died.
        - stopping did not stop. The cancel flag is read in `_Tee.write`, so a
          job whose every print was going somewhere else never learned it had
          been asked to stop until the pool finished on its own.

        Falling back to the single live tee fixes both, and keeps the reason
        the thread-local exists in the first place: if two jobs are ever
        collecting at once, an unbound thread has no honest owner, so its
        output goes to the console rather than into whichever log it would have
        torn. That was the original fault - "175/757" made of two jobs - and it
        stays fixed.
        """
        mine = getattr(self.local, "tee", None)
        if mine is not None:
            return mine
        # A thread answering a web request is never part of a job, whatever
        # is running. Without this, the traceback a phone's dropped connection
        # leaves behind was poured into the one live job's log - and its rule
        # of dashes made "ConnectionResetError" the step the job was on, which
        # is what every screen then showed for hours.
        if getattr(_web_thread, "on", False):
            return self.fallback
        live = self.live
        return live[0] if len(live) == 1 else self.fallback

    def bind(self, tee):
        self.local.tee = tee
        self.live.append(tee)

    def unbind(self):
        tee = getattr(self.local, "tee", None)
        self.local.tee = None
        if tee is not None:
            try:
                self.live.remove(tee)
            except ValueError:
                pass

    def write(self, s):
        return self._target().write(s)

    def flush(self):
        self._target().flush()


_router = None
_web_thread = threading.local()      # .on is set in every request thread


def _install_router():
    global _router
    import sys

    if _router is None:
        _router = _Router(sys.__stdout__)
        sys.stdout = _router
        sys.stderr = _router
    return _router


"""What the phone is told, and how often.

The hard part of this is not sending a notification, it is not sending five.
A job prints thousands of lines and crosses a percentage every few seconds; a
knock on each one would be unusable within a minute, and the honest end of that
is somebody turning notifications off and never hearing about the job that
actually mattered.

So three rules, and they are the whole policy:

- **A stage starting is worth saying.** It is the thing you asked for - one
  step done, on to the next - and stages are minutes apart, not seconds.
- **Progress within a stage is worth saying occasionally.** Every two minutes
  at most, and never for a stage that is nearly over anyway.
- **Finishing is always worth saying**, and is the only one that overrides the
  quiet period, because it is the one you are actually waiting for.

Every notification lands on the same tag, so the phone shows *one* line that
keeps changing rather than a stack that has to be swiped away.
"""
def _tidy(secs):
    """A duration a person would say out loud."""
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {secs % 3600 // 60}m"


PROGRESS_EVERY = 120.0     # seconds between "still going" knocks
_last_push = {"at": 0.0}
_state_lock = threading.Lock()
# What the service worker reads when it is woken. It is written here, on this
# machine, and never travels through the push service.
LATEST = {"title": "Agape Aftershot", "body": "Nothing running.", "at": 0.0,
          "done": True, "pct": None}
# The last job to end, whichever device started it and whoever was watching.
# Read by every screen, so "did that import actually work" has an answer on
# the machine you happen to be holding.
LAST_DONE = {}
# When this process started answering. The one number that says "the thing on
# the computer is alive" without having to be watching a job.
STARTED = time.time()


def _say(title, body, done=False, pct=None, force=False):
    """Record what to say, then knock - if the quiet period allows it."""
    now = time.time()
    with _state_lock:
        LATEST.update({"title": title, "body": body, "at": now,
                       "done": done, "pct": pct})
        if not force and now - _last_push["at"] < PROGRESS_EVERY:
            # Held back on purpose, not lost. Worth recording: "it went quiet
            # for four minutes" and "it was deliberately quiet for four
            # minutes" look identical from the phone.
            journal.write("push.held", body=body, pct=pct,
                          since_last=round(now - _last_push["at"], 1))
            return False
        _last_push["at"] = now

    def go():
        try:
            from . import push
            res = push.wake_all()
            # Until now this was thrown away, which made "the notification
            # stopped arriving" impossible to answer: the app could not tell a
            # phone that never got knocked on from one that got knocked on and
            # ignored it. Both are written down now, with whatever the push
            # service said back.
            journal.write("push.sent" if res.get("sent") else "push.nobody",
                          body=body, done=done, pct=pct,
                          sent=res.get("sent"), failed=res.get("failed"),
                          why=res.get("why"), notes=res.get("notes"))
        except Exception as exc:
            journal.write("push.error", body=body,
                          error=f"{type(exc).__name__}: {exc}")
            # ...and a job must still never die because a phone was unreachable.

    threading.Thread(target=_ordered(go), daemon=True).start()
    return True


# One knock at a time, in the order they were asked for.
#
# Every knock is the same empty push, and the phone answers it by asking this
# machine what to say *now*. So two knocks in flight are not two messages -
# they are one message sent twice, and their answers race. That is how a
# finish ended up on screen underneath a stage that started a second earlier:
# both threads woke the phone, both fetched, and the slower one drew last.
#
# Serialising them costs nothing (a knock takes milliseconds) and makes the
# last thing asked for the last thing shown, which is the only ordering
# anybody can reason about.
_send_lock = threading.Lock()


def _ordered(fn):
    def run():
        with _send_lock:
            fn()
    return run


def _job_label(argv):
    """What the job is, in the words the app itself uses."""
    names = {"quality": "Sifting", "import": "Importing", "shoots": "Separating",
             "work": "Moving your picks", "people": "Grouping by who is in them",
             "preview": "Pulling previews", "commit": "Naming the shoots",
             "poses": "Grouping the near-identical",
             "xmp": "Marking for Lightroom"}
    return names.get(argv[0] if argv else "", "Working") if argv else "Working"


# The commands worth waking a phone for.
#
# Everything else the app runs - reading the settings, listing the drives,
# counting what is cached - is over before the phone could light up, and a
# knock for one of those is not information, it is training: five "Working -
# settings" in one second is how somebody learns to ignore the one knock that
# actually mattered. Whitelisted rather than blacklisted on purpose, so a
# command added later is silent until somebody decides it is worth saying.
WORTH_SAYING = frozenset({"import", "quality", "shoots", "work", "people",
                          "preview", "poses", "commit"})


def _worth_saying(argv):
    return bool(argv) and argv[0] in WORTH_SAYING


# What a job actually did, read out of its own last words.
#
# "Importing finished - took 15m" is not an answer. It says a thing ended,
# which you already knew, and leaves the only question you had - did it
# work, how many, is the card safe to wipe - to be looked up by walking to
# the computer. These are the lines every command already prints at the end;
# all that was missing was reading them.
RESULTS = [
    (re.compile(r"^copied (\d+), already there (\d+), failed (\d+)"),
     lambda m: (f"{m.group(3)} of {int(m.group(1)) + int(m.group(3))} failed to "
                "copy. Do NOT format the card"
                if int(m.group(3))
                else f"{m.group(1)} files copied and checksummed"
                     + (f", {m.group(2)} already there" if int(m.group(2)) else "")
                     + ". The card is safe to reformat")),
    (re.compile(r"^verdict: (\d+) keep, (\d+) flagged"),
     lambda m: f"{m.group(1)} keep, {m.group(2)} flagged for your call"),
    (re.compile(r"^(\d+) shoot\(s\) detected"),
     lambda m: f"{m.group(1)} project(s) found — check and name them"),
    (re.compile(r"^(\d+) runs found in (\d+) frames"),
     lambda m: f"{m.group(2)} frames fall into {m.group(1)} runs"),
    (re.compile(r"^(\d+) files into (\d+) folders"),
     lambda m: f"{m.group(1)} files sorted into {m.group(2)} folders"),
    (re.compile(r"^moved (\d+)"), lambda m: f"{m.group(1)} moved"),
    (re.compile(r"^matched\s+(\d+)"), lambda m: f"{m.group(1)} frames matched"),
    (re.compile(r"^(\d+) previews removed, ([\d.]+ \w+) freed"),
     lambda m: f"{m.group(2)} freed"),
]


def outcome(lines, rc=0, cancelled=False, argv=None):
    """One sentence saying what came of it. Never empty, never a lie."""
    body = [ln.strip() for ln in (lines or []) if ln.strip()]
    if cancelled:
        got = [ln for ln in body if PROG_RE.match(ln)]
        # The sentence this is dropped into already says "stopped", so this
        # says who and where instead of saying it a second time.
        return ("you ended it" + (f" at {got[-1].strip()}" if got else "")
                + ". Nothing was left half-done")
    if rc:
        bad = next((ln for ln in reversed(body)
                    if re.search(r"error|failed|traceback|no such|not found",
                                 ln, re.I)), "")
        return bad[:120] if bad else "it stopped early — open the app to see why"
    for ln in body:
        for pat, say in RESULTS:
            m = pat.match(ln)
            if m:
                return say(m)
    if any("DRY RUN" in ln for ln in body):
        return "a rehearsal — nothing was copied or moved"
    return "finished"


def _watch(job):
    """Wire a job's own progress up to the phone."""
    what = _job_label(job.argv)
    say = _worth_saying(job.argv)

    def on_phase(j):
        # The record is kept for every job, however small. Only the phone is
        # spared the small ones.
        journal.write("job.phase", id=j.id, what=what, phase=j.phase,
                      secs=round(time.time() - j.started, 1))
        if say:
            _say("Agape Aftershot", f"{what} — {j.phase}", force=True)

    def on_progress(j):
        if not say:
            return
        pct = j.pct()
        if pct is None or pct >= 97:
            return        # about to finish; the finish message says it better
        left = j.of - j.cur
        _say("Agape Aftershot",
             f"{j.phase or what} — {pct}%, {left} to go", pct=pct)

    job.on_phase = on_phase
    job.on_progress = on_progress


def start_job(argv, device=""):
    """Start a command, unless one is already going.

    Returns `(job, None)` when it started, `(None, other)` when something else
    holds the floor. Two values rather than an exception because the caller has
    to say *what* is already running, not just that something is.
    """
    from .cli import main

    jid = f"j{int(time.time() * 1000)}-{threading.get_ident() % 1000}"
    what = _job_label(argv)
    job = Job(jid, argv)
    job.device = device
    _watch(job)
    # One job at a time, and the rule lives here rather than in the page so it
    # holds for every device at once - two phones cannot each start one.
    #
    # The check and the insertion share a lock on purpose. Split them and two
    # requests arriving together both look around, both see an empty house, and
    # both move in - which is the exact thing this is meant to prevent, made
    # rarer and therefore harder to ever notice.
    with _lock:
        for other in _jobs.values():
            if not other.done:
                journal.write("job.refused", argv=argv, device=device,
                              what=what, blocked_by=other.id,
                              blocked_by_what=_job_label(other.argv))
                return None, other
        _jobs[jid] = job
    journal.write("job.start", id=jid, what=what, argv=argv, device=device)
    changed()
    router = _install_router()

    def run():
        tee = _Tee(job)
        job._tee = tee
        job._ident = threading.get_ident()
        router.bind(tee)
        try:
            try:
                job.rc = main(argv)
            finally:
                job._disarm()
        except Cancelled:
            job.lines.append("")
            if job.forced:
                job.lines.append("  Force-stopped by you. A file that was halfway through")
                job.lines.append("  was left under a temporary name and is done again next run.")
            else:
                job.lines.append("  Stopped by you. Nothing was left half-done - the")
                job.lines.append("  stop lands between files, never inside one.")
            job.rc = 130            # the shell's own "ended by a signal"
        except SystemExit as e:
            job.rc = e.code if isinstance(e.code, int) else 1
        except Exception as exc:
            trace = traceback.format_exc()
            for ln in trace.splitlines():
                job.lines.append(ln)
            job.rc = 1
            # The whole traceback, not the one-line summary. This is the entry
            # somebody reads a week later trying to work out what happened,
            # and by then the log panel it also went to is long gone.
            journal.write("job.error", id=jid, what=what, argv=argv,
                          device=device, error=f"{type(exc).__name__}: {exc}",
                          traceback=trace[-4000:])
        finally:
            tee.flush()
            router.unbind()
            job.done = True
            changed()
            # The one everybody is actually waiting for, so it ignores the
            # quiet period. Said plainly: what ran, and whether it worked.
            secs = round(time.time() - job.started, 1)
            took = _tidy(time.time() - job.started)
            # Whatever the ending was, it is written down the same way. The
            # last few lines come too: on a failure they are the reason, and
            # on a success they are the result - "44 keep, 0 flagged" is the
            # answer somebody will want months later.
            job.result = outcome(job.lines, job.rc, job.cancelled, argv)
            journal.write("job.finish", id=jid, what=what, argv=argv,
                          device=device, rc=job.rc, secs=secs,
                          ok=(job.rc == 0), stopped=bool(job.cancelled),
                          phase=job.phase or "", result=job.result,
                          tail=[ln for ln in job.lines[-12:] if ln.strip()])
            # The last thing that happened, kept where every device can read it
            # - including one that was not in the room when it happened. Coming
            # back to the app on the other machine should not mean the answer
            # is only in a log panel in a browser somewhere else.
            with _state_lock:
                LAST_DONE.update({"what": what, "argv": argv, "rc": job.rc,
                                  "ok": job.rc == 0, "took": took, "secs": secs,
                                  "stopped": bool(job.cancelled),
                                  "result": job.result, "at": time.time(),
                                  "id": jid, "device": device})
            if _worth_saying(argv):
                # Ending a task, redoing it, and leaving the screen all arrive
                # here. Naming one of them would be wrong two times out of three.
                #
                # And whatever the ending was, it says what came of it. "Took
                # 15m" answers nothing; "412 files copied and checksummed - the
                # card is safe to reformat" is the whole reason you were waiting.
                if job.cancelled:
                    body = f"{what} stopped — {job.result}"
                elif job.rc:
                    body = f"{what} did not finish — {job.result}"
                else:
                    body = f"{what} finished in {took} — {job.result}"
                _say("Agape Aftershot", body, done=True, force=True)

    threading.Thread(target=run, daemon=True).start()
    return job, None


def _thumb(path, size=420):
    """Shrink a cached preview - or, when nothing would be gained, do not.

    The cached preview is already a JPEG at a sensible size. Re-encoding it to
    serve the big view costs a downscale *and* a second round of compression,
    which is exactly the softness you notice when judging focus. If the caller
    is asking for something at least as large as the file already is, the file
    goes out untouched.
    """
    from PIL import Image

    p = Path(path)
    with Image.open(p) as im:
        w, h = im.size
        if size >= max(w, h):
            return p.read_bytes()
        im = im.convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        # Filmstrip thumbnails can be lean; anything you actually look at cannot.
        big = size >= 700
        im.save(buf, "JPEG", quality=92 if big else 80,
                subsampling=0 if big else 2, optimize=True)
    return buf.getvalue()


LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agape Aftershot</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0d0d;
 color:#eceae5;font:16px/1.6 -apple-system,"Segoe UI",system-ui,sans-serif}
.b{width:min(92vw,360px);text-align:center}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}
p{color:#a9a49b;font-size:14px;margin:0 0 22px}
input{width:100%;padding:15px;font-size:22px;text-align:center;letter-spacing:.4em;
 border-radius:10px;border:1px solid #363634;background:#171717;color:#eceae5}
button{width:100%;margin-top:12px;padding:15px;font-size:15px;font-weight:600;
 border:none;border-radius:10px;background:#3dff87;color:#08150f;cursor:pointer}
.e{color:#ff6b52;font-size:14px;margin-top:12px;min-height:20px}
</style></head><body><div class="b">
<h1>Agape Aftershot</h1><p>Enter the code shown on the computer</p>
<input id="p" inputmode="numeric" maxlength="6" autocomplete="off" autofocus>
<button id="g">Unlock</button><div class="e" id="e"></div>
<script>
const go=async()=>{const r=await fetch('/api/login',{method:'POST',
 headers:{'Content-Type':'application/json'},
 body:JSON.stringify({pin:document.getElementById('p').value.trim()})});
 if(r.ok)location.href='/'+location.search;else{document.getElementById('e').textContent='Not that one.';
 document.getElementById('p').value='';document.getElementById('p').focus();}};
document.getElementById('g').onclick=go;
document.getElementById('p').addEventListener('keydown',e=>{if(e.key==='Enter')go()});
</script></div></body></html>"""


def _num(q, key):
    try:
        v = float(q.get(key))
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _sift(q):
    """Judge a sifted folder from its saved measurements. No photograph is
    opened, so this answers in the time it takes to move a slider."""
    from . import quality as Q
    from . import taste

    folder = str(q.get("folder") or "")
    saved = Q.load_scores(folder) if folder else None
    if saved is None:
        return {"missing": True}
    blur = _num(q, "blur")
    learned = None
    if blur is None:
        learned = taste.learned_blur_ratio()
        blur = learned["ratio"] if learned else taste.DEFAULT_RATIO
    scores, stats = Q.judge(saved["frames"], {
        "blur_ratio": blur, "eye_ratio": _num(q, "eyes"),
        "eye_soft": _num(q, "eye_soft"), "reject_ratio": _num(q, "reject"),
        "blown": _num(q, "blown"), "black": _num(q, "black"),
    }, Q.load_overrides(folder))
    # Frames already moved out by an earlier "Move flagged" are still in the
    # measurements (the medians need them) but are no longer his to decide.
    try:
        here = {Path(e.name).stem for e in os.scandir(folder) if e.is_file()}
    except OSError:
        here = None
    frames = []
    for s in scores:
        f = Q.frame_out(s)
        f["moved"] = bool(here is not None and s.base not in here)
        frames.append(f)
    live = [f for f in frames if not f["moved"]]
    count = {t: sum(1 for f in live if f["tier"] == t)
             for t in ("keep", "review", "reject", "error")}
    reasons = {}
    for f in live:
        if f["tier"] in ("review", "reject"):
            for r in f["reasons"]:
                reasons[r] = reasons.get(r, 0) + 1
    return {
        "folder": folder, "analysed": saved.get("analysed"),
        "settings": stats["settings"], "learned": bool(learned),
        "defaults": dict(Q.SETTINGS, blur_ratio=blur if learned else Q.SETTINGS["blur_ratio"]),
        "median_sharpness": stats.get("median_sharpness"),
        "count": count, "reasons": reasons, "frames": frames,
        "groups": len({f["group"] for f in live if f["group"] is not None}),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AgapeAftershot"
    # HTTP/1.1, because Chrome's service-worker loader is stricter than fetch()
    # is: the page could pull /sw.js down by hand quite happily while
    # registering it failed with "an unknown error occurred when fetching the
    # script". Safe to raise only because every response here sets an accurate
    # Content-Length - without that, keep-alive leaves the browser waiting for
    # a body that never ends. The timeout is the other half: a kept-alive
    # connection otherwise pins a thread for as long as the browser feels like.
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, *a):
        pass  # keep the console clean for the operator

    def setup(self):
        _web_thread.on = True
        super().setup()

    # ------------------------------------------------------------------- auth
    # The app can browse drives and move photographs. On your own wifi that is
    # fine; on a cafe or a client's network anyone who scans the place would
    # find it. Requests from this machine pass freely - everything else needs
    # the code printed in the console.
    # The face of the app, as opposed to its contents. The browser fetches
    # these before anyone has had a chance to type the code - when they answer
    # with the lock screen instead, the phone ends up with a blank grey
    # shortcut instead of the logo, which is exactly what happened. A name and
    # an icon give nobody access to anything.
    # `/sw.js` is here for the same reason. The browser re-fetches the worker
    # on its own schedule, including long after a cookie has expired, and
    # answering that with the lock screen leaves a registered worker running
    # an HTML file as if it were JavaScript. It contains no data - it asks
    # this server for everything it shows.
    PUBLIC = frozenset({"/static/manifest.json", "/static/favicon.png",
                        "/static/icon-180.png", "/static/icon-192.png",
                        "/static/icon-512.png", "/sw.js",
                        # The two home-screen shortcuts. Android draws these
                        # on the long-press menu and on the icon you drag out
                        # of it, and fetches them the way it fetches the logo:
                        # with no cookie.
                        "/static/shortcut-aftershot-96.png",
                        "/static/shortcut-aftershot-192.png",
                        "/static/shortcut-aftershot-512.png",
                        "/static/shortcut-darkroom-96.png",
                        "/static/shortcut-darkroom-192.png",
                        "/static/shortcut-darkroom-512.png",
                        "/static/shortcut-aftershot-maskable-192.png",
                        "/static/shortcut-darkroom-maskable-192.png",
                        "/static/shortcut-files-96.png",
                        "/static/shortcut-files-192.png",
                        "/static/shortcut-files-512.png",
                        "/static/shortcut-files-maskable-192.png",
                        "/static/shortcut-aftershot-maskable-512.png",
                        "/static/shortcut-darkroom-maskable-512.png",
                        "/static/shortcut-files-maskable-512.png"})

    def _who(self):
        """The address of the device that is actually asking.

        On the wifi that is simply who opened the socket. Behind
        `tailscale serve` it is not: every request arrives from loopback with
        the real address in `X-Forwarded-For`, so without this the phone would
        look exactly like this PC - no code asked for, and the controls
        quietly taken. The header is only believed when the connection itself
        came from loopback, which nothing on the network can fake.
        """
        host = self.client_address[0]
        if host not in ("127.0.0.1", "::1"):
            return host
        # The LAST entry, not the first. Tailscale today replaces this header
        # outright, so either would do - but the usual proxy behaviour is to
        # append, and then the first entry is whatever the caller sent and the
        # last is the only one the proxy vouched for. Reading the first would
        # let a device on the tunnel claim to be 127.0.0.1 by sending its own
        # header. Nothing here depends on which of the two Tailscale does.
        fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[-1]
        fwd = fwd.strip().strip("[]")
        if fwd:
            return fwd
        return "tailnet" if self._via_tailnet() else host

    def _via_tailnet(self):
        """Did this arrive through the Tailscale HTTPS front door?

        The Host header says so: this PC's own browser asks for localhost, and
        only a device coming in over the tunnel names the `.ts.net` address.
        Used to refuse the free pass loopback normally gets - if the forwarded
        address is ever missing, the answer must be "ask for the code", not
        "let it in".
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host.strip("[]").lower().endswith(".ts.net")

    def _authorised(self, path=None):
        from . import ledger as L

        if path in Handler.PUBLIC:
            return True
        host = self._who()
        if host in ("127.0.0.1", "::1", "localhost") and not self._via_tailnet():
            return True
        pin = str(L.load_config().get("access_pin") or "")
        if not pin:
            return True
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            if part.strip().startswith("aftershot="):
                if hmac.compare_digest(part.strip().split("=", 1)[1], pin):
                    return True
        return hmac.compare_digest(self.headers.get("X-Aftershot-Pin") or "", pin)

    # The names this machine answers to. Kept for a minute because working them
    # out costs a DNS call, and rebuilt on a miss because the wifi - and with
    # it the address - can change while the server is running.
    _hosts = {"at": 0.0, "names": frozenset()}

    @classmethod
    def _my_names(cls, refresh=False):
        now = time.time()
        if refresh or now - cls._hosts["at"] > 60:
            names = {"localhost", "127.0.0.1", "::1"}
            try:
                hn = socket.gethostname().lower()
                names.add(hn)
                names.add(hn + ".local")
            except Exception:
                pass
            names.update(local_addresses())
            # The Tailscale name is one of this machine's names too. Without
            # it every request through the HTTPS front door is refused as a
            # rebinding attempt, which is exactly how it looks from here.
            ts = tailnet_name()
            if ts:
                names.add(ts)
            cls._hosts = {"at": now, "names": frozenset(n.lower() for n in names)}
        return cls._hosts["names"]

    def _host_allowed(self):
        """The Host header must name this machine.

        A hostile web page cannot read answers from this server (no CORS), but
        DNS rebinding gets around that: the page asks for its own domain and
        the domain quietly resolves here. Its requests then arrive carrying the
        attacker's domain in Host - so a Host that is not one of this machine's
        own names is refused outright.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        host = host.strip("[]").lower()
        if not host:
            return False
        if host in Handler._my_names():
            return True
        return host in Handler._my_names(refresh=True)

    def _origin_ok(self):
        """POSTs change things, so a cross-site page must not be able to send
        them. The app's own pages send no Origin or one naming this machine;
        anything else is another website using this browser as a relay."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            o = urllib.parse.urlsplit(origin)
            host = (o.hostname or "").lower()
        except Exception:
            return False
        if not host:
            return False
        if host in Handler._my_names():
            return True
        return host in Handler._my_names(refresh=True)

    def _deny(self, path):
        if path.startswith("/api/"):
            return self._send(401, {"error": "locked"})
        body = LOGIN_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    # ---------------------------------------------------------------- helpers
    def _send(self, code, body, ctype="application/json", cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if cache:
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            # Everything else here is an answer about *now* - what is running,
            # how far along, who is driving. A browser that reuses one of these
            # is not saving a request, it is showing yesterday's weather. Said
            # out loud rather than left to heuristics, which differ per browser
            # and are exactly the kind of thing that only misbehaves on the one
            # device you cannot debug.
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _query(self):
        parsed = urllib.parse.urlparse(self.path)
        return (urllib.parse.unquote(parsed.path),
                dict(urllib.parse.parse_qsl(parsed.query)))

    def _static_file(self, path):
        """Resolve a /static/ URL to a file inside the web folder - only there.

        The URL is attacker-typed text; without the containment check a request
        for /static/../../.cullkit/config.json walks out of the folder and
        reads whatever it likes off the disk.
        """
        f = (WEB / path[len("/static/"):]).resolve()
        try:
            f.relative_to(WEB.resolve())
        except ValueError:
            return None
        return f if f.is_file() else None

    # -------------------------------------------------------------------- GET
    def do_GET(self):
        path, q = self._query()
        if not self._host_allowed():
            return self._send(403, {"error": "wrong host"})
        if not self._authorised(path):
            return self._deny(path)
        # Fetching the icon is the browser being tidy, not a person arriving.
        if path not in Handler.PUBLIC:
            self._note_client()
        try:
            if path in ("/", "/index.html"):
                return self._file(WEB / "index.html", "text/html; charset=utf-8")
            # Served from the root on purpose. A worker's reach is the folder
            # it was served from, so one at /static/sw.js could only ever see
            # /static/ - it would register and then quietly control nothing.
            if path == "/sw.js":
                return self._file(WEB / "sw.js", "text/javascript; charset=utf-8")
            if path.startswith("/static/"):
                f = self._static_file(path)
                if f:
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    return self._file(f, ctype)
                return self._send(404, {"error": "not found"})

            if path == "/api/wait":
                # Held open until something changes, or 25 seconds - well
                # inside the connection timeout and any proxy's idea of idle.
                try:
                    since = int(q.get("v", -1))
                except ValueError:
                    since = -1
                return self._send(200, {"v": wait_for_change(since)})
            if path == "/api/state":
                # A page in the darkroom says so on every poll. Presence, not
                # an announcement made once on the way in - somebody already
                # inside when this process restarted would otherwise stay
                # invisible for as long as they kept working.
                if q.get("place") == "dev":
                    self._note_darkroom(q.get("folder", ""), q.get("device", ""))
                return self._send(200, self._state())
            if path == "/api/browse":
                return self._send(200, self._browse(
                    q.get("path", ""), want_files=q.get("files") == "1"))
            if path.startswith("/api/files/"):
                return self._files_get(path, q)
            if path == "/api/shoots":
                return self._send(200, self._read_json(
                    Path(q["folder"]) / "_cullkit" / "shoots" / "shoots.json"))
            if path == "/api/quality":
                return self._send(200, self._read_json(
                    Path(q["folder"]) / "_cullkit" / "quality.json"))
            if path == "/api/sift":
                return self._send(200, _sift(q))
            if path == "/api/focus":
                return self._send(200, self._focus(
                    q.get("folder", ""), q.get("stem", ""), q.get("path", "")))
            if path == "/api/full":
                data = self._full(q.get("folder", ""), q.get("stem", ""))
                if data is None:
                    return self._send(404, {"error": "no original for this frame"})
                return self._send(200, data, "image/jpeg", cache=True)
            if path == "/api/poses":
                return self._send(200, self._read_json(
                    Path(q["folder"]) / "_cullkit" / "poses" / "poses.json"))
            if path == "/api/frames":
                # Same again: only the darkroom asks what frames a folder has.
                self._note_darkroom(q.get("folder", ""), q.get("device", ""))
                return self._send(200, self._frames(q["folder"]))
            if path == "/api/audio":
                return self._send(200, self._audio())
            if path == "/api/ytm/search":
                from . import ytm
                return self._send(200, ytm.search(q.get("q", ""), q.get("kind", "songs")))
            if path == "/api/ytm/playlist":
                from . import ytm
                return self._send(200, ytm.playlist(q.get("id", "")))
            if path == "/api/vibes":
                from . import vibes as V
                out = []
                for v in V.load():
                    row = dict(v)
                    row["tracks"] = (self._audio().get(v["builtin"], [])
                                     if v.get("builtin") else
                                     [t["name"] for t in V.tracks(v["id"])])
                    row["own"] = V.tracks(v["id"])
                    out.append(row)
                from . import ytm
                ids = [t["id"] for v in out for t in (v.get("yt") or [])]
                # Whatever a vibe holds that is not here yet is fetched now, so
                # opening the app is enough to finish what an Add started.
                ytm.prefetch(ids)
                return self._send(200, {"vibes": out, "max": V.MAX_VIBES,
                                        # Whether YouTube tracks play as audio
                                        # in the app's own player (yt-dlp is
                                        # installed) or in YouTube's embed.
                                        "ytdl": ytm.can_fetch(),
                                        "yt_ready": ytm.status(ids),
                                        "converter": bool(
                                            __import__("shutil").which("ffmpeg"))})
            if path == "/api/yt-audio":
                # One YouTube track as a plain audio file, fetched the first
                # time it is asked for and kept. See the second half of ytm.py.
                from . import ytm
                f, why = ytm.audio(q.get("id", ""))
                if not f:
                    journal.write("yt.audio-failed", id=q.get("id", ""), why=why)
                    return self._send(502, {"error": why})
                return self._file(f, mimetypes.guess_type(str(f))[0] or "audio/mp4")
            if path == "/api/vibe-track":
                # A vibe's own music, which lives beside the program's state
                # rather than inside the web folder. Contained the same way
                # /static/ is: the name is attacker-typed text, so it is
                # reduced to a bare filename and checked to be in the folder
                # it claims to be in.
                from . import vibes as V
                d = V.folder(q.get("id", ""))
                f = (d / Path(str(q.get("file", ""))).name).resolve()
                try:
                    f.relative_to(d.resolve())
                except ValueError:
                    return self._send(404, {"error": "not found"})
                if not f.is_file():
                    return self._send(404, {"error": "not found"})
                ctype = mimetypes.guess_type(str(f))[0] or "audio/mp4"
                return self._file(f, ctype)
            if path == "/api/caches":
                return self._send(200, self._caches())
            if path == "/api/journal":
                # `kind` matches on prefix, so `job` brings back starts and
                # finishes together and `push` brings back the whole story of
                # one notification.
                return self._send(200, {
                    "events": journal.read(
                        limit=min(int(q.get("limit", 200) or 200), 5000),
                        kind=str(q.get("kind", "") or ""),
                        contains=str(q.get("contains", "") or ""),
                        since=float(q.get("since", 0) or 0)),
                    "summary": journal.summary()})
            if path == "/api/progress":
                # What the service worker asks for after an empty knock wakes
                # it. Whatever is running now, plus the sentence to show.
                with _lock:
                    live = [j for j in _jobs.values() if not j.done]
                with _state_lock:
                    said = dict(LATEST)
                said["running"] = [{"id": j.id, "argv": j.argv,
                                    "elapsed": round(time.time() - j.started, 1),
                                    **j.progress()} for j in live]
                return self._send(200, said)
            if path == "/api/push/key":
                from . import push
                return self._send(200, {"key": push.public_key(),
                                        "devices": len(push.subscriptions()),
                                        "available": push.available()})
            if path == "/api/run-state":
                # The run belongs to the job, not to the device holding it, so
                # picking it up on the laptop lands exactly where the tablet was.
                f = Path.home() / ".cullkit" / "run.json"
                return self._send(200, _read_state(f) or {"active": False})
            if path == "/api/selection":
                # Asking for a folder's selection means standing in the
                # darkroom looking at that folder - nothing else in the app
                # asks. Noting it here rather than waiting to be told means it
                # works whatever version of the page is doing the asking, and
                # without anybody having to choose a photograph first.
                self._note_darkroom(q.get("folder", ""), q.get("device", ""))
                return self._send(200, self._read_json(
                    Path(q["folder"]) / "_cullkit" / "selection.json"))
            if path == "/api/place":
                return self._send(200, _last_place())
            if path == "/api/darkroom":
                # Which darkroom is open, if any, and everything needed to walk
                # back into it. The selection lives beside the photographs; this
                # is only the pointer saying which folder to look in.
                d = _open_darkroom()
                if d.get("folder"):
                    sel = self._read_json(
                        Path(d["folder"]) / "_cullkit" / "selection.json")
                    if sel.get("missing"):
                        d = {}
                    else:
                        d["selection"] = sel
                return self._send(200, d)
            if path == "/api/thumb":
                p = Path(q["path"])
                if not p.is_file():
                    return self._send(404, {"error": "no such image"})
                return self._send(200, _thumb(p, int(q.get("size", 420))),
                                  "image/jpeg", cache=True)
            if path == "/api/job":
                with _lock:
                    job = _jobs.get(q.get("id", ""))
                if not job:
                    return self._send(404, {"error": "no such job"})
                return self._send(200, job.as_json(int(q.get("since", 0))))
        except Exception as exc:
            # Until now this went back to whoever asked and nowhere else, so a
            # request that blew up while the app was closed left no trace at
            # all on the machine that failed it.
            journal.write("http.error", path=path, method=self.command,
                          error=f"{type(exc).__name__}: {exc}",
                          traceback=traceback.format_exc()[-4000:])
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        self._send(404, {"error": "not found"})

    # ------------------------------------------------------------------- HEAD
    def do_HEAD(self):
        """The page asks HEAD whether an audio file exists before using it."""
        path, _ = self._query()
        code = 404
        if not self._host_allowed() or not self._authorised(path):
            code = 401
        elif path.startswith("/static/"):
            f = self._static_file(path)
            if f:
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(f.stat().st_size))
                self.end_headers()
                return
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------------- POST
    # Six digits fall to a patient script unless wrong guesses cost something:
    # after five misses an address waits half a minute per try.
    _login_fails = {}

    def do_POST(self):
        path, _ = self._query()
        if not self._host_allowed() or not self._origin_ok():
            return self._send(403, {"error": "forbidden"})
        try:
            # One route does not carry JSON: a music file arrives as its own
            # bytes, with the name in a header. Handled before the read below,
            # which would otherwise try to parse a megabyte of AAC as JSON.
            if path == "/api/vibes/track":
                return self._take_track()
            if path == "/api/files/upload":
                return self._files_upload()
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")

            if path == "/api/login":
                from . import ledger as L
                who = self._who()
                fails, until = Handler._login_fails.get(who, (0, 0.0))
                if fails >= 5 and time.time() < until:
                    return self._send(429, {"error": "too many tries - wait a moment"})
                pin = str(L.load_config().get("access_pin") or "")
                if pin and hmac.compare_digest(str(data.get("pin", "")).strip(), pin):
                    Handler._login_fails.pop(who, None)
                    body = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    # Secure only when the code really did arrive over HTTPS.
                    # Set unconditionally it would be dropped on the plain-HTTP
                    # wifi address and the phone could never log in there at
                    # all; left off over the tunnel, the browser would happily
                    # send the code back out over plain HTTP later.
                    https = self.headers.get("X-Forwarded-Proto") == "https"
                    self.send_header(
                        "Set-Cookie",
                        f"aftershot={pin}; Path=/; Max-Age=2592000; "
                        "SameSite=Lax; HttpOnly" + ("; Secure" if https else ""))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                Handler._login_fails[who] = (fails + 1, time.time() + 30)
                return self._send(401, {"error": "wrong code"})

            if not self._authorised():
                return self._deny(path)
            # Whoever last *did* something owns the session. Recorded here, in
            # one place, rather than by each feature deciding for itself.
            if path in ACTIONS and data.get("device"):
                _note_actor(str(data["device"]), path)
            if path == "/api/push/subscribe":
                from . import push
                sub = data.get("subscription") or {}
                if not sub.get("endpoint"):
                    return self._send(400, {"error": "no endpoint"})
                sub["ua"] = self.headers.get("User-Agent", "")[:120]
                return self._send(200, {"ok": True, "devices": push.add(sub)})
            if path == "/api/push/forget":
                from . import push
                push.drop(data.get("endpoint") or "")
                return self._send(200, {"ok": True,
                                        "devices": len(push.subscriptions())})
            if path == "/api/push/test":
                # Proves the whole path end to end - sign, send, wake, fetch,
                # show - without waiting for a real job to reach a milestone.
                _say("Agape Aftershot",
                     "Notifications are on. This is what one looks like.",
                     done=True, force=True)
                from . import push
                return self._send(200, push.wake_all())
            if path == "/api/sift/override":
                # His own call on one frame. It touches a small file beside
                # the measurements and no photograph, so it is allowed while
                # anything else is running.
                from . import quality as Q
                folder, base = str(data.get("folder") or ""), str(data.get("base") or "")
                if not folder or not base or not Path(folder).is_dir():
                    return self._send(400, {"error": "no such folder or frame"})
                tier = data.get("tier")
                Q.save_override(folder, base, tier if tier in ("keep", "reject") else None)
                return self._send(200, _sift(data))
            if path == "/api/job/stop":
                # Leaving a screen with work on it means stopping the work, not
                # abandoning it to run on unattended. Named ids only: the caller
                # stops what it started, so a job someone launched from another
                # device is not collateral.
                ids = data.get("ids") or ([data["id"]] if data.get("id") else [])
                force = bool(data.get("force"))
                stopped = []
                with _lock:
                    for jid in ids:
                        job = _jobs.get(jid)
                        if job and not job.done:
                            job.force() if force else job.cancel()
                            stopped.append(jid)
                return self._send(200, {"ok": True, "stopped": stopped})
            if path == "/api/run":
                argv = data.get("argv") or []
                if not argv:
                    return self._send(400, {"error": "no command"})
                job, other = start_job([str(a) for a in argv],
                                       device=str(data.get("device") or ""))
                if other is not None:
                    # 409: the request is fine, the moment is not. Say what is
                    # in the way, so the page can name it rather than shrug.
                    return self._send(409, {
                        "error": f"{_job_label(other.argv)} is already running. "
                                 "One job at a time — stop that one first, or "
                                 "wait for it to finish.",
                        "running": {"id": other.id, "argv": other.argv,
                                    "elapsed": round(time.time() - other.started, 1),
                                    **other.progress()}})
                return self._send(200, {"id": job.id})
            if path == "/api/focus/vote":
                from . import taste
                folder, stem = str(data.get("folder") or ""), str(data.get("stem") or "")
                if not folder or not stem:
                    return self._send(400, {"error": "folder and stem are required"})
                row = self._quality_row(folder, stem) or {}
                vote = data.get("vote")
                return self._send(200, taste.record(
                    folder, stem, vote if vote in ("sharp", "soft") else None,
                    blur_ratio=row.get("blur_ratio"), verdict=row.get("verdict")))
            if path == "/api/run-state":
                # The run belongs to the job, not to the device holding it.
                f = Path.home() / ".cullkit" / "run.json"
                f.parent.mkdir(parents=True, exist_ok=True)
                data["saved"] = time.time()
                _write_whole(f, json.dumps(data, indent=2))
                return self._send(200, {"ok": True})

            if path == "/api/control":
                # "I am driving now." Whoever asks gets the controls, and every
                # other device finds out on its next poll and puts up a curtain.
                # Deliberately not a negotiation: the person pressing the button
                # is in the room, and the other device probably is not.
                return self._send(200, {"ok": True,
                                        "control": self._take_control()})

            if path == "/api/screen":
                # Which screen the device holding the controls is standing on.
                # Every other note here follows *work* - a job, a run, the
                # darkroom, a result. This is the rest: a folder half-picked,
                # Overview, the list of tasks. Picking up the other device
                # should land on it too, not on Home.
                if self._holds():
                    Handler._screen = {"view": str(data.get("view") or ""),
                                       "device": str(data.get("device") or ""),
                                       "at": time.time()}
                return self._send(200, {"ok": True})

            if path == "/api/control/release":
                # The page on its way out (sendBeacon on pagehide). This must
                # NOT hand the controls back on the spot: pagehide also fires
                # on a refresh, and a refresh must not cost the phone its
                # controls. Instead the device's clock is aged so the silence
                # check fires in ~5 seconds - a page that comes back polls
                # again and nothing happens; a page that is really gone lets
                # the computer reclaim almost at once instead of in 30 s.
                key = self._key()
                if Handler._control.get("holder") == key:
                    seen = Handler._seen.get(key) or {"ip": self._who()}
                    seen["last"] = min(seen.get("last", 0), time.time() - 25)
                    Handler._seen[key] = seen
                return self._send(200, {"ok": True})

            if path == "/api/selection":
                # Saved as you go, so closing the iPad mid-review loses nothing.
                # No folder is not a folder. Path("") is ".", the program's own
                # directory - so a save that fired a moment after the page had
                # left the darkroom wrote a selection into the program and
                # declared a darkroom open on it.
                if not str(data.get("folder") or "").strip():
                    return self._send(200, {"ok": True, "count": 0, "gone": True})
                folder = Path(data["folder"])
                if not folder.is_dir():
                    # The shoot has just gone to the discards with the rest of
                    # its frames. Writing here would rebuild an empty shell of
                    # it at the old address.
                    return self._send(200, {"ok": True, "count": 0, "gone": True})
                out = folder / "_cullkit"
                out.mkdir(parents=True, exist_ok=True)
                (out / "selection.json").write_text(
                    json.dumps({"folder": str(folder),
                                "picked": data.get("picked", []),
                                "skipped": data.get("skipped", []),
                                "mode": data.get("mode", ""),
                                # Where you had got to, not just what you chose.
                                # Without these, picking the darkroom up on
                                # another device drops you back at the first
                                # group of ninety-two.
                                "gi": data.get("gi", 0),
                                "i": data.get("i", 0),
                                "saved": time.strftime("%Y-%m-%d %H:%M:%S")},
                               indent=2), encoding="utf-8")
                _mark_darkroom(str(folder), data.get("device", ""),
                               data.get("mode", ""))
                return self._send(200, {"ok": True, "count": len(data.get("picked", []))})
            if path == "/api/place":
                _mark_place(str(data.get("view") or ""),
                            str(data.get("folder") or ""),
                            str(data.get("cmd") or ""),
                            str(data.get("device") or ""))
                return self._send(200, {"ok": True})
            if path == "/api/darkroom/open":
                # Walking in is the event, not choosing your first photograph.
                # This only writes the pointer - never selection.json - so it
                # cannot overwrite picks with an empty list on the way in.
                if str(data.get("folder") or "").strip():
                    _mark_darkroom(str(data.get("folder") or ""),
                                   str(data.get("device") or ""),
                                   str(data.get("mode") or ""))
                return self._send(200, {"ok": True})
            if path == "/api/vibes":
                from . import vibes as V
                saved = V.save(data.get("vibes") or [])
                # A vibe that has gone takes its music with it. Left behind it
                # is a folder of files nothing can reach and nothing can
                # delete, quietly using the disk for ever.
                keep = {v["id"] for v in saved}
                if V.AUDIO.is_dir():
                    for d in V.AUDIO.iterdir():
                        if d.is_dir() and d.name not in keep:
                            V.forget_all(d.name)
                # The same for audio fetched from YouTube: gone from every
                # vibe, gone from the disk - and what was just added is
                # fetched now, behind whoever added it.
                from . import ytm
                yt_ids = [t["id"] for v in saved for t in (v.get("yt") or [])]
                ytm.prune(yt_ids)
                ytm.prefetch(yt_ids)
                journal.write("vibes.saved", count=len(saved),
                              names=[v["name"] for v in saved])
                return self._send(200, {"ok": True, "vibes": saved})
            if path == "/api/vibes/forget-track":
                from . import vibes as V
                ok = V.forget_track(data.get("id", ""), data.get("file", ""))
                return self._send(200, {"ok": ok})
            if path == "/api/vibes/clear-tracks":
                # Every track of one vibe's own, without touching its name,
                # icon or colour - the difference between "start this vibe's
                # playlist over" and "remove the vibe".
                from . import vibes as V
                vid = str(data.get("id") or "")
                V.forget_all(vid)
                journal.write("vibes.cleared", vibe=vid)
                return self._send(200, {"ok": True})
            if path == "/api/settings":
                # Deliberately one field. `work_root` and a separate "quick"
                # folder were tried here and asked to be removed - the archive
                # is the only folder worth a quick setting, and the working
                # folder stays where `cull config --work-root` left it.
                from . import ledger as L
                cfg = L.load_config()
                changed = {}
                if "archive_root" in data:
                    cfg["archive_root"] = str(data["archive_root"])
                    changed["archive_root"] = cfg["archive_root"]
                L.save_config(cfg)
                journal.write("settings.saved", changed=changed)
                return self._send(200, {"ok": True, "config": cfg})
            if path == "/api/pin/new":
                # Only from the machine with the photographs on it. A phone
                # that already has the code must not be able to change it and
                # shut the others out.
                if not self._is_local(self._who()):
                    return self._send(403, {"error": "only from the computer"})
                return self._send(200, {"ok": True, "pin": _new_access_pin()})
            if path == "/api/reset":
                return self._send(200, {"ok": True,
                                        "cleared": _reset_everything()})
            if path == "/api/darkroom/close":
                _mark_darkroom("", "", "", closing=True)
                return self._send(200, {"ok": True})
            if path == "/api/journal":
                # The page's own account of itself. Most of the faults in this
                # app have been in the browser, where nothing on this machine
                # could see them - a script error on the tablet at 2am left no
                # record anywhere. Now it does.
                kind = str(data.get("kind") or "ui.note")
                if not kind.startswith("ui."):
                    kind = "ui." + kind          # the page speaks only for itself
                fields = {k: v for k, v in data.items() if k != "kind"}
                journal.write(kind, **fields)
                return self._send(200, {"ok": True})
            if path == "/api/journal/clear":
                return self._send(200, {"ok": True, **journal.clear()})
            if path.startswith("/api/files/"):
                return self._send(200, self._files_post(path, data))
            if path == "/api/fs/rename":
                return self._send(200, self._fs_rename(data))
            if path == "/api/fs/move":
                return self._send(200, self._fs_move(data))
        except Exception as exc:
            # Until now this went back to whoever asked and nowhere else, so a
            # request that blew up while the app was closed left no trace at
            # all on the machine that failed it.
            journal.write("http.error", path=path, method=self.command,
                          error=f"{type(exc).__name__}: {exc}",
                          traceback=traceback.format_exc()[-4000:])
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        self._send(404, {"error": "not found"})

    def _take_track(self):
        """One music file, arriving as its own bytes.

        No multipart parser, because there is nothing to parse: one file per
        request, its name in a header, its bytes in the body. The size cap is
        checked against the declared length *before* reading, so a bad or
        hostile Content-Length costs nothing rather than a gigabyte of memory.
        """
        from . import report, vibes as V

        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0:
            return self._send(400, {"error": "no file"})
        if n > V.MAX_UPLOAD:
            return self._send(413, {
                "error": f"that file is {report.human(n)} — the limit is "
                         f"{report.human(V.MAX_UPLOAD)} per track"})
        vid = self.headers.get("X-Vibe-Id", "")
        name = urllib.parse.unquote(self.headers.get("X-Track-Name", "track"))
        if not vid:
            return self._send(400, {"error": "no vibe"})
        data = self.rfile.read(n)
        got = V.add_track(vid, name, data)
        journal.write("vibes.track" if got.get("ok") else "vibes.track-refused",
                      vibe=vid, name=name, bytes=len(data),
                      error=got.get("error"))
        return self._send(200 if got.get("ok") else 400, got)

    # ------------------------------------------------------------------- data
    def _file(self, f: Path, ctype):
        if not f.is_file():
            return self._send(404, {"error": f"missing {f.name}"})
        self._send(200, f.read_bytes(), ctype)

    def _read_json(self, f: Path):
        if not f.is_file():
            return {"missing": True, "path": str(f)}
        # Read between another request truncating the file and filling it
        # again, the file is empty and `json.loads` raises - which is how
        # /api/state answered one poll with a 500 on 2026-11-08, the second a
        # run saved its place. Writers now swap the file in whole; this is
        # the other half, for a file some other program is rewriting.
        for attempt in range(3):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                if attempt == 2:
                    raise
                time.sleep(0.04)

    # Walking every known cache on an external drive is not free, so the answer
    # is held for a minute. Cache size does not change second to second.
    _cache_stat = {"at": 0.0, "data": None}

    def _cache_size(self):
        from . import ledger as L

        now = time.time()
        if Handler._cache_stat["data"] and now - Handler._cache_stat["at"] < 60:
            return Handler._cache_stat["data"]

        cfg = L.load_config()
        warn_gb = float(cfg.get("cache_warn_gb") or 2)
        total, folders, drives = 0, 0, set()
        for c in L.caches(only_existing=True):
            prev = Path(c) / "_cullkit" / "previews"
            size = 0
            try:
                for f in prev.glob("*.jpg"):
                    size += f.stat().st_size
            except OSError:
                pass
            if size:
                folders += 1
                total += size
                drives.add(__import__("cullkit.plat", fromlist=["x"]).mount_of(c))

        gb = total / (1024 ** 3)
        level = "high" if gb >= warn_gb * 2.5 else ("warn" if gb >= warn_gb else "ok")

        # A full disk is the real problem; a big cache on a roomy drive is not.
        tight = []
        from . import plat
        for d in drives:
            tot, free = plat.disk_usage(d)
            if tot and free / tot < 0.10:
                tight.append(d)
        if tight:
            level = "high"

        data = {"bytes": total, "folders": folders, "warn_gb": warn_gb,
                "level": level, "tight_drives": tight}
        Handler._cache_stat = {"at": now, "data": data}
        return data

    # Who has been talking to this server lately. The computer itself is not
    # interesting; a phone or tablet on the wifi is.
    _seen = {}

    # Who is driving. Two devices on one run is how files end up in the wrong
    # place, so exactly one holds the controls and the other is shown a curtain.
    # The computer starts with them, because it is the thing that was here first.
    _control = {"holder": "local", "device": "This computer", "at": 0.0}
    _screen = {"view": "", "device": "", "at": 0.0}

    @staticmethod
    def _is_local(host):
        return host in ("127.0.0.1", "::1")

    _DEV_RE = re.compile(r"^[a-z0-9]{6,40}$")

    def _key(self):
        """Who is asking, as a device rather than as an address.

        The page sets `aftershot_dev` to the id it keeps in its own storage,
        so one phone stays one phone across wifi, Tailscale and mobile data.
        A page too old to set it is known by its address, as before.
        """
        host = self._who()
        if Handler._is_local(host):
            return host
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, val = part.strip().partition("=")
            if name == "aftershot_dev" and Handler._DEV_RE.match(val):
                return "dev:" + val
        return host

    def _take_control(self):
        """Hand the controls to whoever is making this request."""
        host = self._who()
        if Handler._is_local(host):
            control = {"holder": "local", "device": "This computer",
                       "at": time.time()}
        else:
            key = self._key()
            seen = Handler._seen.get(key) or {}
            who = "%s · %s" % (seen.get("device", "device"),
                               seen.get("browser", "browser"))
            control = {"holder": key, "device": who, "at": time.time()}
        if control["holder"] != Handler._control.get("holder"):
            journal.write("control.taken", holder=control["holder"],
                          device=control["device"],
                          was=Handler._control.get("holder"))
        Handler._control = control
        _save_control(control)
        changed()
        return Handler._control

    def _note_client(self):
        host = self._who()
        if Handler._is_local(host):
            return
        key = self._key()
        ua = self.headers.get("User-Agent", "")
        kind = "device"
        for word, name in (("iPad", "iPad"), ("iPhone", "iPhone"),
                           ("Android", "Android phone"), ("Macintosh", "Mac"),
                           ("Windows", "Windows PC"), ("Linux", "Linux machine")):
            if word in ua:
                kind = name
                break
        browser = ("Safari" if "Safari" in ua and "Chrome" not in ua else
                   "Chrome" if "Chrome" in ua else
                   "Firefox" if "Firefox" in ua else "browser")
        prev = Handler._seen.get(key) or {}
        # A device that polls repeatedly is a session; one request is a probe.
        hits = prev.get("hits", 0) + 1 if time.time() - prev.get("last", 0) < 300 else 1
        Handler._seen[key] = {"ip": host, "key": key, "device": kind,
                              "browser": browser, "last": time.time(),
                              "hits": hits}
        # A device that has just arrived takes the controls, and the computer
        # is locked the moment it does. That is what opening the app on your
        # phone means - you are about to use it there.
        #
        # Only on its FIRST request of a session, though. A phone left open in
        # a pocket keeps polling, and if arriving took the controls every time
        # it would snatch them straight back off the computer a second after
        # you pressed "Take over here". After that first request the phone
        # holds them until somebody deliberately takes them somewhere else.
        if hits == 1:
            self._take_control()

    def _remote_clients(self, within=90):
        now = time.time()
        return [dict(v, seconds_ago=round(now - v["last"]), hits=v.get("hits", 1))
                for v in Handler._seen.values() if now - v["last"] < within]

    # Listing drives touches every mounted volume, and a job hammering an
    # external drive can make that block for seconds. The heartbeat that
    # polls this every second or two would rather answer instantly with a
    # slightly stale list than sit behind the job's own disk I/O and trip
    # the page's "lost the connection" timeout.
    _vols_stat = {"at": 0.0, "data": None}

    def _volumes(self):
        from . import importer as I

        now = time.time()
        if (Handler._vols_stat["data"] is not None
                and now - Handler._vols_stat["at"] < 5):
            return Handler._vols_stat["data"]
        try:
            vols = []
            for v in I.list_volumes(count_media=False):
                vols.append({
                    "letter": v.letter, "tag": v.tag, "label": v.label, "kind": v.kind,
                    "total": v.total, "free": v.free, "used": v.used,
                    "card": bool(v.dcim and v.kind == "removable"),
                    "path": str(v.path),
                })
        except OSError:
            return Handler._vols_stat["data"] or []
        Handler._vols_stat = {"at": now, "data": vols}
        return vols

    def _state(self):
        from . import ledger as L

        self._reclaim_if_gone()
        cfg = L.load_config()
        vols = self._volumes()
        led = L.load()
        # What is executing, from the machine doing it rather than from whichever
        # browser happened to start it. Overview asked the page, and the page only
        # knows about jobs it launched itself - so a job started on the phone left
        # the laptop's Overview looking idle, and a standalone Sift showed nothing
        # anywhere, because only a full Aftershot run was ever counted.
        with _lock:
            live = [j for j in _jobs.values() if not j.done]
        running = [{"id": j.id, "argv": j.argv, "device": j.device,
                    "elapsed": round(time.time() - j.started, 1),
                    **j.progress()} for j in live]
        return {
            # Whether a chained run is still open, so a device that was not the
            # one to end it finds out. Ending it used to be known only to the
            # browser that pressed the button; every other screen carried on
            # showing a run that no longer existed.
            "sep": os.sep,       # a backslash on Windows, a slash on a Mac
            "run_active": bool(_read_state(
                Path.home() / ".cullkit" / "run.json").get("active")),
            # Which darkroom is open, so a page sitting in one that has been
            # closed elsewhere finds out and steps out of it.
            "darkroom": _open_darkroom().get("folder", ""),
            # The device that last did something. A page that finds itself here
            # is already where it was, and should stay put.
            "last_actor": _last_actor().get("device", ""),
            "program": _program_state(),
            # The last job to end, so a screen that was not watching still
            # knows how it went. This is the difference between "it finished"
            # and "it finished, 412 files copied, the card is safe to wipe".
            "last_done": dict(LAST_DONE),
            # How long the program has been up. A phone in another room cannot
            # tell "nothing is running" from "the computer went to sleep and
            # this page is showing you the last thing it heard", and those are
            # opposite situations. A clock that keeps moving is the difference.
            "started": STARTED, "now": time.time(),
            "config": cfg, "drives": vols, "running": running,
            "shoots": L.shoots(),
            # Every one, not the last 8 - a card copied and never sifted used
            # to disappear from every history the app could show, because
            # this was the only place that remembered it happened at all.
            "imports": led.get("imports", []),
            "cache": self._cache_size(),
            "addresses": local_addresses(),
            "tailnet": tailnet_url(),
            "port": self.server.server_address[1],
            "remote": self._remote_clients(),
            "you_are_remote": not self._is_local(self._who()),
            "control": Handler._control,
            # Answered per asker, so each device can just believe what it is
            # told rather than working out whether "local" means itself.
            "you_hold": self._holds(),
            "version": _version(),
            "screen": dict(Handler._screen,
                           seconds_ago=round(time.time() - Handler._screen["at"])),
        }

    def _holds(self):
        holder = Handler._control.get("holder")
        if self._is_local(self._who()):
            return holder == "local"
        return holder == self._key()

    def _reclaim_if_gone(self, silence=30):
        """A remote device that has stopped talking has left the building.

        Closing the tab on the phone used to leave it holding the controls
        forever, with the computer stuck behind the curtain saying someone was
        still there. A holding device polls every 4 seconds, so half a minute
        of silence is not a hiccup - it is gone, and the controls come home.
        """
        holder = Handler._control.get("holder")
        if holder == "local":
            return
        seen = Handler._seen.get(holder) or {}
        if time.time() - seen.get("last", 0) > silence:
            journal.write("control.reclaimed", was=holder,
                          silent=round(time.time() - seen.get("last", 0)))
            Handler._control = {"holder": "local", "device": "This computer",
                                "at": time.time()}
            _save_control(Handler._control)
            changed()

    def _audio(self):
        """Every track in web/audio, grouped by which mood it belongs to.

        A file belongs to a mood if its name starts with that word, so dropping
        `metal-2.mp3` or `schranz whatever.mp3` into the folder is all it takes
        to add to that playlist. No config, no restart.
        """
        moods = {"selenge": "afro", "afro": "afro", "metal": "metal",
                 "schranz": "schranz", "reggae": "reggae"}
        out = {"afro": [], "metal": [], "schranz": [], "reggae": []}
        folder = WEB / "audio"
        if not folder.is_dir():
            return out
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.suffix.lower() not in (".mp3", ".m4a", ".ogg", ".wav"):
                continue
            stem = f.stem.lower()
            for word, mood in moods.items():
                if stem.startswith(word):
                    out[mood].append({"url": f"/static/audio/{f.name}",
                                      "name": f.stem})
                    break
        return out

    def _note_darkroom(self, folder, device):
        """Somebody is in the darkroom, on this folder, right now.

        Written from the request rather than from a message the page chooses to
        send, because a page that has not been reloaded since this feature
        existed sends no such message - and "I have it open, that should be
        enough" is exactly the promise being made. Falls back to the caller's
        address when the page does not name itself: it only has to tell one
        device from another.
        """
        if not folder:
            return
        try:
            if not (Path(folder) / "_cullkit").is_dir():
                return
            if _recently_closed():
                return          # somebody just shut it; presence does not undo that
            open_now = _open_darkroom()
            # Do not keep re-stamping the same one; once a minute is plenty to
            # keep it from going stale under somebody who is still working.
            if (open_now.get("folder") == folder
                    and time.time() - open_now.get("at", 0) < 60):
                return
            _mark_darkroom(folder, device or self._who(),
                           open_now.get("mode", "") if
                           open_now.get("folder") == folder else "")
        except Exception:
            pass

    def _caches(self):
        """Every folder the program has left a cache in, and what is in it now.

        The ledger has always remembered *where* it worked; nothing counted
        *what is there*. Both halves are needed for the same reason: you cannot
        agree to a deletion you have not been shown, and the button used to ask
        about a single configured root while the ledger knew about folders on
        two different drives.

        A drive that is unplugged is reported, not skipped. "That shoot lives
        on a disk you have not connected" is an answer; leaving it off the list
        silently is how a folder gets forgotten and quietly kept forever.
        """
        from . import ledger as L

        out = []
        for c in L.caches(only_existing=False):
            root = Path(c)
            cache = root / "_cullkit"
            item = {"path": str(root), "name": root.name, "drive": root.anchor,
                    "here": False, "previews": 0, "preview_bytes": 0,
                    "reports": 0, "report_bytes": 0, "journals": 0}
            try:
                item["here"] = cache.is_dir()
            except OSError:
                item["here"] = False
            if item["here"]:
                try:
                    for f in cache.rglob("*"):
                        if not f.is_file():
                            continue
                        try:
                            size = f.stat().st_size
                        except OSError:
                            continue
                        if f.name.startswith("undo-"):
                            item["journals"] += 1      # never deleted, never counted
                        elif f.suffix.lower() == ".jpg":
                            item["previews"] += 1
                            item["preview_bytes"] += size
                        else:
                            item["reports"] += 1
                            item["report_bytes"] += size
                except OSError:
                    pass
            out.append(item)

        here = [i for i in out if i["here"]]
        away = [i for i in out if not i["here"]]
        drives = sorted({i["drive"] for i in here})
        return {
            "folders": out,
            "drives": drives,
            # The activity log is not a cache and does not live beside the
            # photographs, so it gets its own line rather than being folded
            # into a total. It is listed here because this is the box that
            # asks permission, and it may not ask about something it has not
            # shown - which was the whole complaint that built this box.
            "journal": journal.summary(),
            "reachable": len(here),
            "unreachable": len(away),
            "preview_bytes": sum(i["preview_bytes"] for i in here),
            "previews": sum(i["previews"] for i in here),
            "report_bytes": sum(i["report_bytes"] for i in here),
            "reports": sum(i["reports"] for i in here),
            "journals": sum(i["journals"] for i in here),
        }

    @staticmethod
    def _quality_row(folder, stem):
        """This frame's line in Sift's scores. After the flagged frames are
        moved aside, they sit in Review or Rejects one level below the folder
        that was sifted, so the parent is asked too."""
        if not folder or not stem:
            return None
        here = Path(folder)
        for root in (here, here.parent):
            f = root / "_cullkit" / "quality.json"
            if not f.is_file():
                continue
            try:
                frames = json.loads(f.read_text(encoding="utf-8")).get("frames", [])
            except (OSError, ValueError):
                continue
            for row in frames:
                if row.get("base") == stem:
                    return row
        return None

    def _focus(self, folder, stem, preview):
        """Where to open the zoom on this frame, what Sift said, and your vote.

        Always the thing a photographer checks focus on, in this order: the
        face, the subject, the scene. A frame Sift judged on a face opens on
        that face. Any other frame is looked at now, from its preview - a quick
        face search, then the subject standing out from the background, then
        the sharpest spot - once, and remembered for the session.
        """
        from . import taste

        row = self._quality_row(folder, stem) or {}
        if not preview and folder and stem:
            preview = str(Path(folder) / "_cullkit" / "previews" / f"{stem}.jpg")
        if row.get("focus") and row.get("faces"):
            focus, kind = row["focus"], "face"
        else:
            focus, kind = self._look(preview)
            if focus is None:
                focus, kind = row.get("focus"), "scene"
        vote = taste.get(folder, stem)
        return {"focus": focus or [0.5, 0.5], "kind": kind,
                "measured": bool(row.get("focus")),
                "blur_ratio": row.get("blur_ratio"), "verdict": row.get("verdict"),
                "tier": row.get("tier"), "faces": row.get("faces"),
                "vote": vote["vote"] if vote else None, "taste": taste.summary()}

    _FULL = {}            # the last few full-size frames, newest last

    def _full(self, folder, stem):
        """Full-size JPEG of one frame, from its original file, for the zoom."""
        import glob as _glob

        from . import previews as P, scan as S

        if not folder or not stem:
            return None
        root = Path(folder)
        cands = [p for p in root.glob(_glob.escape(stem) + ".*") if p.is_file()]
        if not cands:          # a shoot kept in subfolders (cards, bodies)
            cands = [p for p in root.rglob(_glob.escape(stem) + ".*")
                     if p.is_file() and "_cullkit" not in p.parts]
        raws = [p for p in cands if p.suffix.lower() in S.RAW_EXTS]
        jpgs = [p for p in cands if p.suffix.lower() in (".jpg", ".jpeg", ".jpe")]
        src = (raws or jpgs or [None])[0]
        if src is None:
            return None
        try:
            key = (str(src), src.stat().st_mtime)
        except OSError:
            return None
        hit = self._FULL.pop(key, None)
        if hit is None:
            try:
                hit = P.full_jpeg(src)
            except (OSError, ValueError):
                return None
        self._FULL[key] = hit
        while len(self._FULL) > 6:     # ~6 frames of a 24 MP shoot in memory
            self._FULL.pop(next(iter(self._FULL)))
        return hit

    _LOOKED = {}

    def _look(self, preview):
        """(point, kind) for a preview with no Sift face, or (None, None)."""
        if not preview:
            return None, None
        p = Path(preview)
        try:
            key = (str(p), p.stat().st_mtime)
        except OSError:
            return None, None
        if key in self._LOOKED:
            return self._LOOKED[key]
        import cv2

        from . import faces as F, quality as Q

        img = cv2.imread(str(p))
        if img is None:
            return None, None
        h, w = img.shape[:2]
        s = min(1.0, 800.0 / max(h, w))
        if s < 1.0:
            img = cv2.resize(img, (round(w * s), round(h * s)),
                             interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        pt, kind = Q.look_point(gray, F.quick_faces(img))
        out = ([float(pt[0]), float(pt[1])], kind)
        if len(self._LOOKED) > 4000:
            self._LOOKED.clear()
        self._LOOKED[key] = out
        return out

    def _frames(self, folder):
        """Every frame in a folder, with whether its preview is already cached."""
        from . import scan as S

        root = Path(folder)
        if not root.is_dir():
            return {"error": f"not a folder: {folder}"}
        idx = S.scan(root)
        cache = root / "_cullkit" / "previews"
        out = []
        for st in idx.stacks:
            if not st.primary:
                continue
            jpg = cache / f"{st.primary.stem}.jpg"
            out.append({"base": st.base, "stem": st.primary.stem,
                        "frame": st.frame, "preview": str(jpg) if jpg.is_file() else None})
        ready = sum(1 for f in out if f["preview"])
        return {"folder": str(root), "frames": out,
                "total": len(out), "ready": ready}

    def _browse(self, path, want_files=False):
        from . import importer as I

        if not path:
            return {"path": "", "parent": None, "folders": [], "photos": 0}
        root = Path(path)
        if not root.is_dir():
            return {"error": f"not a folder: {path}"}
        folders = []
        try:
            for p in sorted(root.iterdir()):
                if not p.is_dir() or p.name.startswith(("$", ".")):
                    continue
                folders.append({
                    "name": p.name, "path": str(p),
                    "photos": I.count_photos(p, cap=3000),
                })
        except PermissionError:
            return {"error": "permission denied"}
        photos = 0
        files = []
        try:
            for f in sorted(root.iterdir()):
                if not f.is_file() or f.name.startswith(("$", ".")):
                    continue
                if f.suffix.lower() in I.MEDIA_EXTS:
                    photos += 1
                if want_files:
                    files.append(self._file_row(f))
        except OSError:
            pass
        parent = str(root.parent) if root.parent != root else None
        out = {"path": str(root), "parent": parent,
               "folders": folders, "photos": photos}
        if want_files:
            out["files"] = files
        return out

    # Preview-quality images (jpg/png/webp) are shown by pointing straight at
    # the file - a fresh resize is cheap for a browser-sized thumbnail and
    # nothing is written anywhere. A RAW gets a thumbnail only if this shoot has
    # already been culled and left one behind in `_cullkit\previews\` - this
    # panel must never trigger the extraction that step normally does, or
    # glancing at a card of untouched RAWs would quietly start a long job.
    _DIRECT_THUMB = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

    def _file_row(self, f):
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        ext = f.suffix.lower()
        thumb = None
        if ext in self._DIRECT_THUMB:
            thumb = str(f)
        else:
            cached = f.parent / "_cullkit" / "previews" / f"{f.stem}.jpg"
            if cached.is_file():
                thumb = str(cached)
        return {"name": f.name, "path": str(f), "size": size, "ext": ext,
                "thumb": thumb}

    # ------------------------------------------------------------ the explorer
    # The Files screen. The work is in files.py; this is only the door, and
    # the one rule that belongs to the session rather than to the disk: nothing
    # here changes a file while a job is running.
    def _files_busy(self):
        with _lock:
            live = [j for j in _jobs.values() if not j.done]
        if not live:
            return ""
        return ("Something is running. Files can be looked at, but not moved, "
                "renamed, copied or binned until it has finished.")

    def _files_get(self, path, q):
        from . import files as F, ledger as L

        what = path[len("/api/files/"):]
        target = q.get("path", "")
        if what == "home":
            return self._send(200, {"drives": self._volumes(),
                                    "places": F.places(L.load_config()),
                                    "local": Handler._is_local(self._who())
                                             and not self._via_tailnet()})
        if what == "list":
            return self._send(200, F.listing(target))
        if what == "search":
            return self._send(200, F.search(target, q.get("q", "")))
        if what == "info":
            return self._send(200, F.info(target))
        if what == "thumb":
            try:
                size = int(q.get("size", 320))
            except ValueError:
                size = 320
            got = F.thumb(target, size)
            if got is None:
                return self._send(404, {"error": "no picture for this file"})
            return self._send(200, got, "image/jpeg", cache=True)
        if what == "raw":
            return self._files_stream(Path(target), download=q.get("dl") == "1")
        if what == "play":
            return self._files_play(Path(target), q.get("t", "0"))
        return self._send(404, {"error": "not found"})

    def _files_stream(self, f: Path, download=False):
        """A file's own bytes, in pieces.

        Read and sent a megabyte at a time rather than loaded whole - a video
        off the card is several gigabytes - and honouring Range, which is what
        lets a phone scrub through that video instead of waiting for all of it.
        """
        from . import files as F

        if not f.is_file():
            return self._send(404, {"error": "no such file"})
        size = f.stat().st_size
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range") or ""
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", F.content_type(f))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            name = urllib.parse.quote(f.name)
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{name}")
        elif not F.content_type(f).startswith(
                ("video/", "audio/", "application/pdf", "text/plain", "image/jpeg",
                 "image/png", "image/gif", "image/webp", "image/bmp")):
            # Shown in the page, never run by it: a .html or .svg off a drive
            # must not get to act as this app. The kinds let through cannot
            # carry script - and a PDF or a video will not play sandboxed.
            self.send_header("Content-Security-Policy", "sandbox")
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _files_play(self, f: Path, start="0"):
        """A format the browser cannot open, converted while it is watched.

        The length is not known ahead of time, so there is no Content-Length
        and the connection is closed at the end instead - which is also why it
        cannot be scrubbed. When the viewer goes away ffmpeg is stopped; left
        alone it would go on converting a two-hour file for nobody.
        """
        from . import files as F

        if not f.is_file():
            return self._send(404, {"error": "no such file"})
        try:
            t = float(start)
        except ValueError:
            t = 0.0
        proc, ctype = F.transcoder(f, t)
        if proc is None:
            return self._send(415, {"error": ctype})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                chunk = proc.stdout.read(64 << 10)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                proc.kill()
            except OSError:
                pass

    def _files_upload(self):
        from . import files as F

        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0:
            return self._send(400, {"error": "no file"})
        busy = self._files_busy()
        dest = urllib.parse.unquote(self.headers.get("X-Dest", ""))
        name = urllib.parse.unquote(self.headers.get("X-Name", ""))
        if busy:
            # The body still has to be read, or the connection is left with a
            # request half-sent and the browser reports a network failure
            # instead of the reason.
            left = n
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                left -= len(chunk)
            return self._send(409, {"error": busy})
        got = F.save_upload(dest, name, self.rfile, n)
        journal.write("files.upload", dest=dest, name=name, bytes=n,
                      error=got.get("error"))
        return self._send(200 if got.get("ok") else 400, got)

    def _files_post(self, path, data):
        from . import files as F

        what = path[len("/api/files/"):]
        paths = [str(x) for x in (data.get("paths") or [])]
        if what in ("open", "reveal"):
            # Opens a window on this computer's screen, so only this computer
            # may ask for it. From a phone it would be a prank.
            if not Handler._is_local(self._who()) or self._via_tailnet():
                return {"error": "that only works from the computer itself"}
            return F.open_on_pc(str(data.get("path") or ""), reveal=what == "reveal")
        busy = self._files_busy()
        if busy:
            return {"error": busy}
        device = str(data.get("device") or "")
        if what == "mkdir":
            got = F.mkdir(str(data.get("path") or ""), str(data.get("name") or ""))
        elif what == "copy":
            got = F.copy(paths, str(data.get("dest") or ""))
        elif what == "trash":
            got = F.trash(paths)
        elif what == "move":
            why = F.check_protected(paths)
            got = {"error": why} if why else self._fs_move(data)
        elif what == "rename":
            why = F.check_protected([str(data.get("path") or "")])
            got = {"error": why} if why else self._fs_rename(data)
        else:
            return {"error": "not found"}
        journal.write("files." + what, device=device, n=len(paths) or 1,
                      error=got.get("error"),
                      skipped=len(got.get("skipped") or []))
        return got

    def _fs_rename(self, data):
        """Rename a file or folder in place - never crosses folders.

        A move already exists as its own action; this only ever changes the
        last path segment, and refuses anything that tries to sneak a
        separator or a bare drive letter into the new name.
        """
        old = Path(str(data.get("path") or ""))
        name = str(data.get("name") or "").strip()
        if not old.exists():
            return {"error": "that no longer exists"}
        if not name or any(c in name for c in '\\/:*?"<>|') or name in (".", ".."):
            return {"error": "not a valid name"}
        new = old.parent / name
        if new.exists():
            return {"error": f"“{name}” is already taken here"}
        try:
            old.rename(new)
        except OSError as exc:
            return {"error": str(exc)}
        journal.write("fs.rename", old=str(old), new=str(new),
                      device=str(data.get("device") or ""))
        return {"ok": True, "path": str(new)}

    def _fs_move(self, data):
        """Move one or more files/folders into a destination folder.

        Every applied move writes the same undo journal `work`/`commit` write -
        `actions.execute` is reused rather than re-implemented, so `cull undo`
        already knows how to put these back. A name already taken at the
        destination is skipped and reported, never overwritten; a folder can
        never be dropped into itself or one of its own children.
        """
        from . import actions as A

        dest = Path(str(data.get("dest") or ""))
        sources = [Path(str(s)) for s in (data.get("paths") or [])]
        if not dest.is_dir():
            return {"error": "destination is not a folder"}
        if not sources:
            return {"error": "nothing chosen"}

        plan = A.Plan(dest=dest, mode=A.MOVE)
        skipped = []
        for src in sources:
            if not src.exists():
                skipped.append({"name": src.name, "why": "no longer there"})
                continue
            try:
                dest_r, src_r = dest.resolve(), src.resolve()
            except OSError:
                dest_r, src_r = dest, src
            if src.is_dir() and (dest_r == src_r or src_r in dest_r.parents):
                skipped.append({"name": src.name, "why": "can't move a folder into itself"})
                continue
            target = dest / src.name
            if target.exists():
                skipped.append({"name": src.name, "why": "already taken there"})
                continue
            plan.ops.append(A.Op(src, target, A.MOVE))

        journal_path = A.execute(plan, apply=True, journal_dir=dest / "_cullkit")
        moved = [op.as_json() for op in plan.ops if op.status == "done"]
        failed = [op.as_json() for op in plan.ops if op.status == "failed"]
        journal.write("fs.move", dest=str(dest), moved=len(moved),
                      failed=len(failed), skipped=len(skipped),
                      device=str(data.get("device") or ""))
        return {"ok": True, "moved": moved, "failed": failed, "skipped": skipped,
                "undo": str(journal_path) if journal_path else None}


def local_addresses():
    """Every address this machine answers on, so the phone can be told where to
    look after the laptop moves to a different wifi."""
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")) or ip in out:
                continue
            out.append(ip)
    except Exception:
        pass
    primary = local_ip()
    if primary and primary not in out and not primary.startswith("127."):
        out.insert(0, primary)
    return out


def _tailscale_cli():
    """Where the Tailscale command lives, or nothing if it is not installed."""
    from . import plat
    return plat.tailscale_cli()


def _ts(*args, timeout=4):
    """Ask the Tailscale command something, quietly. Empty on any trouble."""
    cli = _tailscale_cli()
    if not cli:
        return ""
    try:
        # CREATE_NO_WINDOW: this runs while the app is polling, and a console
        # flashing on screen every minute would be its own kind of bug.
        r = subprocess.run([cli, *args], capture_output=True, text=True,
                           timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


# Two commands per minute is nothing; two per poll would be silly.
_tailnet = {"at": 0.0, "name": "", "url": ""}


def tailnet_name():
    """This machine's Tailscale name - `your-pc.your-tailnet.ts.net` or similar.

    Read live rather than saved, because it follows the machine's name and
    there is nowhere else to learn it from.
    """
    _tailnet_refresh()
    return _tailnet["name"]


def tailnet_url():
    """The https:// address, but only while it actually answers.

    Requires two things to be true at once: Tailscale is up, and it has been
    told to put HTTPS in front of this port (`tailscale serve`). If either is
    missing this is empty, because an address that does not answer is worse
    than no address at all.
    """
    _tailnet_refresh()
    return _tailnet["url"]


def _tailnet_refresh(ttl=60):
    """Kick off a refresh at most once a minute, and never make anyone wait.

    Asking Tailscale means two commands, and the page asks for state every
    couple of seconds. Doing that inside the request would hang the whole app
    for as long as Tailscale takes to answer, which on a sleeping network is
    seconds. The answer changes about never, so the caller gets the last one
    and the new one lands a moment later.
    """
    if time.time() - _tailnet["at"] < ttl:
        return
    _tailnet["at"] = time.time()  # claimed before starting, so two polls
    threading.Thread(target=_tailnet_read, daemon=True).start()  # start one job


def _serving_https(cfg):
    """Is Tailscale actually putting HTTPS in front of anything?

    `tailscale serve status --json` answers
    `{"TCP": {"443": {"HTTPS": true}}, "Web": {...}}` - the port is the key,
    bare, with no host in front of it. Guessing that shape wrong is silent:
    the address simply never appears, and nothing says why.
    """
    return bool(((cfg or {}).get("TCP") or {}).get("443", {}).get("HTTPS"))


def _tailnet_read():
    name, url = "", ""
    try:
        st = json.loads(_ts("status", "--json") or "{}")
        if st.get("BackendState") == "Running":
            name = (st.get("Self") or {}).get("DNSName", "").strip(".")
    except Exception:
        pass
    if name:
        try:
            if _serving_https(json.loads(_ts("serve", "status", "--json") or "{}")):
                url = f"https://{name}"
        except Exception:
            pass
    _tailnet["name"], _tailnet["url"] = name, url


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


"""Which darkroom is open, if any.

The picks have always been written beside the photographs, so a closed lid
loses nothing. What was never written down is that a darkroom *is* open, and
which folder it is looking at — so the second device had no way to know there
was anything to walk back into. It is a pointer, not a copy: one small file
saying where to look, next to `run.json` which does the same job for a run.
"""
DARKROOM = Path.home() / ".cullkit" / "darkroom.json"

"""Who owns the session: whoever last did something.

Not whoever last *looked* - a phone left open on a table polls all day and has
done nothing. Doing something is starting a job, stopping one, choosing a
photograph, walking into or out of the darkroom, opening a result. When a
device comes back and finds it is still the owner, the right thing to do is
nothing at all: it is already where it was. Only when somebody else has acted
since does it make sense to go and see what they did.

Every rule about resuming hangs off this, which is why it is written down once
here rather than decided again in four separate places.
"""
SESSION = Path.home() / ".cullkit" / "session.json"
ACTIONS = frozenset({
    "/api/run", "/api/job/stop", "/api/run-state",
    "/api/selection", "/api/darkroom/open", "/api/darkroom/close", "/api/place",
    "/api/fs/rename", "/api/fs/move",
})


"""Telling every screen the moment something changes.

Each page used to find out by asking, every four to twenty seconds - so taking
the controls on the phone left the computer looking usable for up to twenty
seconds, and ending a task on one device left it running on the other screen
for as long. Now a page keeps one request open at `/api/wait`, and the server
answers it the instant anything a screen would draw differently happens:
the controls change hands, a job starts, stops or ends, somebody acts. The
answer carries nothing but a number; the page then asks for the state as it
always has, so there is still exactly one place the state comes from.

The ordinary poll stays as it was, underneath. If the wait is ever cut - a
proxy, a sleeping radio - nothing is worse than before.
"""
_changes = threading.Condition()
_ver = {"n": 0}


def changed():
    with _changes:
        _ver["n"] += 1
        _changes.notify_all()


def _version():
    return _ver["n"]


def wait_for_change(since, timeout=25.0):
    with _changes:
        _changes.wait_for(lambda: _ver["n"] != since, timeout=timeout)
        return _ver["n"]


"""Who holds the controls survives a restart.

It lived only in memory, so restarting the program - or the program being
restarted for you - quietly handed the controls back to the computer while
the phone was in the middle of using them.
"""
CONTROL = Path.home() / ".cullkit" / "control.json"


def _write_whole(path, text):
    """Replace a small state file in one step, never in two.

    `write_text` empties the file and then fills it. Every screen polls these
    files, so sooner or later a poll lands in between and reads nothing. Written
    beside it and swapped in, a reader sees the old file or the new one.

    Windows refuses the swap for as long as a reader has the target open,
    which is microseconds at a time but, with every screen polling, often. So
    it is tried again for up to a quarter of a second before giving up and
    writing the plain way - a torn read is retried by the reader, a lost save
    is not recoverable by anybody.
    """
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(50):
        try:
            tmp.replace(path)
            return
        except OSError:
            time.sleep(0.005)
    path.write_text(text, encoding="utf-8")
    try:
        tmp.unlink()
    except OSError:
        pass


"""A small state file that could not be read is not a small state file that
says nothing.

Every reader of these used to treat the two the same: any failure came back as
`{}`, and `{}` means "no darkroom is open", "no run is active". But Windows
refuses to open a file for the instant it is being swapped, and every pick in
the darkroom swaps two of them while every screen is being told to come and
look. So sooner or later a poll landed on that instant, the server answered
"nothing is open", and the page - believing it - stepped out of the darkroom
and went Home in the middle of somebody choosing photographs. The journal has
it twenty-one times in two weeks.

So a read is tried again for a quarter of a second, and if it still cannot be
had, the last answer that *was* read stands. Only a file that is really not
there says nothing.
"""
_last_good = {}


def _read_state(path):
    for attempt in range(25):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _last_good[str(path)] = d
                return dict(d)
        except FileNotFoundError:
            if attempt >= 2:              # gone, not mid-swap
                _last_good.pop(str(path), None)
                return {}
        except (OSError, ValueError):
            pass
        time.sleep(0.01)
    journal.write("state.unreadable", file=path.name,
                  kept=str(path) in _last_good)
    return dict(_last_good.get(str(path)) or {})


def _save_control(control):
    try:
        CONTROL.parent.mkdir(parents=True, exist_ok=True)
        _write_whole(CONTROL, json.dumps(control, indent=2))
    except OSError:
        pass


def _load_control():
    """The holder from before a restart, given half a minute to come back."""
    try:
        control = json.loads(CONTROL.read_text(encoding="utf-8"))
    except Exception:
        return
    holder = control.get("holder")
    if not holder or holder == "local":
        return
    Handler._control = control
    Handler._seen[holder] = {"ip": "", "key": holder, "device": "device",
                             "browser": "browser", "last": time.time(),
                             "hits": 2}


"""The computer stays awake while it is being used.

It slept on its own schedule - ten minutes on battery - and nothing in the
program asked it not to, not even halfway through a job. On 25 October it
slept from 08:11 to 10:43 in the middle of a Separate Projects, and every
phone looking at it in that time saw "lost the connection".

So while a job is running, or a phone or tablet has talked to it in the last
two minutes, the program tells Windows the system is in use. The screen may
still turn off; the machine does not sleep. It stops asking the moment
neither is true, so an idle computer sleeps exactly as it always did.
Closing the lid is Windows' own decision and still wins.
"""
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_awake = {"on": None}


def _wanted_awake():
    with _lock:
        if any(not j.done for j in _jobs.values()):
            return True
    now = time.time()
    return any(now - v.get("last", 0) < 120 for v in Handler._seen.values())


def _keep_awake_loop():
    from . import plat
    if not (plat.IS_WIN or plat.IS_MAC):
        return

    def loop():
        k32 = ctypes.windll.kernel32 if plat.IS_WIN else None
        while True:
            want = _wanted_awake()
            # Re-asserted every pass, not only on a change: the request belongs
            # to this thread, and asking again costs nothing.
            if k32 is not None:
                k32.SetThreadExecutionState(
                    _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if want else 0))
            else:
                plat.keep_awake(want)
            if want != _awake["on"]:
                journal.write("awake.hold" if want else "awake.release")
                _awake["on"] = want
            time.sleep(20)

    threading.Thread(target=loop, daemon=True, name="keep-awake").start()


def _note_actor(device, what=""):
    changed()
    journal.write("device.action", device=device, what=what)
    try:
        SESSION.parent.mkdir(parents=True, exist_ok=True)
        _write_whole(SESSION, json.dumps(
            {"device": device, "what": what, "at": time.time()}, indent=2))
    except OSError:
        pass


def _last_actor():
    return _read_state(SESSION)


# Closing wins over presence, for a moment.
#
# A device standing in the darkroom says so on every poll, which is what makes
# "having it open is enough" true. It also means that when you close the
# darkroom on one device, the other one - still sitting there - reopens it a
# second later, and the close never takes. So a close is remembered for long
# enough that every other device has polled at least once, seen that it is
# shut, and stepped out of it themselves. Twenty seconds is the slow poll;
# thirty gives it room.
CLOSE_WINS_FOR = 30.0


def _mark_darkroom(folder, device, mode, closing=False):
    journal.write("darkroom.close" if closing else "darkroom.open",
                  folder=str(folder), device=device, mode=mode)
    try:
        DARKROOM.parent.mkdir(parents=True, exist_ok=True)
        d = {"folder": folder, "device": device, "mode": mode,
             "at": time.time()}
        if closing:
            d["closed_at"] = time.time()
        _write_whole(DARKROOM, json.dumps(d, indent=2))
    except OSError:
        pass


def _recently_closed():
    """True while a deliberate close still outranks anybody's presence."""
    d = _read_state(DARKROOM)
    return time.time() - d.get("closed_at", 0) < CLOSE_WINS_FOR


def _open_darkroom(stale=6 * 3600):
    """Empty when nothing is open, or when what was open is old enough that
    dropping somebody into it on a fresh morning would be a surprise."""
    d = _read_state(DARKROOM)
    if not d.get("folder") or time.time() - d.get("at", 0) > stale:
        return {}
    if not (Path(d["folder"]) / "_cullkit").is_dir():
        return {}          # the drive is unplugged, or the folder has moved
    return d


"""The last thing you were looking at, so a finished piece of work is not lost
just because it finished before you picked up the other device.

Everything else here follows work that is *alive* - a running job, an open run,
an open darkroom. A result is none of those: Sift ends, the counts and the
flagged grid are on screen, and the moment you walk to the other machine there
is nothing to attach to. The result itself is on the disk and always was; what
was missing was any record of which one you were reading.
"""
PLACE = Path.home() / ".cullkit" / "place.json"


def _mark_place(view, folder, cmd, device):
    try:
        PLACE.parent.mkdir(parents=True, exist_ok=True)
        _write_whole(PLACE, json.dumps(
            {"view": view, "folder": folder, "cmd": cmd, "device": device,
             "at": time.time()}, indent=2))
    except OSError:
        pass


def _last_place(stale=6 * 3600):
    d = _read_state(PLACE)
    if not d.get("view") or time.time() - d.get("at", 0) > stale:
        return {}
    folder = d.get("folder") or ""
    # A result whose folder has gone - or whose drive is unplugged - is not
    # somewhere to be taken back to.
    if folder and not Path(folder).is_dir():
        return {}
    return d


"""What the program is doing, in one word, and a way to put it back to nothing.

Four small files remember what was happening - a run, a darkroom, a result you
were reading, and who acted last. They exist so work survives a closed lid and
follows you between devices. The cost of that is that a session can be left
half-open: nothing running, but every screen still behaving as though something
is. A stale run is not dangerous, and it is confusing, which is its own kind of
harm.

So: say plainly which of three states this is in, and offer one button that
puts it back to the fourth. Nothing here touches a photograph, a folder, or
anything you have chosen - only the notes about where you were.
"""
STATE_FILES = ("run.json", "darkroom.json", "place.json", "session.json")
# When everything was last deliberately cleared. A job stopped before that
# moment has already been accounted for - it is exactly what you pressed the
# button about - and must not keep the program looking half-finished
# afterwards, which would make the button appear not to have worked.
_reset_at = 0.0


def _program_state():
    with _lock:
        live = [j for j in _jobs.values() if not j.done]
        stopped = [j for j in _jobs.values()
                   if j.done and j.cancelled and j.started > _reset_at]
    if live and all(j.cancelled for j in live):
        return {"state": "stopping",
                "detail": ", ".join(_job_label(j.argv) for j in live)}
    if live:
        return {"state": "working",
                "detail": ", ".join(_job_label(j.argv) for j in live)}
    run = bool(_read_run().get("active"))
    dark = bool(_open_darkroom().get("folder"))
    if run or dark or stopped:
        bits = []
        if run:
            bits.append("a run is open")
        if dark:
            bits.append("a darkroom is open")
        if stopped and not (run or dark):
            bits.append("the last job was stopped")
        return {"state": "interrupted", "detail": " and ".join(bits)}
    return {"state": "free", "detail": "nothing is running"}


def _read_run():
    return _read_state(Path.home() / ".cullkit" / "run.json")


def _reset_everything():
    """Forget where you were. Never forget what you did.

    The second half of that sentence is now literally true: the reset clears
    the pointers, and the journal keeps the account of everything that led up
    to it. Reaching for the hard reset is itself worth knowing about - it means
    something got stuck.
    """
    journal.write("reset.start", state=_program_state())
    global _reset_at
    _reset_at = time.time()
    cleared = []
    for name in STATE_FILES:
        f = Path.home() / ".cullkit" / name
        try:
            if f.is_file():
                f.unlink()
                cleared.append(name)
        except OSError:
            pass
    with _lock:
        for job in _jobs.values():
            if not job.done:
                job.cancel()
    # "Last: Sifting finished, 261 keep" is a note about where you were, and
    # this is the button that says forget where I was. The journal still has
    # it; Overview should not still be showing it.
    with _state_lock:
        LAST_DONE.clear()
        LATEST.update({"title": "Agape Aftershot", "body": "Nothing running.",
                       "at": time.time(), "done": True, "pct": None})
    # The job records are deliberately NOT thrown away. A page somewhere is
    # very likely still reading one of them, and a reader that suddenly gets
    # "no such job" has no way to tell that from a hiccup - it would poll a
    # dead id for as long as the tab stayed open. Left in place, each one
    # reports itself stopped on the next poll and every screen tidies up.
    journal.write("reset.done", cleared=cleared)
    return cleared


def _access_pin():
    """A short code, made once and kept, that other devices must type in."""
    from . import ledger as L

    cfg = L.load_config()
    pin = str(cfg.get("access_pin") or "")
    if not pin:
        pin = "".join(secrets.choice("0123456789") for _ in range(6))
        cfg["access_pin"] = pin
        L.save_config(cfg)
    return pin


def _new_access_pin():
    """Change the code, which is how every other device is asked again.

    A device that has typed the code is given a cookie holding it, good for a
    month, and that cookie is what stops it being asked twice a day. Clearing
    a browser's *cache* does not touch cookies - and an app added to the home
    screen usually keeps its own jar, untouched by anything done in the
    browser beside it - so "I wiped the phone and it still let me in" is the
    system working, not failing.

    What was missing was any way to undo it. There is one now, and it is the
    simplest one that cannot be got wrong: change the code. Every cookie
    holding the old one stops matching on the next request, everywhere at
    once, with nothing to keep track of. This computer is let in by being this
    computer, so it cannot lock you out of the machine with the photographs.
    """
    from . import ledger as L

    cfg = L.load_config()
    old = str(cfg.get("access_pin") or "")
    pin = old
    while pin == old:
        pin = "".join(secrets.choice("0123456789") for _ in range(6))
    cfg["access_pin"] = pin
    L.save_config(cfg)
    journal.write("access.pin-changed")     # never the code itself
    return pin


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A request that died, written down rather than printed.

        Almost always a phone or tablet that went to sleep or changed network
        mid-request - ordinary, and not worth a traceback anywhere a person
        looks. Anything else is still recorded in full.
        """
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            journal.write("web.dropped", who=str(client_address[0]),
                          error=type(exc).__name__)
            return
        journal.write("web.error", who=str(client_address[0]),
                      error=f"{type(exc).__name__}: {exc}",
                      traceback=traceback.format_exc()[-3000:])


def serve(port=8765, host="0.0.0.0", open_browser=True):
    httpd = _Server((host, port), Handler)
    _load_control()
    _keep_awake_loop()
    # Read once, here, where waiting costs nothing. Every later refresh happens
    # off the request thread - but the very first request through the tunnel
    # must already know this machine's Tailscale name or it is refused as a
    # rebinding attempt.
    _tailnet_read()
    _tailnet["at"] = time.time()
    pin = _access_pin()
    # A restart is the boundary between two stretches of the record, and it
    # explains a great deal on its own: a job that vanished, a page that got
    # "no such job", a notification that never came. Written down before
    # anything else can happen.
    journal.write("server.start", port=port, host=host,
                  tailnet=tailnet_url() or "", state=_program_state())
    print()
    print("  Agape Aftershot is running.")
    print()
    print(f"      on this PC      http://localhost:{port}")
    for ip in local_addresses():
        print(f"      on your phone   http://{ip}:{port}")
    ts = tailnet_url()
    if ts:
        print(f"      from anywhere   {ts}")
    print()
    print(f"      code to unlock  {pin}")
    print()
    print("  This PC opens straight through. Any other device asks for the code")
    print("  once - which is what keeps a cafe or hotel wifi out of your photos.")
    print()
    print("  Leave this window open. Ctrl-C to stop.")
    print()
    if open_browser:
        import webbrowser
        threading.Timer(0.7, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    else:
        print("  (started without opening a browser)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0
