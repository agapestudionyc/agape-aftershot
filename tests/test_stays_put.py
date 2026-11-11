"""The app stays where you are.

On 2026-11-10 the complaint was "it refreshes on its own and sends me Home".
It was not refreshing. A poll had landed on the instant one of the small state
files was being swapped, Windows refused the read, the server answered "no
darkroom is open", and the page - believing it - stepped out to Home in the
middle of somebody choosing photographs. Twenty-one times in two weeks.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["CULLKIT_NO_JOURNAL"] = "1"

from cullkit import server  # noqa: E402

PAGE = (Path(server.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")


class TestAStateFileThatCannotBeReadIsNotEmpty(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.f = Path(self.dir.name) / "darkroom.json"
        server._last_good.clear()

    def tearDown(self):
        self.dir.cleanup()

    def test_a_refused_read_keeps_the_last_answer(self):
        self.f.write_text(json.dumps({"folder": "E:/shoot", "at": 1}), encoding="utf-8")
        self.assertEqual(server._read_state(self.f)["folder"], "E:/shoot")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("in use")):
            self.assertEqual(server._read_state(self.f)["folder"], "E:/shoot")

    def test_a_torn_read_keeps_the_last_answer(self):
        self.f.write_text(json.dumps({"folder": "E:/shoot"}), encoding="utf-8")
        server._read_state(self.f)
        self.f.write_text("", encoding="utf-8")          # truncated, not yet filled
        self.assertEqual(server._read_state(self.f)["folder"], "E:/shoot")

    def test_a_read_refused_once_is_simply_tried_again(self):
        self.f.write_text(json.dumps({"folder": "E:/shoot"}), encoding="utf-8")
        real = Path.read_text
        calls = {"n": 0}

        def flaky(self_, *a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("in use")
            return real(self_, *a, **k)

        with mock.patch.object(Path, "read_text", flaky):
            self.assertEqual(server._read_state(self.f)["folder"], "E:/shoot")

    def test_a_file_that_is_really_gone_says_nothing(self):
        self.f.write_text(json.dumps({"folder": "E:/shoot"}), encoding="utf-8")
        server._read_state(self.f)
        self.f.unlink()
        self.assertEqual(server._read_state(self.f), {})

    def test_the_darkroom_pointer_is_read_that_way(self):
        import inspect
        for fn in (server._open_darkroom, server._recently_closed,
                   server._last_actor, server._last_place, server._read_run):
            self.assertIn("_read_state(", inspect.getsource(fn), fn.__name__)


class TestNoFolderIsNotAFolder(unittest.TestCase):
    """Path("") is ".", the program's own directory."""

    def test_the_server_refuses_to_save_a_selection_nowhere(self):
        src = Path(server.__file__).read_text(encoding="utf-8")
        a = src.index('if path == "/api/selection":', src.index("def do_POST"))
        body = src[a:a + 900]
        self.assertLess(body.index('if not str(data.get("folder") or "").strip():'),
                        body.index('folder = Path(data["folder"])'))

    def test_the_page_does_not_send_one(self):
        a = PAGE.index("function saveSelection()")
        self.assertIn("if (!DEV.folder) return;", PAGE[a:a + 200])


class TestClosedHasToBeSaidTwice(unittest.TestCase):
    def test_the_darkroom(self):
        a = PAGE.index("function noticeTheDarkroomClosed()")
        body = PAGE[a:a + 2600]
        self.assertLess(body.index("if (!DEV.goneSince) {"), body.index("DEV.folder = ''"))
        self.assertIn("if (Date.now() - DEV.goneSince < 3000) return;", body)
        # An answer saying it is open again wipes the doubt.
        self.assertIn("if (STATE.darkroom) { DEV.goneSince = 0; return; }", body)
        # And the pending save must not fire with no folder in it.
        self.assertLess(body.index("clearTimeout(whereTimer)"), body.index("DEV.folder = ''"))

    def test_a_run(self):
        a = PAGE.index("function noticeTheRunEnded()")
        body = PAGE[a:a + 900]
        self.assertLess(body.index("if (!RUN_GONE_SINCE) {"), body.index("FLOW.on = false"))


class TestAFreshPageGoesBackToWhereThisDeviceWas(unittest.TestCase):
    def test_a_live_page_is_left_alone_but_a_new_one_follows_itself(self):
        a = PAGE.index("async function followHandover(taking, own)")
        self.assertIn("if (s.device === DEVICE_ID && !own) return false;", PAGE[a:a + 900])
        self.assertIn("await followHandover(false, true);", PAGE)
        # Taking the controls on a page that is still alive must not move it.
        self.assertIn("await restoreRun(true) || await followHandover(true);", PAGE)

    def test_the_darkroom_icon_returns_to_the_darkroom_you_were_in(self):
        a = PAGE.index("async function backIntoMyDarkroom()")
        body = PAGE[a:a + 700]
        self.assertIn("d.device !== DEVICE_ID", body)       # yours, not somebody's
        self.assertIn("> HANDOVER_FRESH) return false;", body)  # and recent

    def test_arriving_takes_the_controls_without_a_tap(self):
        a = PAGE.index("if (!HELD && !document.hidden && !DOWN) {")
        self.assertIn("fetch('/api/control'", PAGE[a:a + 300])
        self.assertLess(a, PAGE.index("resumed = await restoreRun(false);"))

    def test_every_arrival_is_written_down(self):
        self.assertIn("logEvent('ui.page-load'", PAGE)
        self.assertIn("logEvent('ui.refresh-button'", PAGE)


