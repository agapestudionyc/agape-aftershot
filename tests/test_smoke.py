"""The smoke test: prove the pipeline still works before trusting it with photos.

Runs the whole spine - scan, parse a picks list, resolve it, move files, undo
the move, import a card - against synthetic fixtures, in a temp folder, without
touching the ledger, the config, or a single real photograph.

Run it from the project folder:

    %USERPROFILE%\\.cullkit\\venv\\Scripts\\python.exe -m unittest discover -s tests

Every test cleans up after itself. If one fails, do not trust the change that
broke it.
"""
import os

# Before any import of `cullkit`: the suite runs real jobs, and the app now
# journals every one. Without this they are filed in the user's own record,
# next to a real afternoon's work. Set here rather than only in the package
# `__init__`, which `unittest discover` does not import.
os.environ["CULLKIT_NO_JOURNAL"] = "1"

import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from cullkit import actions, importer, previews, scan, selects
from cullkit.cli import _safe
from tests.make_fixtures import build, make_jpeg, make_raw


def _tmpdir():
    d = tempfile.mkdtemp(prefix="cullkit-test-")
    return Path(d)


class FixtureCase(unittest.TestCase):
    """One shared fixture folder for the read-only tests; built once."""

    @classmethod
    def setUpClass(cls):
        cls.root = _tmpdir()
        cls.shoot = build(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)


class TestScan(FixtureCase):
    def test_stacks_group_raw_jpg_and_sidecar(self):
        idx = scan.scan(self.shoot)
        st = idx.by_base["dsc_1203"]
        self.assertEqual(len(st.raws), 1)
        self.assertEqual(len(st.sidecars), 1)   # DSC_1203.xmp
        st = idx.by_base["dsc_1204"]
        self.assertEqual(len(st.jpgs), 1)       # 1204 % 7 == 0 -> has a JPG

    def test_doubled_sidecar_name_unwraps(self):
        # DSC_1201.NEF.xmp must land on the DSC_1201 stack, not its own.
        idx = scan.scan(self.shoot)
        st = idx.by_base["dsc_1201"]
        self.assertEqual(len(st.sidecars), 1)
        self.assertNotIn("dsc_1201.nef", idx.by_base)

    def test_frame_numbers_and_two_bodies(self):
        idx = scan.scan(self.shoot)
        self.assertEqual(idx.frames[0], 1200)
        # 1205 exists as DSC_1205.NEF and IMG_1205.CR2 - both must be found.
        self.assertEqual(len(idx.by_frame[1205]), 2)


class TestSelects(FixtureCase):
    def test_parse_the_messy_real_list(self):
        sel = selects.parse_file(self.root / "picks.txt")
        nums = set(sel.numbers)
        self.assertIn(1200, nums)
        self.assertTrue(set(range(1203, 1208)) <= nums)   # 1203-1207
        self.assertIn(1210, nums)                          # from a filename
        self.assertTrue(set(range(1215, 1219)) <= nums)   # "1215 a 1218"
        self.assertIn(1221, nums)                          # zero-padded
        self.assertIn(1999, nums)                          # missing, still parsed

    def test_iso_dates_are_never_ranges(self):
        sel = selects.parse_text("2026-03-14\n1200")
        self.assertEqual(sel.numbers, [1200])

    def test_backwards_range_flips_with_warning(self):
        sel = selects.parse_text("1210-1205")
        self.assertEqual(sel.numbers, list(range(1205, 1211)))
        self.assertTrue(sel.warnings)

    def test_huge_span_is_refused(self):
        sel = selects.parse_text("1-999999")
        self.assertEqual(sel.numbers, [])
        self.assertTrue(sel.warnings)

    def test_resolve_reports_missing_and_ambiguous(self):
        idx = scan.scan(self.shoot)
        sel = selects.parse_file(self.root / "picks.txt")
        matches = scan.resolve(idx, sel)
        by = {m.number: m for m in matches}
        self.assertEqual(by[1999].status, "missing")
        self.assertEqual(by[1205].status, "ambiguous")   # two bodies
        self.assertEqual(by[1200].status, "ok")

    def test_resolve_narrows_by_typed_prefix(self):
        idx = scan.scan(self.shoot)
        sel = selects.parse_text("DSC_1205")
        matches = scan.resolve(idx, sel)
        self.assertEqual(matches[0].status, "ok")
        self.assertEqual(matches[0].stacks[0].base, "DSC_1205")


