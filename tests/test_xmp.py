"""Stars and colour labels reach Lightroom through a sidecar, and nothing else.

Asked for on 2026-11-11: mark the selects and the rejects in a batch and write
it to XMP so Lightroom reads it. The promises that come with that:

    a photograph is never opened for writing
    an .xmp that is already there keeps everything else it holds, and the
        original is kept the first time it is touched
    JPEG and DNG are skipped - a sidecar means nothing for those
    a dry run writes nothing at all
    nothing is ever written to a camera card
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import hashlib
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from cullkit import cli, ledger, quality as Q, xmp as X
from tests.make_fixtures import build

ROOT = Path(__file__).resolve().parent.parent
NS = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}
XMP = "{http://ns.adobe.com/xap/1.0/}"
CRS = "{http://ns.adobe.com/camera-raw-settings/1.0/}"

DEVELOPED = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n <rdf:RDF '
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
    '    crs:Exposure2012="+0.35" crs:Temperature="5200"/>\n'
    ' </rdf:RDF>\n</x:xmpmeta>\n')


def desc(path):
    """The rdf:Description of a sidecar, parsed - so the test reads it the way
    Lightroom would, not by looking for a string."""
    import re
    text = re.sub(r"<\?xpacket.*?\?>", "", Path(path).read_text(encoding="utf-8"),
                  flags=re.S).strip()
    return ET.fromstring(text).find(".//rdf:Description", NS)


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Sidecars(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="cullkit-test-"))
        self.raw = self.d / "DSC_0001.NEF"
        self.raw.write_bytes(b"not really a raw, and it must stay exactly this")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_new_sidecar_says_the_rating_and_the_label(self):
        self.assertEqual(X.mark(self.raw, 3, "Green", apply=True), "new")
        d = desc(self.d / "DSC_0001.xmp")
        self.assertEqual((d.get(XMP + "Rating"), d.get(XMP + "Label")), ("3", "Green"))
        self.assertEqual(X.read(self.raw), {"rating": 3, "label": "Green"})

    def test_a_dry_run_writes_nothing(self):
        before = digest(self.raw)
        self.assertEqual(X.mark(self.raw, 3, "Green"), "new")
        self.assertEqual([p.name for p in self.d.iterdir()], ["DSC_0001.NEF"])
        self.assertEqual(digest(self.raw), before)

    def test_the_photograph_is_never_touched(self):
        before = digest(self.raw)
        X.mark(self.raw, 5, "Red", apply=True)
        X.mark(self.raw, 1, "Blue", apply=True)
        self.assertEqual(digest(self.raw), before)

    def test_an_existing_sidecar_keeps_its_develop_settings(self):
        side = self.d / "DSC_0001.xmp"
        side.write_text(DEVELOPED, encoding="utf-8")
        self.assertEqual(X.mark(self.raw, 4, "Yellow", apply=True), "changed")
        d = desc(side)
        self.assertEqual((d.get(XMP + "Rating"), d.get(XMP + "Label")), ("4", "Yellow"))
        self.assertEqual((d.get(CRS + "Exposure2012"), d.get(CRS + "Temperature")),
                         ("+0.35", "5200"))

    def test_the_original_is_kept_once_and_not_replaced_by_a_later_edit(self):
        side = self.d / "DSC_0001.xmp"
        side.write_text(DEVELOPED, encoding="utf-8")
        X.mark(self.raw, 4, "Yellow", apply=True)
        X.mark(self.raw, 2, "Red", apply=True)
        kept = self.d / "_cullkit" / X.BACKUP_DIR / "DSC_0001.xmp"
        self.assertEqual(kept.read_text(encoding="utf-8"), DEVELOPED)
        self.assertEqual(X.read(self.raw), {"rating": 2, "label": "Red"})

    def test_a_rating_already_there_is_replaced_not_doubled(self):
        side = self.d / "DSC_0001.xmp"
        side.write_text(DEVELOPED.replace('crs:Exposure2012', 'xmp:Rating="1" '
                        'xmlns:xmp="http://ns.adobe.com/xap/1.0/" crs:Exposure2012'),
                        encoding="utf-8")
        X.mark(self.raw, 5, None, apply=True)
        text = side.read_text(encoding="utf-8")
        self.assertEqual(text.count("xmp:Rating"), 1)
        self.assertEqual(desc(side).get(XMP + "Rating"), "5")

    def test_the_element_form_lightroom_sometimes_writes_is_understood(self):
        side = self.d / "DSC_0001.xmp"
        side.write_text(
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
            '<xmp:Rating>2</xmp:Rating><xmp:Label>Blue</xmp:Label>'
            '</rdf:Description></rdf:RDF></x:xmpmeta>', encoding="utf-8")
        self.assertEqual(X.read(self.raw), {"rating": 2, "label": "Blue"})
        self.assertEqual(X.mark(self.raw, 2, "Blue", apply=True), "same")
        self.assertEqual(X.mark(self.raw, 5, "Red", apply=True), "changed")
        self.assertEqual(X.read(self.raw), {"rating": 5, "label": "Red"})

    def test_saying_the_same_thing_twice_changes_nothing(self):
        X.mark(self.raw, 3, "Green", apply=True)
        before = digest(self.d / "DSC_0001.xmp")
        self.assertEqual(X.mark(self.raw, 3, "Green", apply=True), "same")
        self.assertEqual(digest(self.d / "DSC_0001.xmp"), before)
        self.assertFalse((self.d / "_cullkit").exists())

    def test_a_sidecar_that_is_not_xmp_it_knows_is_left_alone(self):
        side = self.d / "DSC_0001.xmp"
        side.write_text("<x:xmpmeta/>", encoding="utf-8")
        self.assertEqual(X.mark(self.raw, 3, "Green", apply=True), "strange")
        self.assertEqual(side.read_text(encoding="utf-8"), "<x:xmpmeta/>")

    def test_jpeg_and_dng_are_skipped(self):
        for name in ("DSC_0002.JPG", "DSC_0003.dng"):
            p = self.d / name
            p.write_bytes(b"x")
            self.assertEqual(X.mark(p, 3, "Green", apply=True), "not-raw")
        self.assertEqual(sorted(p.suffix for p in self.d.iterdir()), [".JPG", ".NEF", ".dng"])

    def test_a_reject_can_carry_minus_one_and_stars_stop_at_five(self):
        X.mark(self.raw, -1, "Red", apply=True)
        self.assertEqual(X.read(self.raw)["rating"], -1)
        X.mark(self.raw, 9, None, apply=True)
        self.assertEqual(X.read(self.raw)["rating"], 5)

    def test_the_label_is_in_lightrooms_own_language(self):
        self.assertEqual(X.label_text("green", "en"), "Green")
        self.assertEqual(X.label_text("green", "es"), "Verde")
        self.assertEqual(X.label_text("purple", "es"), "Púrpura")
        self.assertIsNone(X.label_text("none"))
        self.assertEqual(X.label_text("Para retocar"), "Para retocar")
        X.mark(self.raw, None, 'Ana & "Ben"', apply=True)
        self.assertEqual(desc(self.d / "DSC_0001.xmp").get(XMP + "Label"), 'Ana & "Ben"')


class Commands(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cullkit-test-"))
        self.shoot = build(self.root)
        self._ledger, self._config = ledger.LEDGER, ledger.CONFIG
        ledger.LEDGER = self.root / "ledger.json"
        ledger.CONFIG = self.root / "config.json"

    def tearDown(self):
        ledger.LEDGER, ledger.CONFIG = self._ledger, self._config
        shutil.rmtree(self.root, ignore_errors=True)

    def run_cli(self, *argv):
        args = cli.build_parser().parse_args([str(a) for a in argv])
        return args.func(args)

    def sifted(self):
        """Pretend Sift has been here: 1200-1209 fine, 1210 soft, 1211 hopeless."""
        rows = []
        for n in range(1200, 1212):
            sharp = {1210: 0.5, 1211: 0.1}.get(n, 1.0)
            rows.append({"base": f"DSC_{n}", "faces": 1, "detected": 1, "face_sharp": sharp,
                         "global_sharp": sharp, "eye_sharp": 1.0, "main_face_frac": 0.02,
                         "face_score": 0.9, "ear": 0.3, "eyes_known": True, "eyes_why": "",
                         "mean_luma": 120.0, "clip_white": 0.0, "clip_black": 0.0,
                         "focus": [0.5, 0.5], "unreadable": False, "group": None})
        c = self.shoot / "_cullkit"
        c.mkdir(exist_ok=True)
        (c / Q.SCORES_FILE).write_text(json.dumps(
            {"version": Q.SCORES_VERSION, "frames": rows}), encoding="utf-8")

    def test_the_picks_arrive_starred_where_they_land(self):
        dest = self.root / "TRABAJO"
        rc = self.run_cli("work", self.shoot, "1202", "1208", "--dest", dest,
                          "--into", "Selects", "--stars", "3", "--label", "green",
                          "--label-lang", "es", "--apply")
        self.assertEqual(rc, 0)
        for n in (1202, 1208):
            raw = dest / "Selects" / f"DSC_{n}.NEF"
            self.assertTrue(raw.is_file())
            self.assertEqual(X.read(raw), {"rating": 3, "label": "Verde"})
        self.assertFalse((self.shoot / "DSC_1204.xmp").exists(),
                         "what was not picked is not marked")

    def test_a_dry_run_of_the_move_marks_nothing(self):
        dest = self.root / "TRABAJO"
        self.run_cli("work", self.shoot, "1202", "--dest", dest, "--stars", "3")
        self.assertFalse(dest.exists())
        self.assertFalse((self.shoot / "DSC_1202.xmp").exists())

    def test_without_asking_for_marks_the_move_writes_no_sidecar(self):
        dest = self.root / "TRABAJO"
        self.run_cli("work", self.shoot, "1202", "--dest", dest, "--into", "S", "--apply")
        self.assertEqual([p.name for p in (dest / "S").iterdir() if p.suffix == ".xmp"], [])

    def test_sifts_verdicts_become_stars_and_colours(self):
        self.sifted()
        self.assertEqual(self.run_cli("xmp", self.shoot, "--stars", "2", "--reject-rating"), 0)
        self.assertFalse((self.shoot / "DSC_1202.xmp").exists(), "that was the dry run")
        self.assertEqual(self.run_cli("xmp", self.shoot, "--stars", "2", "--reject-rating",
                                      "--apply"), 0)
        read = lambda n: X.read(self.shoot / f"DSC_{n}.NEF")
        self.assertEqual(read(1202), {"rating": 2, "label": "Green"})
        self.assertEqual(read(1210), {"rating": None, "label": "Yellow"})
        self.assertEqual(read(1211), {"rating": -1, "label": "Red"})
        self.assertFalse((self.shoot / "DSC_1220.xmp").exists(), "never sifted, never marked")

    def test_his_own_call_decides_the_colour(self):
        self.sifted()
        Q.save_override(self.shoot, "DSC_1211", "keep")
        self.run_cli("xmp", self.shoot, "--apply")
        self.assertEqual(X.read(self.shoot / "DSC_1211.NEF")["label"], "Green")

    def test_a_folder_that_was_not_sifted_is_refused(self):
        self.assertEqual(self.run_cli("xmp", self.shoot, "--apply"), 1)
        self.assertFalse((self.shoot / "DSC_1202.xmp").exists())

    def test_nothing_is_written_to_a_camera_card(self):
        self.sifted()
        (self.root / "DCIM").mkdir()
        with patch("cullkit.plat.mount_of", lambda p: str(self.root)):
            self.assertEqual(self.run_cli("xmp", self.shoot, "--apply"), 1)
        self.assertFalse((self.shoot / "DSC_1202.xmp").exists())


class ThePage(unittest.TestCase):
    def test_the_darkroom_and_sift_can_both_ask_for_it(self):
        page = (ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="dev-lr"', "function devMarkArgs(a)", "function siftLightroom(",
                       "'--label-lang'", "Check first"):
            self.assertIn(needle, page)
        self.assertEqual(page.count("devMarkArgs(a); if (devRestOn())"), 2)


if __name__ == "__main__":
    unittest.main()
