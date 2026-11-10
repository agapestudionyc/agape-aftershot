"""Stopping that stops, and one session moving between screens.

From the morning of 25 October: a Separate Projects stopped at 02:25 ended at
02:37, every refresh in between took him straight back into it, Clear and
start fresh said it was done while the job was still working, and the step on
every screen read "ConnectionResetError" for hours. Alongside it, the phone and
the computer each lived in their own world - the same phone on three networks
was three devices, and the curtain arrived up to twenty seconds late.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import tempfile
import threading
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

WEB = Path(__file__).resolve().parent.parent / "cullkit" / "web" / "index.html"


def _page():
    return WEB.read_text(encoding="utf-8")


class _Jobs(unittest.TestCase):
    def setUp(self):
        from cullkit import server
        with server._lock:
            self._saved = dict(server._jobs)
            server._jobs.clear()

    def tearDown(self):
        from cullkit import server
        with server._lock:
            for j in server._jobs.values():
                j.cancel()
        for _ in range(100):
            with server._lock:
                if all(j.done for j in server._jobs.values()):
                    break
            time.sleep(0.02)
        with server._lock:
            server._jobs.clear()
            server._jobs.update(self._saved)


class TestAStopLandsBetweenFilesInEveryStage(_Jobs):
    """A stage that prints once a group used to hear "stop" once a group."""

    def test_a_silent_loop_with_checkpoints_stops_within_a_second(self):
        from cullkit import server
        from cullkit.stop import checkpoint

        def quiet(argv):
            print("grouping by who is in each of 41")
            print("-" * 40)
            for _ in range(10_000):          # minutes of work, never printing
                checkpoint()
                time.sleep(0.01)
            return 0

        with mock.patch("cullkit.cli.main", quiet):
            job, _ = server.start_job(["shoots", "X:\\nowhere"])
            time.sleep(0.2)
            job.cancel()
            t0 = time.time()
            while not job.done and time.time() - t0 < 3:
                time.sleep(0.02)
        self.assertTrue(job.done, "the stop never landed")
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(job.rc, 130)

    def test_pool_workers_hear_it_too(self):
        """Previews are pulled on eight threads that are not the job's own."""
        from cullkit import previews, server

        seen = []

        def slow(src, out, **kw):
            seen.append(src)
            time.sleep(0.05)
            return mock.Mock(ok=True, out=out, source=src)

        def many(argv):
            with mock.patch.object(previews, "extract", slow):
                previews.extract_many([f"f{i}.nef" for i in range(2000)],
                                      tempfile.gettempdir(), workers=4)
            return 0

        with mock.patch("cullkit.cli.main", many):
            job, _ = server.start_job(["preview", "X:\\nowhere"])
            time.sleep(0.3)
            job.cancel()
            t0 = time.time()
            while not job.done and time.time() - t0 < 5:
                time.sleep(0.02)
        self.assertTrue(job.done)
        self.assertLess(len(seen), 2000, "every queued file ran anyway")

    def test_a_job_stuck_past_the_stop_is_let_go(self):
        """An hour and a half at "stopping", the whole app behind the curtain:
        a thread blocked inside something that never returns hears nothing."""
        from cullkit import server

        gate = threading.Event()

        def stuck(argv):
            print("grouping by who is in each of 41")
            print("-" * 40)
            gate.wait(10)                    # blocked, never printing
            return 0

        with mock.patch.object(server, "ABANDON_AFTER", 0.3), \
                mock.patch("cullkit.cli.main", stuck):
            job, _ = server.start_job(["shoots", "X:\\nowhere"])
            time.sleep(0.2)
            job.cancel()
            time.sleep(0.8)
            self.assertTrue(job.done, "still holding the app hostage")
            self.assertTrue(job.abandoned)
            self.assertNotEqual(server._program_state()["state"], "stopping")
            self.assertIn("let go", server.LAST_DONE.get("result", ""))
            # ...and the next task is not refused because of it.
            nxt, other = server.start_job(["status"])
            self.assertIsNone(other)
            gate.set()
            for _ in range(100):
                if nxt.done:
                    break
                time.sleep(0.02)

    def test_checkpoint_is_harmless_outside_a_job(self):
        from cullkit.stop import checkpoint

        checkpoint()        # the command line: nothing to stop, nothing happens


