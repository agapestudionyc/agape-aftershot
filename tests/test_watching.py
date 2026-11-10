"""What the program says about itself while it works, and whether it is true.

Every test here comes from one afternoon: the first time a real card was
imported with the app rather than the command line. Nothing lost a photograph.
What went wrong was everything around the work - the page said a job was
running while Overview said the machine was free, the longest stage of an
import reported no progress at all, a notification said "finished, took 15m"
and not whether it had worked, and a folder full of faces started being
analysed without anybody asking for it.

Those are all the same fault in different clothes: the program knew something
and did not say it, or said something it did not know. So this file asks, of
each one, the only question that matters - would somebody watching this screen
be told the truth?
"""
import os

# Before any import of `cullkit`: the suite runs real jobs, and the app
# journals every one. Without this they are filed in the user's own record,
# next to a real afternoon's work.
os.environ["CULLKIT_NO_JOURNAL"] = "1"

import io
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from cullkit import server

WEB = Path(__file__).resolve().parent.parent / "cullkit" / "web" / "index.html"


def _page():
    return WEB.read_text(encoding="utf-8")


class TestAJobSaysWhatCameOfIt(unittest.TestCase):
    """"Importing finished - took 15m" answers the question nobody asked.

    What you are waiting to hear is whether it worked, how many, and whether
    the card is safe to wipe. Every command already prints that at the end;
    all that was missing was reading it back out.
    """

    def test_a_finished_import_says_the_card_is_safe(self):
        said = server.outcome([
            "  copied 412, already there 0, failed 0   (15.2 GB)",
            "  Every file was checksummed against the card and matched.",
        ])
        self.assertIn("412", said)
        self.assertIn("safe to reformat", said)

    def test_a_failed_file_says_do_not_format(self):
        said = server.outcome(["  copied 400, already there 0, failed 12   (14 GB)"])
        self.assertIn("12 of 412", said)
        self.assertIn("NOT format", said)

    def test_a_sift_says_the_count(self):
        said = server.outcome(["verdict: 261 keep, 6 flagged  (soft 4, blinked 2)"])
        self.assertEqual(said, "261 keep, 6 flagged for your call")

    def test_a_rehearsal_says_it_touched_nothing(self):
        said = server.outcome(["  47 files, 16.6 MB",
                               "  DRY RUN - nothing copied. Add --apply to do it."])
        self.assertIn("nothing was copied", said)

    def test_a_stop_says_where_it_stopped(self):
        said = server.outcome(["  120/900", "  240/900"], cancelled=True)
        self.assertIn("240/900", said)
        self.assertIn("nothing was left half-done", said.lower())

    def test_a_failure_hands_back_the_reason(self):
        said = server.outcome(["  reading the card", "  PermissionError: E: is gone"],
                              rc=1)
        self.assertIn("PermissionError", said)

    def test_it_always_says_something(self):
        # A command with nothing recognisable at the end still has to produce a
        # sentence: an empty notification body is worse than a vague one.
        self.assertTrue(server.outcome([]))
        self.assertTrue(server.outcome(["something nobody wrote a rule for"]))


class TestOnlyRealWorkWakesThePhone(unittest.TestCase):
    """Five knocks in one second is how somebody learns to ignore the sixth.

    Reading the settings, listing the drives and counting the caches are all
    over before a phone could light up. The record keeps them; the phone does
    not hear about them.
    """

    def test_the_long_jobs_are_worth_saying(self):
        for cmd in ("import", "quality", "shoots", "work", "poses", "preview"):
            self.assertTrue(server._worth_saying([cmd, "E:\\x"]), cmd)

    def test_the_instant_ones_are_not(self):
        for cmd in ("status", "config", "browse", "drives", "journal", ""):
            self.assertFalse(server._worth_saying([cmd]), cmd)

    def test_a_quiet_job_wires_up_no_notification(self):
        job = server.Job("j-quiet", ["status"])
        server._watch(job)
        sent = []
        real = server._say
        server._say = lambda *a, **k: sent.append(a)
        try:
            for line in ["settings", "-" * 8, "recent imports", "-" * 8]:
                job.note(line)
        finally:
            server._say = real
        self.assertEqual(sent, [])

    def test_a_real_job_does(self):
        job = server.Job("j-loud", ["quality", "E:\\x"])
        server._watch(job)
        sent = []
        real = server._say
        server._say = lambda *a, **k: sent.append(a)
        try:
            for line in ["faces", "-" * 8]:
                job.note(line)
        finally:
            server._say = real
        self.assertEqual(len(sent), 1)
        self.assertIn("faces", sent[0][1])


