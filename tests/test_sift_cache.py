"""Sift looks once and judges as often as it is asked.

Asked for on 2026-11-11: analyse every frame one time, keep what was measured,
and let a slider change the verdict without analysing anything again. A
portrait with a soft background is not a soft photograph. A weak face
detection is not a face. Every flagged frame says why, and may say several
things. The photographer's own call on a frame stands over all of it.

No face model and no real photograph is needed for any of this: the rows are
what the slow half would have written down.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from cullkit import quality as Q

ROOT = Path(__file__).resolve().parent.parent


def row(base, face=1.0, glob=1.0, faces=1, ear=0.30, eye=1.0, **kw):
    r = {"base": base, "faces": faces, "detected": faces, "face_sharp": face if faces else 0.0,
         "global_sharp": glob, "eye_sharp": eye if faces else 0.0,
         "main_face_frac": 0.02 if faces else 0.0, "face_score": 0.9 if faces else 0.0,
         "ear": ear if faces else None, "eyes_known": bool(faces and ear is not None),
         "eyes_why": "" if (faces and ear is not None) else "no face found",
         "mean_luma": 120.0, "clip_white": 0.0, "clip_black": 0.0,
         "focus": [0.5, 0.4], "unreadable": False, "group": None}
    r.update(kw)
    return r


def shoot(*odd, n=12):
    """A dozen ordinary sharp portraits, plus whatever is being tested."""
    return [row(f"OK_{i:02d}") for i in range(n)] + list(odd)


def verdicts(rows, settings=None, overrides=None):
    scores, _ = Q.judge(rows, settings, overrides)
    return {s.base: s for s in scores}


class TestMovingASliderDoesNotLookAgain(unittest.TestCase):
    def test_judging_needs_no_photograph(self):
        """The rows point at files that do not exist, and it does not matter."""
        got = verdicts(shoot(row("SOFT", face=0.5, glob=0.4)))
        self.assertEqual(got["SOFT"].tier, "review")
        self.assertFalse(Path(got["SOFT"].image).exists())

    def test_the_same_measurements_give_a_different_answer_at_another_bar(self):
        rows = shoot(row("EDGE", face=0.72, glob=0.5))
        self.assertEqual(verdicts(rows)["EDGE"].tier, "keep")
        self.assertEqual(verdicts(rows, {"blur_ratio": 0.80})["EDGE"].tier, "review")
        self.assertEqual(verdicts(rows, {"blur_ratio": 0.80, "reject_ratio": 0.75})["EDGE"].tier,
                         "reject")

    def test_what_is_saved_is_measurement_not_judgement(self):
        scores, _ = Q.judge(shoot(row("SOFT", face=0.3)))
        saved = Q.to_row(next(s for s in scores if s.base == "SOFT"))
        for decided in ("tier", "reasons", "verdict", "blur_ratio"):
            self.assertNotIn(decided, saved)
        back = Q.from_row(saved)
        self.assertEqual((back.face_sharp, back.reasons, back.tier), (0.3, [], "keep"))

    def test_the_file_round_trips_and_an_old_one_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(Q.load_scores(d))
            c = Path(d) / "_cullkit"
            c.mkdir()
            (c / Q.SCORES_FILE).write_text(json.dumps(
                {"version": Q.SCORES_VERSION, "frames": shoot()}), encoding="utf-8")
            self.assertEqual(len(Q.load_scores(d)["frames"]), 12)
            (c / Q.SCORES_FILE).write_text(json.dumps(
                {"version": 1, "frames": shoot()}), encoding="utf-8")
            self.assertIsNone(Q.load_scores(d))


class TestSoftOnPurposeIsNotSoft(unittest.TestCase):
    def test_a_sharp_face_on_a_soft_background_is_kept(self):
        """Bokeh: the subject is sharp and nothing else is. That is the picture."""
        got = verdicts(shoot(row("BOKEH", face=1.1, glob=0.15)))
        self.assertEqual((got["BOKEH"].tier, got["BOKEH"].reasons), ("keep", []))

    def test_a_soft_face_with_something_else_sharp_is_missed_focus(self):
        got = verdicts(shoot(row("BACKFOCUS", face=0.4, glob=1.0)))
        self.assertEqual(got["BACKFOCUS"].reasons, ["soft", "missed focus"])

    def test_a_soft_face_with_nothing_sharp_is_motion_blur(self):
        got = verdicts(shoot(row("SHAKE", face=0.4, glob=0.3)))
        self.assertEqual(got["SHAKE"].reasons, ["soft", "motion blur"])

    def test_no_subject_and_nothing_in_focus_is_a_misfire_and_is_rejected(self):
        """The shutter going off at the floor."""
        rows = shoot(row("FLOOR", faces=0, glob=0.1),
                     *[row(f"DETAIL_{i}", faces=0, glob=1.0) for i in range(6)])
        got = verdicts(rows)
        self.assertEqual(got["FLOOR"].tier, "reject")
        self.assertIn("misfire (nothing in focus)", got["FLOOR"].reasons)
        self.assertEqual(got["DETAIL_0"].tier, "keep")


class TestAFrameCanSayMoreThanOneThing(unittest.TestCase):
    def test_reasons_are_a_list_and_can_hold_several(self):
        got = verdicts(shoot(row("BOTH", face=0.4, glob=0.3, ear=0.05)))
        self.assertEqual(got["BOTH"].reasons, ["soft", "motion blur", "eyes closed"])
        out = Q.frame_out(got["BOTH"])
        self.assertEqual(out["reasons"], ["soft", "motion blur", "eyes closed"])
        self.assertEqual(out["eyes"], "closed")

    def test_eyes_that_could_not_be_read_are_unknown_not_open(self):
        r = row("PROFILE", ear=None, eyes_why="the face is turned away")
        out = Q.frame_out(verdicts(shoot(r))["PROFILE"])
        self.assertEqual((out["eyes"], out["eyes_why"]), ("unknown", "the face is turned away"))
        self.assertNotIn("eyes closed", out["reasons"])
        self.assertEqual(Q.frame_out(verdicts(shoot())["OK_00"])["eyes"], "open")

    def test_eyes_behind_the_face_are_said_only_when_they_could_be_read(self):
        got = verdicts(shoot(row("EYES", face=1.0, eye=0.2),
                             row("TURNED", face=1.0, eye=0.2, ear=None)))
        self.assertEqual(got["EYES"].reasons, ["eyes soft"])
        self.assertEqual(got["TURNED"].reasons, [])


class TestAWeakDetectionIsNotAFace(unittest.TestCase):
    def test_it_is_dropped_before_anything_is_scored(self):
        import cv2

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "DSC_0001.jpg"
            rng = np.random.default_rng(3)
            cv2.imwrite(str(p), rng.integers(0, 255, (600, 900, 3), dtype=np.uint8))
            ghost = SimpleNamespace(image=p, bbox=(300, 200, 120, 120), score=0.31,
                                    area_frac=0.03, kps=None, landmarks=None)
            s = Q.score_image(p, [ghost])
        self.assertEqual((s.detected, s.faces), (1, 0))
        self.assertEqual(s.eyes_why, "no face found")
        self.assertEqual(s.face_score, 0.0)


class TestHisOwnCallStands(unittest.TestCase):
    def test_keep_and_reject_by_hand_win_over_the_measurements(self):
        rows = shoot(row("SOFT", face=0.1, glob=0.1))
        got = verdicts(rows, None, {"SOFT": "keep", "OK_03": "reject"})
        self.assertEqual((got["SOFT"].tier, got["SOFT"].override), ("keep", "keep"))
        self.assertEqual(got["OK_03"].tier, "reject")
        self.assertIn("soft", got["SOFT"].reasons, "it still says what it measured")
        self.assertEqual(got["OK_04"].override, "")

    def test_it_is_remembered_and_can_be_taken_back(self):
        with tempfile.TemporaryDirectory() as d:
            Q.save_override(d, "DSC_1", "keep")
            Q.save_override(d, "DSC_2", "reject")
            self.assertEqual(Q.load_overrides(d), {"DSC_1": "keep", "DSC_2": "reject"})
            Q.save_override(d, "DSC_1", None)
            self.assertEqual(Q.load_overrides(d), {"DSC_2": "reject"})
            self.assertEqual([p.name for p in Path(d).iterdir()], ["_cullkit"])

    def test_an_unreadable_frame_cannot_be_overruled_into_a_verdict(self):
        got = verdicts(shoot(row("BROKEN", unreadable=True)), None, {"BROKEN": "reject"})
        self.assertEqual(got["BROKEN"].tier, "error")


class TestTheBestOfEachRun(unittest.TestCase):
    def test_a_keeper_with_open_eyes_beats_a_sharper_blink(self):
        rows = shoot(row("A", face=1.6, ear=0.05, group=4),      # sharpest, but blinking
                     row("B", face=1.2, group=4),
                     row("C", face=0.3, glob=0.2, group=4))      # soft
        got = verdicts(rows)
        self.assertEqual([b for b in "ABC" if got[b].best], ["B"])

    def test_one_best_per_run_and_none_for_a_frame_on_its_own(self):
        rows = shoot(row("A", face=1.0, group=1), row("B", face=1.3, group=1),
                     row("C", face=1.1, group=2), row("D", face=0.9, group=2),
                     row("ALONE", group=3))
        got = verdicts(rows)
        self.assertEqual(sorted(b for b in got if got[b].best), ["B", "C"])

    def test_his_own_keep_makes_a_frame_the_best_of_its_run(self):
        rows = shoot(row("A", face=1.4, group=1), row("B", face=0.3, glob=0.2, group=1))
        self.assertTrue(verdicts(rows)["A"].best)
        mine = verdicts(rows, None, {"A": "reject", "B": "keep"})
        self.assertTrue(mine["B"].best)


class TestItIsWiredIn(unittest.TestCase):
    def test_the_command_measures_once_and_can_judge_from_the_cache(self):
        src = (ROOT / "cullkit" / "cli.py").read_text(encoding="utf-8")
        for needle in ('"--from-cache"', '"--fresh"', "def _sift_measure(",
                       "Q.load_overrides(idx.root)", "Q.SCORES_FILE"):
            self.assertIn(needle, src)

    def test_the_server_judges_without_starting_a_job(self):
        src = (ROOT / "cullkit" / "server.py").read_text(encoding="utf-8")
        self.assertIn('if path == "/api/sift":', src)
        self.assertIn('if path == "/api/sift/override":', src)
        body = src[src.index("def _sift(q):"):src.index("class Handler(")]
        self.assertNotIn("start_job", body)
        self.assertNotIn("score_image", body)

    def test_the_server_answer_from_a_saved_folder(self):
        from cullkit import server

        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(server._sift({"folder": d}), {"missing": True})
            c = Path(d) / "_cullkit"
            c.mkdir()
            rows = shoot(row("SOFT", face=0.5, glob=0.4))
            (c / Q.SCORES_FILE).write_text(json.dumps(
                {"version": Q.SCORES_VERSION, "frames": rows}), encoding="utf-8")
            for r in rows[:-2]:                      # two have been moved away
                (Path(d) / (r["base"] + ".NEF")).write_bytes(b"x")
            a = server._sift({"folder": d, "blur": "0.65"})
            b = server._sift({"folder": d, "blur": "0.40"})
        self.assertEqual(sum(1 for f in a["frames"] if f["moved"]), 2)
        self.assertEqual(a["count"]["keep"] + a["count"]["review"], 11)
        self.assertEqual(a["settings"]["blur_ratio"], 0.65)
        self.assertEqual(b["settings"]["blur_ratio"], 0.40)

    def test_the_page_has_dials_reasons_and_one_press_overrule(self):
        page = (ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        for needle in ("async function showSift(", "SIFT_DIALS", "/api/sift/override",
                       "'--from-cache', '--apply'", "eyes: cannot tell",
                       "Best of each run"):
            self.assertIn(needle, page)


if __name__ == "__main__":
    unittest.main()