class TestStoppingIsAStateEveryScreenCanSee(_Jobs):

    def test_progress_says_stopping_and_for_how_long(self):
        from cullkit import server

        j = server.Job("j-s", ["shoots"])
        self.assertFalse(j.progress()["stopping"])
        j.cancel()
        p = j.progress()
        self.assertTrue(p["stopping"])
        self.assertGreaterEqual(p["stopping_for"], 0)

    def test_the_program_state_is_stopping_not_working(self):
        from cullkit import server

        j = server.Job("j-s", ["shoots"])
        with server._lock:
            server._jobs["j-s"] = j
        self.assertEqual(server._program_state()["state"], "working")
        j.cancel()
        self.assertEqual(server._program_state()["state"], "stopping")
        j.done = True

    def test_the_page_never_picks_up_a_job_on_its_way_out(self):
        page = _page()
        i = page.index("async function adoptRunningWork()")
        self.assertIn("!j.stopping", page[i:i + 1600])

    def test_the_curtain_follows_the_computer_not_only_the_button(self):
        page = _page()
        i = page.index("function paintStopping()")
        block = page[i:i + 600]
        self.assertIn("j.stopping", block)
        self.assertIn("LOCAL_STOP ||", block)
        j = page.index("async function loadState()")
        self.assertIn("paintStopping();", page[j:j + 4000])

    def test_the_wait_no_longer_gives_up_at_twelve_seconds(self):
        self.assertNotIn("timeout = 12000", _page())


class TestForceStopDoesNotWait(_Jobs):
    """"I tried to stop it and it kept going" - a button that does not wait."""

    def test_a_long_silent_step_is_interrupted_at_once(self):
        """No print and no checkpoint for a long time: the polite stop would
        wait it out. A forced one lands at the next line of Python."""
        from cullkit import server

        steps = []

        def silent(argv):
            print("grouping by who is in each of 41")
            print("-" * 40)
            for i in range(10_000):          # minutes of work, never printing
                steps.append(i)
                time.sleep(0.01)
            return 0

        with mock.patch("cullkit.cli.main", silent):
            job, _ = server.start_job(["shoots", "X:\\nowhere"])
            time.sleep(0.2)
            job.cancel()
            time.sleep(0.3)
            self.assertFalse(job.done, "the test needs a stop that is ignored")
            t0 = time.time()
            job.force()
            while not job.done and time.time() - t0 < 3:
                time.sleep(0.02)
        self.assertTrue(job.done, "force stop never landed")
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(job.rc, 130)
        self.assertFalse(job.abandoned, "it ended itself, it was not let go")
        self.assertIn("Force-stopped", "\n".join(job.lines))
        n = len(steps)
        time.sleep(0.1)
        self.assertEqual(len(steps), n, "still working after the force stop")

    def test_a_thread_that_never_returns_frees_the_app_in_seconds(self):
        from cullkit import server

        gate = threading.Event()

        def stuck(argv):
            print("grouping by who is in each of 41")
            print("-" * 40)
            gate.wait(10)                    # inside C, never back to Python
            return 0

        with mock.patch.object(server, "FORCE_GRACE", 0.3), \
                mock.patch("cullkit.cli.main", stuck):
            job, _ = server.start_job(["shoots", "X:\\nowhere"])
            time.sleep(0.2)
            job.force()
            time.sleep(0.8)
            self.assertTrue(job.done)
            self.assertTrue(job.abandoned)
            self.assertIn("force-stopped", server.LAST_DONE.get("result", ""))
            nxt, other = server.start_job(["status"])
            self.assertIsNone(other)
            gate.set()
            for _ in range(100):
                if nxt.done:
                    break
                time.sleep(0.02)

    def test_a_job_that_finishes_on_its_own_is_not_hit_late(self):
        """The forced stop is disarmed before the job tidies up."""
        from cullkit import server

        with mock.patch("cullkit.cli.main", lambda argv: 0):
            job, _ = server.start_job(["status"])
            for _ in range(100):
                if job.done:
                    break
                time.sleep(0.02)
        self.assertEqual(job.rc, 0)
        self.assertEqual(job._ident, 0)
        job.force()                          # too late: does nothing
        self.assertEqual(job.rc, 0)

    def test_the_curtain_has_the_button(self):
        page = _page()
        i = page.index('<div id="stopping">')
        self.assertIn('id="stop-force"', page[i:i + 800])
        j = page.index("async function forceStop()")
        self.assertIn("force: true", page[j:j + 800])