class TestAStageIsNamedProperly(unittest.TestCase):
    """A Windows path has a colon two characters in.

    Cutting a stage name at the first colon of any kind turned
    "reading C:\\Users\\..." into a stage called "reading C", and that is the
    name that went to the phone.
    """

    def test_a_drive_letter_does_not_end_the_name(self):
        self.assertEqual(server._phase_name("reading C:\\Users\\me\\DCIM"),
                         "reading C:\\Users\\me\\DCIM")

    def test_a_real_label_still_ends_it(self):
        self.assertEqual(server._phase_name("verdict: 261 keep, 6 flagged"),
                         "verdict")
        self.assertEqual(server._phase_name("previews: 47 frames -> C:\\x\\y"),
                         "previews")

    def test_a_bare_label_keeps_its_name(self):
        self.assertEqual(server._phase_name("Second:"), "Second")

    def test_nothing_runs_off_the_end_of_a_notification(self):
        self.assertLessEqual(len(server._phase_name("reading " + "x" * 300)), 70)


class TestEveryThreadReportsIntoItsJob(unittest.TestCase):
    """The longest stage of the work was reporting to nobody.

    Previews are extracted on a pool of eight threads. The router sent output
    to a thread-local tee, none of those threads had one, and so every line
    they printed went to the console instead of the job. Two things followed,
    and both were reported as "it looked like it had stopped": no progress at
    all through the longest part of a card, and a stop that did not land -
    cancelling is noticed inside the very write that was going elsewhere.
    """

    def test_an_unbound_thread_finds_the_only_job_in_the_house(self):
        fallback = io.StringIO()
        router = server._Router(fallback)
        job = server.Job("j-pool", ["quality"])
        tee = server._Tee(job)
        router.bind(tee)
        try:
            done = threading.Event()

            def worker():                     # a preview thread, in miniature
                router.write("  25/47\n")
                done.set()

            t = threading.Thread(target=worker)
            t.start()
            t.join(5)
            self.assertTrue(done.is_set())
        finally:
            router.unbind()
        self.assertIn("  25/47", job.lines)
        self.assertEqual(fallback.getvalue(), "")
        self.assertEqual(job.progress()["cur"], 25)

    def test_a_worker_thread_learns_the_job_was_stopped(self):
        router = server._Router(io.StringIO())
        job = server.Job("j-stop", ["quality"])
        job.cancelled = True
        router.bind(server._Tee(job))
        try:
            box = {}

            def worker():
                try:
                    router.write("  26/47\n")
                except server.Cancelled:
                    box["stopped"] = True

            t = threading.Thread(target=worker)
            t.start()
            t.join(5)
        finally:
            router.unbind()
        self.assertTrue(box.get("stopped"))

    def test_two_jobs_at_once_never_tear_each_other_s_log(self):
        # The reason the thread-local was there in the first place: two jobs
        # collecting at once, and an unbound thread with no honest owner. Its
        # output goes to the console rather than into somebody else's log.
        fallback = io.StringIO()
        router = server._Router(fallback)
        a, b = server.Job("j-a", ["quality"]), server.Job("j-b", ["shoots"])
        ta, tb = server._Tee(a), server._Tee(b)
        # Bound from two different threads, the way real jobs are.
        for tee in (ta, tb):
            t = threading.Thread(target=lambda x=tee: router.bind(x))
            t.start()
            t.join(5)
        try:
            router.write("  175/757\n")
        finally:
            router.live.clear()
        self.assertEqual(a.lines, [])
        self.assertEqual(b.lines, [])
        self.assertIn("175/757", fallback.getvalue())

    def test_the_preview_pass_prints_from_the_pool_and_is_still_counted(self):
        """End to end, with real files: the count reaches the job."""
        from tests.make_fixtures import make_raw

        root = Path(tempfile.mkdtemp(prefix="cullkit-test-watch-"))
        try:
            for n in range(1200, 1206):
                make_raw(root / f"DSC_{n}.NEF", n, sensor_kb=40)
            job = server.Job("j-prev", ["preview", str(root)])
            router = server._install_router()
            tee = server._Tee(job)
            router.bind(tee)
            try:
                from cullkit import previews
                seen = []
                previews.extract_many(
                    sorted(root.glob("*.NEF")), root / "_cullkit" / "previews",
                    long_side=512, workers=4,
                    on_done=lambda r: print(f"  {len(seen) + 1}/6")
                    or seen.append(r))
            finally:
                tee.flush()
                router.unbind()
            self.assertTrue([ln for ln in job.lines if "/6" in ln],
                            f"nothing counted: {job.lines}")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestThePageCannotInsistOnItsOwnStory(unittest.TestCase):
    """The bug that started all of this, guarded where it lived.

    A single failed poll ended the watcher loop with no `catch` around it. The
    busy counter never came down, so the page went on saying "running" for as
    long as it stayed open, while Overview - which asks the machine - said
    Free. Read as text, because there is no JavaScript here and the
    alternative is trusting that the next person remembers.
    """

    def test_the_job_poll_is_wrapped(self):
        page = _page()
        i = page.index("const tick = async () => {")
        block = page[i:i + 1400]
        self.assertIn("try {", block)
        self.assertIn("catch", block)
        self.assertIn("/api/job?id=", block)

    def test_asking_for_a_job_is_wrapped_too(self):
        page = _page()
        i = page.index("'/api/run', {method: 'POST'")
        self.assertIn("catch", page[i - 400:i + 900])

    def test_a_watcher_that_stopped_watching_is_taken_down(self):
        page = _page()
        self.assertIn("function reapDeadWatchers()", page)
        # ...but never one the machine still lists as running, and never from
        # a backgrounded tab, whose timers the browser throttles to a crawl.
        i = page.index("function reapDeadWatchers()")
        block = page[i:i + 900]
        self.assertIn("document.hidden", block)
        self.assertIn("live.has(c.id)", block)

    def test_the_reaper_runs_on_every_poll(self):
        page = _page()
        i = page.index("STATE = await r.json();")
        self.assertIn("reapDeadWatchers()", page[i:i + 1800])

    def test_overview_refreshes_when_you_walk_into_it(self):
        page = _page()
        i = page.index("function go(view) {")
        self.assertIn("if (view === 'home') loadState();", page[i:i + 3000])

    def test_the_poll_tightens_while_work_is_running(self):
        page = _page()
        i = page.index("function statePoll()")
        self.assertIn("STATE.running", page[i:i + 700])


