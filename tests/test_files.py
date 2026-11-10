"""The file explorer's promises, the ones that would hurt to get wrong.

Nothing deleted, nothing overwritten, nothing touched that belongs to Windows,
and nothing changed while a job is running. Each is one line of code that a
tidy-up could remove without anything on screen looking different.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import io
import shutil
import tempfile
import unittest
from pathlib import Path

from cullkit import files as F

ROOT = Path(__file__).resolve().parent.parent


class TestFileOperations(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="cullkit-files-"))
        (self.d / "a.txt").write_text("one")
        (self.d / "sub").mkdir()
        (self.d / "sub" / "b.txt").write_text("two")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_listing_says_what_each_thing_is(self):
        rows = {r["name"]: r for r in F.listing(str(self.d))["items"]}
        self.assertTrue(rows["sub"]["dir"])
        self.assertEqual(rows["a.txt"]["kind"], "text")
        self.assertEqual(rows["a.txt"]["size"], 3)

    def test_a_copy_never_replaces_what_is_there(self):
        got = F.copy([str(self.d / "a.txt")], str(self.d))
        self.assertEqual(Path(got["copied"][0]["to"]).name, "a (2).txt")
        self.assertEqual((self.d / "a.txt").read_text(), "one")

    def test_a_folder_is_not_copied_into_itself(self):
        got = F.copy([str(self.d / "sub")], str(self.d / "sub"))
        self.assertEqual(got["copied"], [])
        self.assertIn("itself", got["skipped"][0]["why"])

    def test_an_upload_never_replaces_what_is_there(self):
        got = F.save_upload(str(self.d), "a.txt", io.BytesIO(b"12345"), 5)
        self.assertEqual(got["name"], "a (2).txt")
        self.assertEqual((self.d / "a.txt").read_text(), "one")
        self.assertEqual([p.name for p in self.d.glob("*.part")], [])

    def test_an_upload_cut_short_leaves_nothing_behind(self):
        got = F.save_upload(str(self.d), "c.bin", io.BytesIO(b"123"), 10)
        self.assertIn("error", got)
        self.assertFalse((self.d / "c.bin").exists())
        self.assertEqual([p.name for p in self.d.glob("*.part")], [])

    def test_an_upload_cannot_name_its_way_out_of_the_folder(self):
        got = F.save_upload(str(self.d), "..\\\\..\\\\evil.txt", io.BytesIO(b"x"), 1)
        if got.get("ok"):
            self.assertEqual(Path(got["path"]).parent, self.d)

    def test_new_folder_refuses_a_bad_or_taken_name(self):
        self.assertIn("error", F.mkdir(str(self.d), "a:b"))
        self.assertIn("error", F.mkdir(str(self.d), "sub"))
        self.assertTrue(F.mkdir(str(self.d), "fresh")["ok"])

    def test_search_finds_names_below(self):
        names = [r["name"] for r in F.search(str(self.d), "B.TX")["items"]]
        self.assertEqual(names, ["b.txt"])

    def test_info_adds_up_a_folder(self):
        got = F.info(str(self.d))
        self.assertEqual((got["files"], got["folders"], got["size"]), (2, 1, 6))


class TestPlacesOrder(unittest.TestCase):
    def test_archive_then_working_then_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            arch, work = Path(d) / "arch", Path(d) / "work"
            arch.mkdir(); work.mkdir()
            got = F.places({"archive_root": str(arch), "work_root": str(work)})
            self.assertEqual([p["label"] for p in got][:2], ["Archive", "Working"])
            self.assertTrue(all(p["kind"] == "home" for p in got[2:]))

    def test_the_drives_come_before_the_places_on_screen(self):
        js = (ROOT / "cullkit" / "web" / "files.js").read_text(encoding="utf-8")
        a = js.index("function paintHomeTiles() {")
        body = js[a:a + 1600]
        self.assertLess(body.index("fx-drives"), body.index("'Places'"))
        b = js.index("function paintSide() {")
        side = js[b:b + 1800]
        self.assertLess(side.index("'Drives'"), side.index("'Places'"))


class TestWhatMayNotBeTouched(unittest.TestCase):
    def test_a_drive_and_windows_are_refused(self):
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        self.assertTrue(F.check_protected([str(drive)]))
        win = os.environ.get("SystemRoot")
        if win:
            self.assertTrue(F.check_protected([str(Path(win) / "System32")]))
        self.assertTrue(F.check_protected([str(Path.home() / ".cullkit" / "ledger.json")]))

    def test_the_bin_refuses_them_without_trying(self):
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        got = F.trash([str(drive)])
        self.assertEqual(got["trashed"], [])


class TestTheDoorInTheServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "cullkit" / "server.py").read_text(encoding="utf-8")
        cls.mod = (ROOT / "cullkit" / "files.py").read_text(encoding="utf-8")

    def test_nothing_in_the_module_deletes(self):
        """Delete means the Recycle Bin. The only unlink allowed is of the
        module's own half-written `.part` file."""
        for line in self.mod.splitlines():
            code = line.split("#", 1)[0]
            self.assertNotIn("rmtree", code)
            self.assertNotIn("os.remove", code)
            if ".unlink(" in code:
                self.assertIn("part.unlink", code)
        self.assertIn("FOF_ALLOWUNDO", self.mod)

    def test_changes_are_refused_while_a_job_runs(self):
        a = self.src.index("def _files_post(self, path, data):")
        body = self.src[a:a + 1700]
        self.assertLess(body.index("busy = self._files_busy()"), body.index('what == "mkdir"'))
        b = self.src.index("def _files_upload(self):")
        self.assertIn("self._files_busy()", self.src[b:b + 500])

    def test_only_the_computer_itself_can_open_a_window_on_it(self):
        a = self.src.index("def _files_post(self, path, data):")
        body = self.src[a:a + 700]
        self.assertIn("if not Handler._is_local(self._who()) or self._via_tailnet():", body)

    def test_a_file_shown_in_the_page_cannot_act_as_the_page(self):
        a = self.src.index("def _files_stream(self, f: Path, download=False):")
        self.assertIn('"Content-Security-Policy", "sandbox"', self.src[a:a + 2600])