class TestAWebRequestNeverWritesIntoAJob(_Jobs):
    """The traceback of a dropped phone connection became the job's step."""

    def test_a_request_threads_output_goes_to_the_console(self):
        from cullkit import server

        router = server._install_router()
        job = server.Job("j-r", ["shoots"])
        tee = server._Tee(job)
        # The live job, as the pool sees it: bound in another thread.
        bound = threading.Event()
        release = threading.Event()

        def owner():
            router.bind(tee)
            bound.set()
            release.wait(2)
            router.unbind()

        threading.Thread(target=owner, daemon=True).start()
        bound.wait(2)
        try:
            def web():
                server._web_thread.on = True
                self.assertIs(router._target(), router.fallback)

            def pool():
                self.assertIs(router._target(), tee)

            for fn in (web, pool):
                t = threading.Thread(target=fn)
                t.start()
                t.join()
        finally:
            release.set()

    def test_a_dropped_connection_is_written_down_not_printed(self):
        from cullkit import server

        srv = object.__new__(server._Server)
        with mock.patch.object(server.journal, "write") as w:
            try:
                raise ConnectionResetError("forcibly closed")
            except ConnectionResetError:
                srv.handle_error(None, ("100.1.2.3", 5000))
        self.assertEqual(w.call_args[0][0], "web.dropped")


class TestOneDeviceIsOneDevice(unittest.TestCase):
    """The same phone on wifi, Tailscale and mobile data was three devices."""

    def _asker(self, ip, cookie=""):
        from cullkit import server

        h = object.__new__(server.Handler)
        h.headers = Message()
        if cookie:
            h.headers["Cookie"] = cookie
        h._who = lambda: ip
        return h

    def test_the_cookie_wins_over_the_address(self):
        a = self._asker("192.168.1.20", "aftershot=1; aftershot_dev=dabc123xyz")
        b = self._asker("100.64.0.7", "aftershot_dev=dabc123xyz; x=1")
        self.assertEqual(a._key(), b._key())
        self.assertEqual(a._key(), "dev:dabc123xyz")

    def test_an_old_page_is_still_known_by_its_address(self):
        self.assertEqual(self._asker("192.168.1.20")._key(), "192.168.1.20")

    def test_nonsense_in_the_cookie_is_ignored(self):
        k = self._asker("192.168.1.20", "aftershot_dev=<script>")._key()
        self.assertEqual(k, "192.168.1.20")

    def test_the_computer_is_the_computer(self):
        self.assertEqual(self._asker("127.0.0.1", "aftershot_dev=dabc123")._key(),
                         "127.0.0.1")

    def test_the_page_sets_the_cookie(self):
        self.assertIn("document.cookie = 'aftershot_dev=' + DEVICE_ID", _page())