class TestNothingLongStartsUnasked(unittest.TestCase):
    """Skipping a step, dropping a run, and setting a shoot analysing are all
    things you should have to mean."""

    def test_skip_asks_first(self):
        page = _page()
        i = page.index("$('#flow-skip').onclick")
        self.assertIn("confirm(skipQuestion())", page[i:i + 300])

    def test_skip_says_which_step_it_is_skipping(self):
        page = _page()
        i = page.index("function skipQuestion()")
        block = page[i:i + 1800]
        for said in ("Skip the import?", "Skip separating the projects?",
                     "Skip sifting", "Skip developing"):
            self.assertIn(said, block)

    def test_the_darkroom_asks_before_a_long_pass(self):
        page = _page()
        self.assertIn("function mayPrepare(", page)
        i = page.index("async function openDoor(")
        block = page[i:i + 2600]
        self.assertIn("mayPrepare(", block)
        # Both of them: the previews, and the pass that reads faces and poses.
        self.assertGreaterEqual(block.count("mayPrepare("), 2)

    def test_a_door_that_opened_itself_starts_nothing(self):
        page = _page()
        i = page.index("function mayPrepare(")
        self.assertIn("if (auto)", page[i:i + 700])
        self.assertIn("openDoor(sel.mode, {gi: sel.gi || 0, i: sel.i || 0}, true)",
                      page)

    def test_walking_out_of_a_run_that_never_started_forgets_its_answers(self):
        page = _page()
        self.assertIn("function forgetImportChoice()", page)
        i = page.index("function flowQuietEnd()")
        self.assertIn("forgetImportChoice()", page[i:i + 300])
        # ...and asks first, when there is an answer to lose.
        j = page.index("function mayDropIdleRun()")
        self.assertIn("importChoiceMade()", page[j:j + 600])

    def test_a_glance_at_overview_ends_nothing(self):
        page = _page()
        i = page.index("document.querySelectorAll('nav button').forEach(b =>")
        block = page[i:i + 1200]
        # Entering Overview returns before any of the teardown paths.
        self.assertIn("return bubbleOpenHome(b);", block)


class TestTheLastThingThatHappenedIsKept(unittest.TestCase):
    """A job that ended while the app was shut left no trace on any screen."""

    def test_state_carries_the_last_finished_job(self):
        src = Path(server.__file__).read_text(encoding="utf-8")
        i = src.index("    def _state(self):")
        self.assertIn('"last_done": dict(LAST_DONE)', src[i:i + 4000])

    def test_the_page_reads_it(self):
        page = _page()
        self.assertIn("STATE.last_done", page)
        self.assertIn('id="prog-last"', page)

    def test_a_clean_start_forgets_it(self):
        server.LAST_DONE.update({"what": "Sifting", "result": "x"})
        src = server._reset_everything.__code__.co_consts
        # The reset must actually mention it; a note left behind after "clear
        # and start fresh" is the same confusion in a smaller box.
        server.LAST_DONE.clear()
        page = Path(server.__file__).read_text(encoding="utf-8")
        i = page.index("def _reset_everything()")
        self.assertIn("LAST_DONE.clear()", page[i:i + 2200])