class TestPlayingAndLooking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = (ROOT / "cullkit" / "files.py").read_text(encoding="utf-8")
        cls.js = (ROOT / "cullkit" / "web" / "files.js").read_text(encoding="utf-8")

    def test_every_kind_has_a_name(self):
        for ext, kind in ((".mkv", "video"), (".avi", "video"), (".wma", "audio"),
                          (".flac", "audio"), (".psd", "image"), (".nef", "raw"),
                          (".pdf", "pdf"), (".xmp", "text"), (".ts", "text"),
                          (".xyz", "file")):
            self.assertEqual(F.kind_of(ext), kind, ext)

    def test_a_raw_thumbnail_reads_the_head_of_the_file_first(self):
        """Searching the whole RAW for its preview read thirty megabytes off
        the card per frame: 1.8 seconds each, and a folder that never drew."""
        a = self.mod.index("def _raw_picture(")
        body = self.mod[a:a + 2600]
        self.assertIn("mm.find(P.SOI, pos, head)", body)
        self.assertIn("im.draft(", body)
        self.assertLess(body.index("mm.find(P.SOI, pos, head)"),
                        body.index("P._largest_embedded(mm, total)"))

    def test_what_the_browser_cannot_play_is_converted_not_refused(self):
        self.assertIn("m.onerror = convert;", self.js)
        self.assertIn("if (NATIVE.has(f.ext)) m.src = rawUrl(f); else convert();", self.js)

    def test_back_in_files_is_one_folder_up_until_the_top(self):
        a = self.js.index("function stepBack() {")
        body = self.js[a:a + 700]
        self.assertIn("if (FX.path) { fxGo(FX.parent || ''); return true; }", body)
        self.assertNotIn("FX.hist[--FX.hi]", body)
        self.assertTrue(body.rstrip().endswith("return false;\n}") or "return false;" in body)
        page = (ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("if (window.FILES && FILES.back()) return;", page)


class TestYouTubeVibes(unittest.TestCase):
    def test_only_real_ids_are_stored_and_text_is_kept_short(self):
        from cullkit import ytm
        got = ytm.clean([{"id": "s5cpzhZDo7I", "title": "x" * 500, "artist": "a" * 500},
                         {"id": "not-an-id"}, {"id": "s5cpzhZDo7I"}, "junk",
                         {"id": "../../etc/pw"}])
        self.assertEqual([g["id"] for g in got], ["s5cpzhZDo7I"])
        self.assertLessEqual(len(got[0]["title"]), 120)
        self.assertLessEqual(len(got[0]["artist"]), 80)

    def test_the_program_never_fetches_the_audio_itself(self):
        """The catalogue comes through the computer; the sound comes from
        YouTube's own player in the page. No stream URL is ever asked for."""
        src = (ROOT / "cullkit" / "ytm.py").read_text(encoding="utf-8")
        code = "\n".join(l.split("#", 1)[0] for l in src.split('"""')[2].splitlines())
        for word in ("get_song", "streamingData", "yt_dlp", "youtube_dl", "adaptiveFormats"):
            self.assertNotIn(word, code)
        page = (ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("https://www.youtube.com/iframe_api", page)

    def test_a_vibe_keeps_its_youtube_list_through_a_save(self):
        import cullkit.vibes as V
        with tempfile.TemporaryDirectory() as d:
            old = (V.HOME, V.STORE)
            V.HOME, V.STORE = Path(d), Path(d) / "vibes.json"
            try:
                saved = V.save([{"id": "a", "name": "A", "icon": "note", "colour": "#ff0000",
                                 "yt": [{"id": "s5cpzhZDo7I", "title": "t", "artist": "x"},
                                        {"id": "bad"}]}])
                self.assertEqual(saved[0]["yt"], [{"id": "s5cpzhZDo7I", "title": "t", "artist": "x"}])
                self.assertEqual(V.load()[0]["yt"][0]["id"], "s5cpzhZDo7I")
            finally:
                V.HOME, V.STORE = old


if __name__ == "__main__":
    unittest.main()