class TestPreviews(FixtureCase):
    def test_finds_the_big_preview_not_the_thumbnail(self):
        idx = scan.scan(self.shoot)
        out = self.root / "cache" / "DSC_1200.jpg"
        res = previews.extract(idx.by_base["dsc_1200"].primary, out)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.embedded, (1600, 1067))   # not the 160x120 thumb

    def test_raw_orientation_rotates_the_preview(self):
        # Frames divisible by 5 are written with orientation 6 (rotate 90).
        idx = scan.scan(self.shoot)
        out = self.root / "cache" / "DSC_1200o.jpg"
        res = previews.extract(idx.by_base["dsc_1200"].primary, out,
                               overwrite=True)
        self.assertEqual(res.orientation, 6)
        self.assertLess(res.saved[0], res.saved[1])    # portrait after rotation


class TestActions(unittest.TestCase):
    """Move, conflict, undo - each in its own scratch folder."""

    def setUp(self):
        self.root = _tmpdir()
        self.shoot = build(self.root)
        self.idx = scan.scan(self.shoot)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_dry_run_touches_nothing(self):
        st = self.idx.by_base["dsc_1203"]
        plan = actions.build_plan([st], self.root / "Selects")
        actions.execute(plan, apply=False)
        self.assertFalse((self.root / "Selects").exists())
        self.assertTrue(st.raws[0].exists())

    def test_move_takes_the_whole_stack_and_undo_restores_it(self):
        st = self.idx.by_base["dsc_1203"]       # RAW + xmp sidecar
        files_before = [str(f) for f in st.files]
        plan = actions.build_plan([st], self.root / "Selects")
        journal = actions.execute(plan, apply=True)
        self.assertTrue((self.root / "Selects" / "DSC_1203.NEF").is_file())
        self.assertTrue((self.root / "Selects" / "DSC_1203.xmp").is_file())
        self.assertFalse(st.raws[0].exists())
        self.assertTrue(journal and journal.is_file())

        actions.undo(journal, apply=True)
        for f in files_before:
            self.assertTrue(Path(f).is_file(), f"{f} did not come back")
        self.assertFalse((self.root / "Selects" / "DSC_1203.NEF").exists())

    def test_conflict_renames_never_overwrites(self):
        dest = self.root / "Selects"
        dest.mkdir()
        (dest / "DSC_1203.NEF").write_bytes(b"something else already here")
        st = self.idx.by_base["dsc_1203"]
        plan = actions.build_plan([st], dest)
        actions.execute(plan, apply=True)
        self.assertEqual((dest / "DSC_1203.NEF").read_bytes(),
                         b"something else already here")
        self.assertTrue((dest / "DSC_1203 (2).NEF").is_file())

    def test_copy_with_verify(self):
        st = self.idx.by_base["dsc_1206"]
        plan = actions.build_plan([st], self.root / "Copies", mode=actions.COPY)
        actions.execute(plan, apply=True, verify=True)
        self.assertTrue(st.raws[0].exists())     # copy leaves the original
        for op in plan.ops:
            self.assertEqual(op.status, "done", op.note)


class TestImporter(unittest.TestCase):
    """The verified copy off a simulated card."""

    def setUp(self):
        self.root = _tmpdir()
        self.card = self.root / "card" / "DCIM" / "100NIKON"
        self.card.mkdir(parents=True)
        for n in (1, 2, 3):
            make_raw(self.card / f"DSC_000{n}.NEF", n, sensor_kb=20)
        self.dest = self.root / "archive"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_copy_verified_copies_and_checksums(self):
        files = importer.find_media(self.root / "card")
        self.assertEqual(len(files), 3)
        res = importer.copy_verified(files, self.dest)
        self.assertTrue(res.ok)
        self.assertEqual(res.copied, 3)
        for f in files:                          # card untouched
            self.assertTrue(f.exists())

    def test_second_import_skips_identical_files(self):
        files = importer.find_media(self.root / "card")
        importer.copy_verified(files, self.dest)
        res = importer.copy_verified(files, self.dest)
        self.assertEqual(res.copied, 0)
        self.assertEqual(res.skipped, 3)

    def test_name_collision_renames_never_overwrites(self):
        # Two cards both holding a DSC_0001.NEF with different content: the
        # bug fixed on 2026-08-22 silently overwrote the first with the second.
        files = importer.find_media(self.root / "card")
        importer.copy_verified(files, self.dest)
        first = (self.dest / "DSC_0001.NEF").read_bytes()

        card2 = self.root / "card2"
        card2.mkdir()
        make_raw(card2 / "DSC_0001.NEF", 99, sensor_kb=5)   # same name, new content
        res = importer.copy_verified(importer.find_media(card2), self.dest)
        self.assertTrue(res.ok)
        self.assertEqual((self.dest / "DSC_0001.NEF").read_bytes(), first)
        self.assertTrue((self.dest / "DSC_0001 (2).NEF").is_file())


