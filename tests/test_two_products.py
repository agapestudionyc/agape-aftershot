"""Aftershot and the Darkroom are two products, each with its own way in.

Asked for on 2026-11-10: New Task asks which of the two first, Aftershot then
asks whole run or one step, and each has an icon on the phone's home screen
that opens the app straight on it.

Three things here fail silently if they drift, which is why they are written
down as tests rather than left to be noticed:

- an icon the manifest names but the server answers with the lock screen shows
  up on the phone as a grey square, and only on the phone;
- a shortcut whose word the page does not recognise opens Home and looks like
  the app ignored the tap;
- a state file read while it is being rewritten answered a poll with a 500.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "cullkit" / "web"


def _page():
    return (WEB / "index.html").read_text(encoding="utf-8")


class TestHomeScreenShortcuts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((WEB / "manifest.json").read_text(encoding="utf-8"))
        cls.src = _page()

    def test_there_is_one_shortcut_per_product_and_one_for_files(self):
        urls = [s["url"] for s in self.manifest["shortcuts"]]
        self.assertEqual(urls, ["/?new=aftershot", "/?new=darkroom", "/?new=files"])

    def test_every_shortcut_icon_exists_and_needs_no_code(self):
        """The phone fetches these with no cookie. Anything not in PUBLIC
        comes back as the lock screen, and the icon is a blank."""
        from cullkit.server import Handler
        for s in self.manifest["shortcuts"]:
            self.assertTrue(s["icons"], s["name"])
            for icon in s["icons"]:
                self.assertIn(icon["src"], Handler.PUBLIC)
                self.assertTrue((WEB / icon["src"][len("/static/"):]).is_file(),
                                icon["src"])

    def test_the_page_knows_every_word_a_shortcut_can_send(self):
        start = self.src.index("const SHORTCUT = ")
        body = self.src[start:start + 1600]
        for s in self.manifest["shortcuts"]:
            word = s["url"].split("=", 1)[1]
            self.assertIn(f"'{word}'", body)
        # Taken off the address at once, or a refresh starts a second task.
        self.assertIn("history.replaceState", body)

    def test_a_shortcut_never_walks_over_work(self):
        start = self.src.index("function launchShortcut(which)")
        body = self.src[start:start + 700]
        self.assertRegex(body, r"if \(FLOW\.on \|\| BUSY > 0 \|\|")
        self.assertLess(body.index("return false"), body.index("devEnter("))

    def test_typing_the_code_keeps_the_shortcut(self):
        """The lock screen used to go to `/` on success, which dropped
        `?new=darkroom` on a phone whose cookie had expired."""
        from cullkit.server import LOGIN_PAGE
        self.assertIn("location.href='/'+location.search", LOGIN_PAGE)


class TestNewTaskAsksTwoQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _page()
        a = cls.src.index('<div id="v-newtask"')
        cls.menu = cls.src[a:cls.src.index("<!-- OVERVIEW -->", a)]
        cls.root = cls.menu[cls.menu.index('id="nt-root"'):
                            cls.menu.index('id="nt-aftershot"')]
        cls.after = cls.menu[cls.menu.index('id="nt-aftershot"'):]

    def test_the_first_question_is_only_the_two_products(self):
        self.assertIn('id="door-aftershot"', self.root)
        self.assertIn('id="choice-dev"', self.root)
        self.assertNotIn("data-go=", self.root)

    def test_aftershot_offers_the_whole_run_and_its_steps_but_not_the_darkroom(self):
        gos = re.findall(r'<button class="choice[^"]*" data-go="(\w+)"( data-solo="1")?',
                         self.after)
        self.assertEqual(gos, [("import", ""), ("import", ' data-solo="1"'),
                               ("shoots", ""), ("cull", "")])
        self.assertNotIn("choice-dev", self.after)

    def test_back_on_the_second_question_returns_to_the_first(self):
        start = self.src.index("const goBack = () => {")
        self.assertIn("if (CURRENT_VIEW === 'newtask') return ntShow('root');",
                      self.src[start:start + 300])
        self.assertIn("(now === 'newtask' && NT.step === 'root')", self.src)


class TestTheSecondSkin(unittest.TestCase):
    """Things the 2026-11-10 redesign depends on that break without a sound."""

    @classmethod
    def setUpClass(cls):
        cls.src = _page()

    def test_motion_is_the_apps_own_switch_not_the_systems(self):
        """`prefers-reduced-motion` follows a Windows setting that is off on
        the machine this runs on. Gating the new animations on it removed
        every one of them there."""
        a = self.src.index("the second skin: two lit rooms")
        skin = self.src[a:self.src.index("</style>", a)]
        self.assertNotIn("@media (prefers-reduced-motion", skin)
        self.assertIn("localStorage.getItem('aftershot.motion') !== '0'", self.src)

    def test_the_light_is_set_from_where_you_are_when_it_lands(self):
        a = self.src.index("function setTheLight()")
        body = self.src[a:a + 420]
        self.assertIn("CURRENT_VIEW === 'dev'", body)
        self.assertIn("killTheLights(setTheLight);", self.src)

    def test_a_darkroom_just_opened_is_not_reported_closed(self):
        a = self.src.index("function noticeTheDarkroomClosed()")
        body = self.src[a:a + 2600]
        self.assertIn("if (DEV.opening ||", body)
        self.assertLess(body.index("if (DEV.opening ||"), body.index("DEV.folder = ''"))

    def test_the_filmstrip_is_not_rebuilt_on_every_step(self):
        a = self.src.index("function paintViewer()")
        body = self.src[a:a + 5200]
        self.assertIn("if (strip.dataset.key !== key) {", body)

    def test_a_flash_is_shown_where_the_tagline_is_hidden(self):
        a = self.src.index("function flash(text")
        self.assertIn("if (!t.offsetParent)", self.src[a:a + 700])


class TestStateFilesAreSwappedInWhole(unittest.TestCase):
    def test_a_reader_never_sees_a_half_written_file(self):
        from cullkit.server import _write_whole
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "run.json"
            _write_whole(f, json.dumps({"active": False}))
            stop = threading.Event()
            torn = []

            def read():
                while not stop.is_set():
                    try:
                        text = f.read_text(encoding="utf-8")
                    except OSError:
                        continue           # mid-swap on Windows; not a tear
                    try:
                        json.loads(text)
                    except ValueError:
                        torn.append(text)

            t = threading.Thread(target=read)
            t.start()
            try:
                for i in range(400):
                    _write_whole(f, json.dumps({"active": True, "n": i,
                                                "pad": "x" * 4000}))
            finally:
                stop.set()
                t.join()
            self.assertEqual(torn, [])
            self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["n"], 399)
            self.assertEqual([p.name for p in Path(d).iterdir()], ["run.json"])

    def test_the_run_state_is_written_that_way(self):
        src = (ROOT / "cullkit" / "server.py").read_text(encoding="utf-8")
        a = src.index('data["saved"] = time.time()')
        self.assertIn("_write_whole(f, json.dumps(data", src[a:a + 200])


if __name__ == "__main__":
    unittest.main()