class TestTheControlsSurviveARestart(unittest.TestCase):

    def setUp(self):
        from cullkit import server

        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(server, "CONTROL",
                                       Path(self.tmp.name) / "control.json")
        self.patch.start()
        self.keep = (dict(server.Handler._control), dict(server.Handler._seen))

    def tearDown(self):
        from cullkit import server

        self.patch.stop()
        self.tmp.cleanup()
        server.Handler._control, server.Handler._seen = self.keep

    def test_the_holder_is_back_and_given_time_to_return(self):
        from cullkit import server

        server._save_control({"holder": "dev:dphone1", "device": "Android phone",
                              "at": time.time()})
        server.Handler._control = {"holder": "local", "device": "This computer",
                                   "at": 0.0}
        server.Handler._seen = {}
        server._load_control()
        self.assertEqual(server.Handler._control["holder"], "dev:dphone1")
        h = object.__new__(server.Handler)
        h._reclaim_if_gone()                    # not silent yet: kept
        self.assertEqual(server.Handler._control["holder"], "dev:dphone1")


class TestEveryScreenHearsAtOnce(unittest.TestCase):

    def test_a_change_wakes_a_waiter_immediately(self):
        from cullkit import server

        v = server._version()
        got = {}

        def wait():
            t0 = time.time()
            got["v"] = server.wait_for_change(v, timeout=5)
            got["took"] = time.time() - t0

        t = threading.Thread(target=wait)
        t.start()
        time.sleep(0.1)
        server.changed()
        t.join(3)
        self.assertEqual(got["v"], v + 1)
        self.assertLess(got["took"], 1.0)

    def test_a_stale_waiter_returns_straight_away(self):
        from cullkit import server

        t0 = time.time()
        server.wait_for_change(-1, timeout=5)
        self.assertLess(time.time() - t0, 0.5)

    def test_cancelling_a_job_counts_as_a_change(self):
        from cullkit import server

        v = server._version()
        server.Job("j-c", ["shoots"]).cancel()
        self.assertGreater(server._version(), v)

    def test_the_page_holds_a_wait_open(self):
        page = _page()
        self.assertIn("fetch('/api/wait?v=' + v", page)


class TestPickingADeviceUpTakesIt(unittest.TestCase):

    def test_coming_back_to_the_page_takes_the_controls(self):
        page = _page()
        i = page.index("async function pickedUp(how)")
        self.assertIn("if (!HELD) takeHere(how);", page[i:i + 200])
        self.assertIn("document.addEventListener('visibilitychange', () => pickedUp(",
                      page)

    def test_taking_over_opens_where_the_other_device_was(self):
        page = _page()
        i = page.index("async function takeHere(how)")
        self.assertIn("followHandover(true)", page[i:i + 900])

    def test_a_cold_open_still_offers_rather_than_goes(self):
        page = _page()
        self.assertIn("s.seconds_ago > HANDOVER_FRESH", page)

    def test_the_curtain_title_is_its_own(self):
        """A bare '.tk-t' is the offline curtain's title, first in the page."""
        page = _page()
        self.assertNotIn("$('.tk-t').textContent", page)
        self.assertIn("$('#takeover .tk-t').textContent", page)


class TestTheComputerStaysAwakeWhileUsed(_Jobs):

    def test_a_running_job_keeps_it_awake(self):
        from cullkit import server

        keep = dict(server.Handler._seen)
        server.Handler._seen = {}
        try:
            self.assertFalse(server._wanted_awake())
            j = server.Job("j-a", ["shoots"])
            with server._lock:
                server._jobs["j-a"] = j
            self.assertTrue(server._wanted_awake())
            j.done = True
            server.Handler._seen = {"dev:x": {"last": time.time()}}
            self.assertTrue(server._wanted_awake())
            server.Handler._seen = {"dev:x": {"last": time.time() - 600}}
            self.assertFalse(server._wanted_awake())
        finally:
            server.Handler._seen = keep


if __name__ == "__main__":
    unittest.main()