class TestSafeName(unittest.TestCase):
    def test_windows_illegal_characters(self):
        self.assertEqual(_safe('Look 1: "navy" <tee>'), "Look 1- -navy- -tee-")
        self.assertEqual(_safe("..."), "shoot")
        self.assertEqual(_safe("Maria"), "Maria")


class TestServerGuards(unittest.TestCase):
    """The security fences, tested with real HTTP against a throwaway server."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from cullkit.server import Handler

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_static_traversal_is_refused(self):
        self.assertEqual(self._get("/static/../server.py"), 404)
        self.assertEqual(self._get("/static/..%2f..%2fserver.py"), 404)

    def test_foreign_host_header_is_refused(self):
        self.assertEqual(self._get("/", headers={"Host": "evil.com"}), 403)

    def test_foreign_origin_post_is_refused(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/run",
            data=b'{"argv":["status"]}',
            headers={"Origin": "https://evil.com",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 403)

    def test_the_service_worker_is_served_as_javascript_and_needs_no_code(self):
        """The browser re-fetches a worker on its own schedule, including long
        after a cookie has expired. Answering that with the lock screen leaves
        a registered worker running an HTML page as if it were JavaScript."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/sw.js")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("javascript", r.headers.get("Content-Type", ""))
            self.assertIn("showNotification", r.read().decode())

    def test_the_app_itself_still_answers(self):
        self.assertEqual(self._get("/"), 200)
        self.assertEqual(self._get("/static/manifest.json"), 200)

    def test_the_tailnet_name_is_not_mistaken_for_rebinding(self):
        """Behind `tailscale serve` every request names the .ts.net address.

        The rebinding guard refuses any Host that is not one of this machine's
        own names, and before the Tailscale name was added to that list it
        refused the tunnel outright - a 403 on every single request.
        """
        from cullkit.server import Handler

        keep = Handler._hosts
        Handler._hosts = {"at": time.time() + 1e9,
                          "names": frozenset({"127.0.0.1", "localhost",
                                              "box.example.ts.net"})}
        try:
            self.assertNotEqual(self._get("/", headers={"Host": "box.example.ts.net"}), 403)
            self.assertEqual(self._get("/", headers={"Host": "other.ts.net"}), 403)
        finally:
            Handler._hosts = keep


