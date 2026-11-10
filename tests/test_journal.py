"""The program's account of itself.

An audit trail is only worth having if it holds under the conditions that
produce the faults you want to audit: several threads at once, a value that
will not serialise, a disk that says no, a file that has grown large. Every
test here is one of those conditions.

Each one runs against a journal in a temporary directory. The suite must never
write into the real one — that is a record of a real person's afternoons, and
filing fixture noise in it would make the first real audit useless.
"""
import os

# Before any import of `cullkit`: the suite runs real jobs, and the app now
# journals every one. Without this they are filed in the user's own record,
# next to a real afternoon's work. Set here rather than only in the package
# `__init__`, which `unittest discover` does not import.
os.environ["CULLKIT_NO_JOURNAL"] = "1"

import json
import tempfile
import threading
import unittest
from pathlib import Path

from cullkit import journal


class JournalCase(unittest.TestCase):
    """A journal of its own, thrown away afterwards."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cullkit-journal-")
        self._home, self._off = journal.HOME, journal._off
        self._env = os.environ.get("CULLKIT_NO_JOURNAL")
        journal.HOME = Path(self.tmp)
        # The rest of the suite runs with recording switched off so it cannot
        # write into the real journal. These tests are the exception: they are
        # about the writing itself, and they write into a temporary directory.
        journal._off = False
        os.environ.pop("CULLKIT_NO_JOURNAL", None)

    def tearDown(self):
        journal.HOME, journal._off = self._home, self._off
        if self._env is None:
            os.environ.pop("CULLKIT_NO_JOURNAL", None)
        else:
            os.environ["CULLKIT_NO_JOURNAL"] = self._env

    def lines(self):
        p = Path(self.tmp) / journal.NAME
        if not p.exists():
            return []
        return [json.loads(x) for x in
                p.read_text(encoding="utf-8").splitlines() if x.strip()]


class TestItWritesWhatHappened(JournalCase):

    def test_one_line_per_event(self):
        journal.write("job.start", what="Sifting", argv=["quality", "x"])
        journal.write("job.finish", what="Sifting", rc=0, secs=1.5)
        rows = self.lines()
        self.assertEqual([r["kind"] for r in rows], ["job.start", "job.finish"])
        self.assertEqual(rows[1]["rc"], 0)
        self.assertIn("t", rows[0])
        self.assertIn("ts", rows[0])

    def test_it_is_json_lines_a_person_can_grep(self):
        """The format is part of the promise: one event per line, so `findstr
        error` works when nothing else does."""
        journal.write("job.error", what="Sifting", error="Boom: nope")
        raw = (Path(self.tmp) / journal.NAME).read_text(encoding="utf-8")
        self.assertEqual(raw.count("\n"), 1)
        self.assertIn("Boom: nope", raw)

    def test_nothing_is_dropped_for_being_awkward(self):
        """A value that will not serialise is described rather than thrown
        away. A truncated fact beats a missing line."""
        journal.write("odd", thing=object(), fine="yes")
        row = self.lines()[0]
        self.assertEqual(row["fine"], "yes")
        self.assertIn("object", row["thing"])

    def test_empty_fields_are_left_out_not_written_as_null(self):
        journal.write("job.start", what="Sifting", device=None)
        self.assertNotIn("device", self.lines()[0])


class TestItNeverBreaksAnything(JournalCase):
    """A witness, never a participant.

    Every one of these would have been an exception raised inside a job that
    was moving photographs. The journal failing must cost a line in the record
    and nothing else.
    """

    def test_a_directory_it_cannot_write_to(self):
        journal.HOME = Path(self.tmp) / "nope.txt"
        journal.HOME.write_text("I am a file, not a folder", encoding="utf-8")
        journal.write("job.start", what="Sifting")      # must not raise

    def test_a_kind_that_is_not_a_string(self):
        journal.write(None, what="Sifting")
        journal.write(12345, what="Sifting")
        self.assertEqual(len(self.lines()), 2)

    def test_reading_a_corrupted_line_reports_it_rather_than_stopping(self):
        journal.write("job.start", what="Sifting")
        with (Path(self.tmp) / journal.NAME).open("a", encoding="utf-8") as fh:
            fh.write("{this is not json\n")
        journal.write("job.finish", what="Sifting", rc=0)
        kinds = [r.get("kind") for r in journal.read()]
        self.assertEqual(kinds, ["job.start", "journal.unreadable", "job.finish"])

    def test_the_switch_turns_it_off_completely(self):
        journal._off = True
        journal.write("job.start", what="Sifting")
        self.assertEqual(self.lines(), [])

    def test_the_environment_switch_is_read_on_every_write(self):
        """Cached at import it looked correct and was not: `unittest discover`
        loads test modules without importing the package `__init__`, so the
        line that sets it never ran and a full suite filed sixteen fixtures'
        worth of jobs in the real record."""
        journal.write("job.start", what="before")
        os.environ["CULLKIT_NO_JOURNAL"] = "1"
        journal.write("job.start", what="after")
        self.assertEqual([r["what"] for r in self.lines()], ["before"])

    def test_many_threads_at_once_lose_nothing(self):
        """Jobs run in threads and several can be reporting progress at the
        same moment. A half-written line would corrupt the file for good."""
        def spam(n):
            for i in range(40):
                journal.write("job.phase", what=f"t{n}", phase=str(i))

        threads = [threading.Thread(target=spam, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = self.lines()                 # parses at all == no torn lines
        self.assertEqual(len(rows), 8 * 40)


class TestItNeverForgetsOnItsOwn(JournalCase):

    def test_a_full_file_is_rolled_aside_not_truncated(self):
        journal.MAX_BYTES, keep = 2000, journal.MAX_BYTES
        try:
            for i in range(200):
                journal.write("job.phase", what="Sifting", phase="x" * 40, n=i)
            rolled = sorted(Path(self.tmp).glob("journal-*.jsonl"))
            self.assertTrue(rolled, "nothing was rolled aside")
            everything = journal.read(limit=0)
            self.assertEqual(len(everything), 200,
                             "entries went missing across the roll")
            # ...and in order, oldest first, across the file boundary.
            self.assertEqual([r["n"] for r in everything], list(range(200)))
        finally:
            journal.MAX_BYTES = keep

    def test_only_clear_removes_anything(self):
        for i in range(5):
            journal.write("job.start", what=f"job {i}")
        got = journal.clear()
        self.assertTrue(got["files"])
        # The clearing itself is the first entry of the new record, so a gap is
        # never silent.
        rows = journal.read()
        self.assertEqual([r["kind"] for r in rows], ["journal.cleared"])


class TestReadingItBack(JournalCase):

    def setUp(self):
        super().setUp()
        journal.write("job.start", what="Sifting")
        journal.write("job.finish", what="Sifting", rc=0)
        journal.write("push.sent", body="Sifting finished")
        journal.write("ui.error", error="Cannot read properties of null")

    def test_a_family_is_picked_out_by_prefix(self):
        """`job` has to bring back starts and finishes together, or following
        one job through the record means reading all of it."""
        self.assertEqual([r["kind"] for r in journal.read(kind="job")],
                         ["job.start", "job.finish"])
        self.assertEqual(len(journal.read(kind="push")), 1)

    def test_searching_the_text(self):
        self.assertEqual(len(journal.read(contains="null")), 1)
        self.assertEqual(len(journal.read(contains="nothing like this")), 0)

    def test_the_limit_keeps_the_newest(self):
        last = journal.read(limit=1)
        self.assertEqual(last[0]["kind"], "ui.error")

    def test_the_summary_says_where_it_is_and_when_it_starts(self):
        s = journal.summary()
        self.assertEqual(s["events"], 4)
        self.assertTrue(s["bytes"] > 0)
        self.assertEqual(s["dir"], self.tmp)
        # The *first* entry. Asking `read` for one row gives the newest, which
        # is how this was confidently wrong the first time.
        self.assertEqual(s["oldest"], journal.read(limit=0)[0]["t"])


class TestTheAppActuallyUsesIt(unittest.TestCase):
    """Hooks, checked at the source.

    A journal nobody calls is worse than none: it looks like an audit trail and
    is an empty file. These read the modules rather than running them, because
    the events that matter most are the ones raised by a real crash.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def source(self, name):
        return (self.ROOT / "cullkit" / name).read_text(encoding="utf-8")

    def test_a_job_is_recorded_from_start_to_finish(self):
        s = self.source("server.py")
        for kind in ("job.start", "job.finish", "job.error", "job.refused",
                     "job.phase"):
            self.assertIn(f'journal.write("{kind}"', s, f"nothing records {kind}")

    def test_a_notification_records_whether_it_arrived(self):
        """This is the one that answers "the notification stopped coming".
        Before it, `_say` swallowed every push failure with a bare `pass`."""
        s = self.source("server.py")
        self.assertIn('journal.write("push.sent"', s)
        self.assertIn('journal.write("push.error"', s)
        self.assertNotRegex(
            s, r"from \. import push\n\s+push\.wake_all\(\)\n\s+except Exception:\n\s+pass",
            "push failures are being swallowed again")

    def test_a_request_that_blew_up_leaves_a_trace_on_this_machine(self):
        s = self.source("server.py")
        self.assertIn('journal.write("http.error"', s)

    def test_the_page_reports_its_own_errors(self):
        """Most faults in this app have been in the browser, where nothing on
        the machine could see them."""
        s = (self.ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("addEventListener('error'", s)
        self.assertIn("addEventListener('unhandledrejection'", s)
        self.assertIn("logEvent('ui.error'", s)

    def test_it_can_be_read_and_cleared_from_outside(self):
        s = self.source("server.py")
        self.assertIn('path == "/api/journal"', s)
        self.assertIn('path == "/api/journal/clear"', s)
        self.assertIn("def cmd_journal(args)", self.source("cli.py"))

    def test_the_terminal_knows_what_trouble_looks_like(self):
        """`cull journal --errors` still has to recognise a fault by name -
        `ui.rejected`, a promise nobody caught, among them. The browser used
        to keep its own copy of this same list for an in-page filtered view;
        that view is gone (see the next test for why), so this is the one
        place the classification has to hold up on its own."""
        cli = self.source("cli.py")
        words = {"error", "fail", "refused", "reject", "unreadable"}
        for w in words:
            self.assertIn(f'"{w}"', cli[cli.index("bad = ("):][:120],
                          f"the terminal does not count {w} as trouble")

    def test_the_app_hands_over_everything_unfiltered(self):
        """The in-page log viewer - four buttons, a scrolling list, a
        clipboard copy - was for reading the record here, and here was never
        really where it got read: a fault gets fixed by whoever has the file,
        not by whoever is squinting at a phone screen. So there is one button
        now, and it does not classify or filter anything on its way out -
        that judgment belongs to whoever opens the file, not to this page."""
        page = (self.ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("$('#jr-save').onclick", page)
        self.assertNotIn("only === 'bad'", page)
        i = page.index("$('#jr-save').onclick")
        self.assertIn("/api/journal?limit=5000", page[i:i + 400])

    def test_the_deletion_box_lists_it_before_asking(self):
        """It may not ask you to agree to something it has not shown. That was
        the whole complaint that built this box in the first place."""
        s = self.source("server.py")
        self.assertIn('"journal": journal.summary()', s)
        page = (self.ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="sp-journal"', page)
        self.assertIn("/api/journal/clear", page)

    def test_the_suite_cannot_write_into_the_real_journal(self):
        self.assertEqual(os.environ.get("CULLKIT_NO_JOURNAL"), "1")


if __name__ == "__main__":
    unittest.main()