class TestOpeningFromNothingOpensFromNothing(unittest.TestCase):
    """Closing the app and opening it again landed him inside a Sift of a
    folder from the night before, result on screen, nothing asked.

    The rule that was missing: the app follows work that is *happening*, and
    only offers work that has already happened. A running job is moving and you
    want to see it move. An open darkroom and a finished result are notes about
    yesterday, and being taken to a note is not continuity, it is being moved.
    """

    def test_the_offer_exists_on_the_front_page(self):
        page = _page()
        self.assertIn('id="resume-card"', page)
        self.assertIn('id="resume-go"', page)
        self.assertIn('id="resume-no"', page)

    def test_nothing_navigates_on_its_own_any_more(self):
        page = _page()
        # The two that used to walk you somewhere are gone by name; one
        # function offers instead, and it is what the poll calls.
        self.assertNotIn("async function resumeLastPlace(", page)
        self.assertNotIn("async function resumeDarkroom(", page)
        self.assertIn("async function offerToResume(", page)
        i = page.index("STATE = await r.json();")
        self.assertIn("offerToResume()", page[i:i + 1800])

    def test_live_work_still_follows_you(self):
        # The one thing that must NOT become an offer. Adoption is what makes
        # a job started on the phone appear on the laptop, and it stays.
        page = _page()
        i = page.index("STATE = await r.json();")
        self.assertIn("adoptRunningWork()", page[i:i + 1800])

    def test_the_offer_stands_down_while_something_is_running(self):
        page = _page()
        i = page.index("async function offerToResume()")
        block = page[i:i + 700]
        self.assertIn("STATE.running", block)
        self.assertIn("BUSY > 0", block)

    def test_saying_no_forgets_the_note(self):
        page = _page()
        i = page.index("$('#resume-no').onclick")
        self.assertIn("offer.forget()", page[i:i + 500])

    def test_the_run_bar_stops_contradicting_the_screen(self):
        # "pick a folder and run it" was printed directly above a folder, a
        # result and three counts, because nothing had run in *this* session.
        page = _page()
        i = page.index("async function offerToResume()")
        self.assertIn("LAST_RUN = {argv: [p.cmd, p.folder, '--sheet']",
                      page[i:i + 3000])


class TestTheNotificationTheBrowserAllows(unittest.TestCase):
    """Android refuses `new Notification()` outright - "Illegal constructor.
    Use ServiceWorkerRegistration.showNotification() instead" - which the
    phone recorded the first evening every job started announcing itself. It
    threw, and the notification never appeared at all."""

    def test_it_goes_through_the_service_worker_where_there_is_one(self):
        page = _page()
        i = page.index("async function notify(")
        block = page[i:i + 1200]
        self.assertIn("serviceWorker.getRegistration()", block)
        self.assertIn("reg.showNotification(title, opts)", block)
        # ...and the constructor is only the fallback, after that.
        self.assertLess(block.index("reg.showNotification"),
                        block.index("new Notification(title, opts)"))

    def test_it_can_never_take_a_job_down_with_it(self):
        page = _page()
        i = page.index("async function notify(")
        self.assertIn("catch", page[i:i + 1200])


class TestAskingEveryDeviceAgain(unittest.TestCase):
    """Clearing a phone's cache does not forget the code - it lives in a
    cookie, and an installed app keeps its own besides. Changing the code is
    the only thing that asks everybody again, and there was no way to do it."""

    def test_the_code_really_changes(self):
        from cullkit import ledger as L

        cfg = L.load_config()
        keep = cfg.get("access_pin")
        try:
            cfg["access_pin"] = "111111"
            L.save_config(cfg)
            first = server._new_access_pin()
            self.assertNotEqual(first, "111111")
            self.assertEqual(len(first), 6)
            self.assertTrue(first.isdigit())
            # Twice in a row must not land on the same code either.
            self.assertNotEqual(server._new_access_pin(), first)
        finally:
            cfg = L.load_config()
            if keep is None:
                cfg.pop("access_pin", None)
            else:
                cfg["access_pin"] = keep
            L.save_config(cfg)

    def test_only_the_computer_can_change_it(self):
        src = Path(server.__file__).read_text(encoding="utf-8")
        i = src.index('if path == "/api/pin/new":')
        block = src[i:i + 500]
        self.assertIn("_is_local(self._who())", block)
        self.assertIn("403", block)

    def test_the_code_itself_is_never_written_down(self):
        src = Path(server.__file__).read_text(encoding="utf-8")
        i = src.index("def _new_access_pin()")
        block = src[i:i + 1600]
        self.assertIn('journal.write("access.pin-changed")', block)
        self.assertNotIn("pin=pin", block)


if __name__ == "__main__":
    unittest.main()