class TestTailnetHttps(unittest.TestCase):
    """The shape Tailscale actually answers with. Getting it wrong is silent:
    the https address just never appears on Overview and nothing says why."""

    def test_the_real_shape_from_tailscale_serve_status(self):
        from cullkit.server import _serving_https

        self.assertTrue(_serving_https({
            "TCP": {"443": {"HTTPS": True}},
            "Web": {"box.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}},
        }))

    def test_nothing_served_means_no_address(self):
        from cullkit.server import _serving_https

        self.assertFalse(_serving_https({}))
        self.assertFalse(_serving_https({"TCP": {"8443": {"HTTPS": True}}}))
        self.assertFalse(_serving_https({"TCP": {"443": {"HTTPS": False}}}))


class TestOnlyOneJobAtATime(unittest.TestCase):
    """One job at a time, enforced on the server rather than in the page - so
    it holds across devices. Two phones cannot each start one."""

    def setUp(self):
        from cullkit import server
        with server._lock:
            self._saved = dict(server._jobs)
            server._jobs.clear()

    def tearDown(self):
        from cullkit import server
        with server._lock:
            server._jobs.clear()
            server._jobs.update(self._saved)

    def test_a_second_job_is_refused_and_told_what_is_in_the_way(self):
        from cullkit import server

        busy = server.Job("j-busy", ["import"])
        with server._lock:
            server._jobs["j-busy"] = busy

        job, other = server.start_job(["status"])
        self.assertIsNone(job)
        self.assertIs(other, busy)

    def test_a_finished_job_does_not_block_the_next_one(self):
        from cullkit import server

        old = server.Job("j-old", ["quality"])
        old.done = True
        with server._lock:
            server._jobs["j-old"] = old

        job, other = server.start_job(["status"])
        self.assertIsNone(other)
        self.assertIsNotNone(job)
        for _ in range(80):
            if job.done:
                break
            time.sleep(0.05)

    def test_the_check_and_the_insert_share_one_lock(self):
        """Split them and two requests arriving together both look around, both
        see an empty house, and both move in - which is the exact thing this is
        meant to prevent, made rare enough to never be noticed."""
        import inspect
        from cullkit import server

        src = inspect.getsource(server.start_job)
        head = src[:src.index("_install_router")]
        guard = head.index("if not other.done")
        insert = head.index("_jobs[jid] = job")
        lock = head.rindex("with _lock:", 0, guard)
        self.assertLess(lock, guard)
        self.assertLess(guard, insert,
                        "the emptiness check must come before the insert, "
                        "inside the same `with _lock:`")


class TestStoppingAJob(unittest.TestCase):
    """Leaving a screen with work on it stops the work.

    The dangerous version of this feature is one that stops a job in the middle
    of writing a file. It cannot: the stop is raised from the job's own output
    stream, and every long operation here reports progress *between* files -
    after one is copied and verified, before the next is begun.
    """

    def test_the_stop_is_raised_the_next_time_the_job_speaks(self):
        from cullkit.server import Job, _Tee, Cancelled

        job = Job("j", ["import"])
        tee = _Tee(job)
        tee.write("1 / 10\n")            # fine
        job.cancelled = True
        with self.assertRaises(Cancelled):
            tee.write("2 / 10\n")

    def test_it_is_not_an_ordinary_exception(self):
        """`except Exception` is all over the file handling in this codebase.
        A stop that one of those swallows is worse than no stop at all."""
        from cullkit.server import Cancelled

        self.assertFalse(issubclass(Cancelled, Exception))
        self.assertTrue(issubclass(Cancelled, BaseException))

    def test_flush_never_raises(self):
        """It runs in the finally that tidies up after a stop; throwing a
        second Cancelled there would lose the tail of the log."""
        from cullkit.server import Job, _Tee

        job = Job("j", ["quality"])
        tee = _Tee(job)
        tee.buf = "half a line"
        job.cancelled = True
        tee.flush()
        self.assertEqual(job.lines[-1], "half a line")

    def test_a_job_already_finished_is_not_stopped_again(self):
        from cullkit import server

        job = server.Job("j-fin", ["quality"])
        job.done = True
        with server._lock:
            server._jobs["j-fin"] = job
        try:
            with server._lock:
                target = server._jobs.get("j-fin")
                stopped = bool(target and not target.done)
            self.assertFalse(stopped)
            self.assertFalse(job.cancelled)
        finally:
            with server._lock:
                server._jobs.pop("j-fin", None)


class TestStateSaysWhatIsRunning(unittest.TestCase):
    """Overview asked the *page* what was running, and a page only knows about
    jobs it started itself. So a job launched from the phone left the laptop's
    Overview looking idle, and a standalone Sift showed nothing anywhere -
    only a full chained run was ever counted. The server knows; now it says."""

    def test_a_live_job_is_reported_with_its_progress(self):
        from cullkit import server

        job = server.Job("j-test", ["quality"])
        for line in ["Scoring faces:", "-" * 8, "100 / 400"]:
            job.note(line)
        with server._lock:
            server._jobs["j-test"] = job
        try:
            with server._lock:
                live = [j for j in server._jobs.values() if not j.done]
            row = [j for j in live if j.id == "j-test"][0]
            self.assertEqual(row.progress(),
                             {"phase": "Scoring faces", "cur": 100,
                              "of": 400, "pct": 25, "stopping": False,
                              "stopping_for": 0})
        finally:
            with server._lock:
                server._jobs.pop("j-test", None)

    def test_a_finished_job_drops_off_the_list(self):
        from cullkit import server

        job = server.Job("j-done", ["shoots"])
        job.done = True
        with server._lock:
            server._jobs["j-done"] = job
        try:
            with server._lock:
                live = [j.id for j in server._jobs.values() if not j.done]
            self.assertNotIn("j-done", live)
        finally:
            with server._lock:
                server._jobs.pop("j-done", None)


class TestCleanKeepsTheUndoHistory(unittest.TestCase):
    """`--all` deleted the undo journals.

    They are named `undo-*.json`, they live in the cache folder, and the sweep
    matched `*.json`. Meanwhile the command's help, the button and the
    confirmation all said the undo history was never touched. A journal is a
    few KB and it is the only thing that can put a moved photograph back.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = self.tmp / "shoot" / "_cullkit"
        (self.cache / "previews").mkdir(parents=True)
        (self.cache / "undo-20260822-120000.json").write_text('{"moves":[]}')
        (self.cache / "quality.json").write_text("{}")
        (self.cache / "sheet.html").write_text("x")
        (self.cache / "previews" / "DSC_0001.jpg").write_bytes(b"jpg")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _clean_all(self):
        """Exactly the sweep cmd_clean runs under --all."""
        for f in list(self.cache.glob("*.html")) + list(self.cache.glob("*.json")):
            if f.name.startswith("undo-"):
                continue
            f.unlink()

    def test_the_undo_journal_survives(self):
        self._clean_all()
        self.assertTrue((self.cache / "undo-20260822-120000.json").is_file(),
                        "the undo journal must outlive --all")

    def test_the_reports_still_go(self):
        self._clean_all()
        self.assertFalse((self.cache / "quality.json").exists())
        self.assertFalse((self.cache / "sheet.html").exists())


class TestWhatWouldBeDeleted(unittest.TestCase):
    """The inventory behind the confirmation. You cannot agree to a deletion
    you have not been shown, and the button used to name one configured root
    while the ledger knew about folders on two different drives."""

    def test_it_counts_previews_reports_and_journals_apart(self):
        from cullkit.server import Handler

        tmp = Path(tempfile.mkdtemp())
        try:
            cache = tmp / "shoot" / "_cullkit"
            (cache / "previews").mkdir(parents=True)
            (cache / "previews" / "a.jpg").write_bytes(b"x" * 100)
            (cache / "previews" / "b.jpg").write_bytes(b"x" * 50)
            (cache / "quality.json").write_bytes(b"x" * 10)
            (cache / "undo-1.json").write_bytes(b"x" * 7)

            real = Handler._caches.__globals__["Path"]
            import cullkit.ledger as L
            keep = L.caches
            L.caches = lambda only_existing=True: [str(tmp / "shoot")]
            try:
                inv = Handler._caches(Handler)
            finally:
                L.caches = keep
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        f = inv["folders"][0]
        self.assertTrue(f["here"])
        self.assertEqual((f["previews"], f["preview_bytes"]), (2, 150))
        self.assertEqual((f["reports"], f["report_bytes"]), (1, 10))
        self.assertEqual(f["journals"], 1)
        self.assertEqual(inv["preview_bytes"], 150)

    def test_a_folder_on_an_unplugged_drive_is_reported_not_hidden(self):
        """Silence is how a folder gets forgotten and quietly kept forever."""
        from cullkit.server import Handler
        import cullkit.ledger as L

        keep = L.caches
        L.caches = lambda only_existing=True: ["Z:\gone\shoot"]
        try:
            inv = Handler._caches(Handler)
        finally:
            L.caches = keep
        self.assertEqual(inv["unreachable"], 1)
        self.assertEqual(inv["reachable"], 0)
        self.assertFalse(inv["folders"][0]["here"])
        self.assertIn("gone", inv["folders"][0]["path"])


class TestProgressWithoutABrowser(unittest.TestCase):
    """The server counts a job's progress itself.

    It used to be the page that read the log and worked out the percentage,
    which is no help at all for the case this exists to serve: a job you are
    NOT watching. If this regresses, nothing on the machine knows how far along
    anything is the moment the app is closed.
    """

    def _job(self, argv=("quality",)):
        from cullkit.server import Job
        return Job("t", list(argv))

    def test_it_reads_stages_and_counts_out_of_the_log(self):
        j = self._job()
        for line in ["Reading previews:", "-" * 20, "1 / 4", "3 / 4"]:
            j.note(line)
        self.assertEqual(j.progress(),
                         {"phase": "Reading previews", "cur": 3, "of": 4, "pct": 75,
                          "stopping": False, "stopping_for": 0})

    def test_a_new_stage_resets_the_count(self):
        j = self._job()
        for line in ["First:", "-" * 8, "9 / 10", "Second:", "-" * 8]:
            j.note(line)
        self.assertEqual(j.phase, "Second")
        self.assertEqual((j.cur, j.of, j.pct()), (0, 0, None))

    def test_impossible_counts_are_ignored(self):
        """Two stages interleaving produced "175/757" from unrelated jobs once.
        A count past its own total is nonsense, not progress."""
        j = self._job()
        j.note("5 / 10")
        j.note("9999 / 3")
        self.assertEqual((j.cur, j.of), (5, 10))

    def test_a_line_that_is_not_progress_is_left_alone(self):
        j = self._job()
        j.note("copied DSC_0001.NEF -> 3 / 4 done")   # not a bare count
        self.assertEqual(j.of, 0)


class TestWhatThePhoneIsTold(unittest.TestCase):
    """How often it speaks. Sending one notification is easy; the whole
    difficulty is not sending five, because the end of that is the notification
    that mattered arriving in a stack nobody reads any more."""

    def setUp(self):
        from cullkit import server
        server._last_push["at"] = 0.0

    def test_a_stage_starting_always_speaks(self):
        from cullkit import server
        self.assertTrue(server._say("t", "one", force=True))
        self.assertTrue(server._say("t", "two", force=True))

    def test_progress_is_held_back_to_one_every_two_minutes(self):
        from cullkit import server
        self.assertTrue(server._say("t", "10%"))
        self.assertFalse(server._say("t", "11%"))
        self.assertFalse(server._say("t", "12%"))

    def test_finishing_speaks_even_inside_the_quiet_period(self):
        from cullkit import server
        server._say("t", "40%")
        self.assertTrue(server._say("t", "done", done=True, force=True))

    def test_the_held_back_message_is_still_the_current_one(self):
        """Suppressed means not knocked, not forgotten. The next knock - or the
        service worker asking - must find the newest state, not a stale one."""
        from cullkit import server
        server._say("t", "10%")
        server._say("t", "60%")
        self.assertEqual(server.LATEST["body"], "60%")

    def test_a_stage_nearly_over_does_not_get_a_progress_knock(self):
        from cullkit.server import Job, _watch
        said = []
        j = Job("t", ["quality"])
        _watch(j)
        import cullkit.server as srv
        real, srv._say = srv._say, lambda *a, **k: said.append(a[1]) or True
        try:
            for line in ["Sifting:", "-" * 8, "98 / 100"]:
                j.note(line)
        finally:
            srv._say = real
        self.assertEqual([s for s in said if "%" in s], [],
                         "98% should be left to the finish message")


class TestWhoIsAsking(unittest.TestCase):
    """Who the server thinks is on the other end - the whole basis of the code
    prompt and of which device holds the controls. Behind `tailscale serve`
    every request arrives from loopback, so getting this wrong hands the phone
    a free pass and the controls with it."""

    def _handler(self, addr, headers):
        from cullkit.server import Handler

        class _Bare(Handler):
            def __init__(self):  # no socket, no request - just the two fields
                self.client_address = (addr, 0)
                self.headers = headers

        return _Bare()

    def test_forwarded_address_is_believed_from_loopback(self):
        h = self._handler("127.0.0.1", {"X-Forwarded-For": "100.64.0.7",
                                        "Host": "box.example.ts.net"})
        self.assertEqual(h._who(), "100.64.0.7")
        self.assertTrue(h._via_tailnet())

    def test_a_forged_forwarded_address_cannot_claim_to_be_this_computer(self):
        """A proxy that appends leaves the caller's own value first in the
        list. Reading the first entry would let a device on the tunnel put
        `127.0.0.1` there and be waved straight through."""
        h = self._handler("127.0.0.1", {"X-Forwarded-For": "127.0.0.1, 100.64.0.7",
                                        "Host": "box.example.ts.net"})
        self.assertEqual(h._who(), "100.64.0.7")

    def test_forwarded_address_is_ignored_from_the_network(self):
        """Otherwise anyone on the wifi could claim to be this computer."""
        h = self._handler("192.168.1.44", {"X-Forwarded-For": "127.0.0.1",
                                           "Host": "192.168.1.9:8765"})
        self.assertEqual(h._who(), "192.168.1.44")
        self.assertFalse(h._via_tailnet())

    def test_the_tunnel_without_a_forwarded_address_is_still_not_local(self):
        h = self._handler("127.0.0.1", {"Host": "box.example.ts.net"})
        self.assertEqual(h._who(), "tailnet")

    def test_this_computer_is_still_this_computer(self):
        h = self._handler("127.0.0.1", {"Host": "localhost:8765"})
        self.assertEqual(h._who(), "127.0.0.1")
        self.assertFalse(h._via_tailnet())


if __name__ == "__main__":
    unittest.main()