if __name__ == "__main__":
    unittest.main()


class TestArrangingAPlaylistDoesNotWait(unittest.TestCase):
    """Adding a song was save, fetch, redraw - three trips with the button dead."""

    def test_adding_from_the_panel_waits_for_nothing(self):
        a = PAGE.index("async function vibeTakeYt(items)")
        body = PAGE[a:PAGE.index("var VP_ADDS", a)]
        self.assertNotIn("await saveVibes()", body)
        self.assertNotIn("await loadVibes()", body)
        self.assertIn("vibeFlush();", body)

    def test_a_tap_made_during_a_save_is_saved_too(self):
        a = PAGE.index("function vibeFlush()")
        body = PAGE[a:a + 700]
        self.assertIn("while (VIBE_DIRTY) { VIBE_DIRTY = false; await saveVibes(); }", body)
        # And neither a late answer nor a refresh may write the old list back.
        self.assertIn("if (r.vibes && !VIBE_DIRTY) {", PAGE)
        b = PAGE.index("async function loadVibes()")
        self.assertIn("if (VIBE_SAVING || VIBE_DIRTY) return VIBE_DEFS;", PAGE[b:b + 300])

    def test_the_card_keeps_its_search_when_a_song_goes_in(self):
        a = PAGE.index("const paintYtList = () => {")
        b = PAGE.index("return card;", a)
        self.assertNotIn("ytSave", PAGE[a:b])

    def test_deleting_every_vibe_leaves_none(self):
        from cullkit import vibes as V
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(V, "HOME", Path(d)), \
                 mock.patch.object(V, "STORE", Path(d) / "vibes.json"):
                self.assertEqual(len(V.load()), 3)      # a fresh install
                V.save([])
                self.assertEqual(V.load(), [])


class TestYouTubePlaysInTheAppsOwnPlayer(unittest.TestCase):
    """Asked for on 2026-11-10: no little video window."""

    def test_one_place_decides_how_a_track_plays(self):
        a = PAGE.index("function ytTrack(t)")
        body = PAGE[a:a + 300]
        self.assertIn("VIBE_YTDL ? {name, url: YT_AUDIO + t.id}", body)
        self.assertIn("{name, yt: t.id, url: 'yt:' + t.id}", body)   # the fallback
        self.assertIn("PLAYLIST[v.id] = v.yt.map(ytTrack);", PAGE)
        self.assertIn("vibeGo(mode, ytTrack(k));", PAGE)

    def test_a_song_that_cannot_be_fetched_does_not_stall_the_vibe(self):
        a = PAGE.index("function playFile(")
        body = PAGE[a:PAGE.index("a YouTube vibe ===", a)]
        self.assertIn("a.addEventListener('error'", body)
        self.assertIn("if (onEnd) setTimeout(onEnd, 800);", body)
        self.assertIn("if (a.dataset.stopped || a.dataset.dead) return;", body)

    def test_without_the_library_nothing_is_fetched(self):
        from cullkit import ytm
        with mock.patch.dict("sys.modules", {"yt_dlp": None}):
            self.assertFalse(ytm.can_fetch())
            self.assertEqual(ytm.audio("AkFqg5wAuFk")[0] if not ytm.have("AkFqg5wAuFk")
                             else None, None)

    def test_only_a_real_id_is_ever_asked_for(self):
        from cullkit import ytm
        self.assertEqual(ytm.audio("../../etc/passwd"), (None, "not a track"))
        self.assertIsNone(ytm.have("../../x"))

    def test_audio_no_vibe_holds_is_dropped(self):
        from cullkit import ytm
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(ytm, "CACHE", Path(d)):
                (Path(d) / "AAAAAAAAAAA.m4a").write_bytes(b"x" * 2048)
                (Path(d) / "BBBBBBBBBBB.m4a").write_bytes(b"x" * 2048)
                self.assertEqual(ytm.prune(["AAAAAAAAAAA"]), 1)
                self.assertTrue(ytm.have("AAAAAAAAAAA"))
                self.assertIsNone(ytm.have("BBBBBBBBBBB"))

    def test_it_stays_an_optional_extra(self):
        req = (Path(server.__file__).parent.parent / "requirements.txt")
        if req.is_file():
            self.assertNotIn("yt-dlp", req.read_text(encoding="utf-8").lower())
