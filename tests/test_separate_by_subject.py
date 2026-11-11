"""Separate cuts where the person changes, not where the wall does.

The old split fingerprinted the whole frame - which is mostly background - so
one client walking from a white wall to a brick one came back as two shoots.
The rule now, asked for on 2026-11-11:

    a new person after a pause         -> a new session   (reason: subject)
    a very long pause, whoever it is   -> a new session   (reason: time)
    the same person, another backdrop  -> the SAME session

No face model is loaded here. The faces are made-up vectors - one direction
per person, with a little noise - and the real clustering sorts them, so what
is being tested is the whole decision: who counts as the subject, what counts
as sustained, and where the cut goes.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from cullkit import sessions as S

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 11, 7, 12, 0, 0)
_RNG = np.random.default_rng(7)
_PEOPLE = {}


def _vec(who):
    """A unit vector for this person: the same direction each time, plus the
    kind of wobble a real face has from one frame to the next."""
    if who not in _PEOPLE:
        v = _RNG.normal(size=128)
        _PEOPLE[who] = v / np.linalg.norm(v)
    v = _PEOPLE[who] + _RNG.normal(scale=0.03, size=128)
    return (v / np.linalg.norm(v)).astype(np.float32)


class FakeStack:
    def __init__(self, n):
        self.base = f"DSC_{n:04d}"
        self.primary = Path(f"/card/{self.base}.NEF")
        self.frame = n


def face(base, who, area=0.02, score=0.9):
    return SimpleNamespace(image=Path(f"/p/{base}.jpg"), embedding=_vec(who),
                           area_frac=area, score=score, cluster=-1)


class Card:
    """A card being shot: frames added in order, each with whoever is in it."""

    def __init__(self):
        self.pairs, self.faces, self.t, self.n = [], {}, T0, 0

    def shoot(self, count, who=(), every_seconds=20, small=()):
        """`who` are the subjects; `small` are people at the back of the room."""
        who = (who,) if isinstance(who, str) else tuple(who)
        for _ in range(count):
            self.n += 1
            st = FakeStack(self.n)
            self.pairs.append((st, {"taken": self.t, "make": "NIKON", "model": "Z fc"}))
            self.faces[st.base] = ([face(st.base, w) for w in who] +
                                   [face(st.base, w, area=0.0012) for w in small])
            self.t += timedelta(seconds=every_seconds)
        return self

    def pause(self, minutes):
        self.t += timedelta(minutes=minutes)
        return self

    def split(self, **kw):
        with patch("cullkit.faces.name_from_library", lambda groups, *_a, **_k: groups):
            return S.split_by_subject(self.pairs, self.faces, **kw)


class TestTheRuleHeAskedFor(unittest.TestCase):
    def test_the_same_person_against_two_backdrops_is_one_session(self):
        """The case that started this: a pause to move to another wall."""
        card = Card().shoot(30, "ana").pause(20).shoot(30, "ana")
        shoots = card.split()
        self.assertEqual(len(shoots), 1)
        self.assertEqual(shoots[0].count, 60)
        self.assertTrue(shoots[0].joined, "it should say it looked and kept them together")

    def test_two_different_people_with_a_pause_are_two_sessions(self):
        card = Card().shoot(30, "ana").pause(20).shoot(30, "ben")
        shoots = card.split()
        self.assertEqual([s.count for s in shoots], [30, 30])
        self.assertEqual(shoots[1].cut["reason"], "subject")
        self.assertEqual(shoots[1].cut["gap_minutes"], 20)

    def test_a_very_long_pause_is_a_new_session_even_for_the_same_person(self):
        card = Card().shoot(30, "ana").pause(120).shoot(30, "ana")
        shoots = card.split()
        self.assertEqual(len(shoots), 2)
        self.assertEqual(shoots[1].cut["reason"], "time")

    def test_the_real_one_a_47_minute_pause_with_the_same_person(self):
        """8 November 2026: 129 frames, 47 minutes, 28 more of the same
        person. The old 45-minute rule made two shoots of it."""
        card = Card().shoot(129, "ana", every_seconds=18).pause(47).shoot(28, "ana")
        self.assertEqual(len(card.split()), 1)

    def test_the_first_session_has_no_reason_it_simply_starts(self):
        shoots = Card().shoot(20, "ana").pause(30).shoot(20, "ben").split()
        self.assertEqual(shoots[0].cut, {})


class TestWhatDoesNotCountAsAChange(unittest.TestCase):
    def test_continuous_shooting_is_never_cut_whoever_walks_in(self):
        """No pause, no cut. Two models taking turns is one session."""
        card = Card().shoot(20, "ana").shoot(20, "ben").shoot(20, "ana")
        self.assertEqual(len(card.split()), 1)

    def test_a_short_pause_is_not_a_place_to_cut(self):
        card = Card().shoot(20, "ana").pause(6).shoot(20, "ben")
        self.assertEqual(len(card.split()), 1)

    def test_somebody_in_the_background_is_not_the_subject(self):
        """The same assistant at the back of both halves must not hold two
        different clients together."""
        card = (Card().shoot(30, "ana", small=["assistant"]).pause(20)
                .shoot(30, "ben", small=["assistant"]))
        self.assertEqual(len(card.split()), 2)

    def test_one_frame_of_a_stranger_is_not_a_change_of_subject(self):
        """Sustained, not a blip: a passer-by caught large in a single frame
        after the pause does not make the second half somebody else's."""
        card = Card().shoot(30, "ana").pause(20)
        card.shoot(1, "stranger").shoot(29, "ana")
        self.assertEqual(len(card.split()), 1)

    def test_a_weak_detection_is_nobody(self):
        card = Card().shoot(30, "ana").pause(20).shoot(30, "ana")
        last = card.pairs[-1][0].base
        card.faces[last].append(face(last, "ghost", area=0.05, score=0.3))
        self.assertEqual(len(card.split()), 1)


class TestFramesWithNobodyInThem(unittest.TestCase):
    def test_they_stay_with_the_session_around_them(self):
        card = Card().shoot(20, "ana").shoot(10).shoot(20, "ana")
        shoots = card.split()
        self.assertEqual([s.count for s in shoots], [50])

    def test_a_stretch_of_details_goes_with_whichever_side_is_nearer(self):
        """ana, 12 min, details, 40 min, ben: the details were ana's."""
        card = (Card().shoot(20, "ana").pause(12).shoot(8).pause(40).shoot(20, "ben"))
        shoots = card.split()
        self.assertEqual([s.count for s in shoots], [28, 20])
        self.assertEqual(shoots[1].cut["reason"], "subject")
        self.assertEqual(shoots[1].cut["gap_minutes"], 40)

    def test_a_card_with_nobody_on_it_is_split_by_long_pauses_only(self):
        card = Card().shoot(20).pause(30).shoot(20).pause(100).shoot(20)
        self.assertEqual([s.count for s in card.split()], [40, 20])


class TestCouplesAndGroups(unittest.TestCase):
    def test_a_couple_then_one_of_them_alone_is_one_session(self):
        card = Card().shoot(30, ["ana", "ben"]).pause(20).shoot(30, "ana")
        self.assertEqual(len(card.split()), 1)

    def test_one_then_joined_by_a_partner_is_one_session(self):
        card = Card().shoot(30, "ana").pause(20).shoot(30, ["ana", "ben"])
        self.assertEqual(len(card.split()), 1)

    def test_two_couples_who_share_nobody_are_two_sessions(self):
        card = (Card().shoot(30, ["ana", "ben"]).pause(20).shoot(30, ["cleo", "dan"]))
        self.assertEqual(len(card.split()), 2)


class TestTheSettingsAreHis(unittest.TestCase):
    def test_the_two_pauses_can_be_changed(self):
        card = Card().shoot(30, "ana").pause(47).shoot(30, "ana")
        self.assertEqual(len(card.split(big_minutes=45)), 2)
        self.assertEqual(len(card.split(big_minutes=90)), 1)
        two = Card().shoot(30, "ana").pause(8).shoot(30, "ben")
        self.assertEqual(len(two.split(medium_minutes=10)), 1)
        self.assertEqual(len(two.split(medium_minutes=5)), 2)

    def test_without_faces_it_falls_back_to_the_old_45_minutes(self):
        card = Card().shoot(30, "ana").pause(50).shoot(30, "ana")
        with_faces = card.split()
        blind = S.split_by_subject(card.pairs, None)
        self.assertEqual((len(with_faces), len(blind)), (1, 2))
        self.assertEqual(blind[1].cut["reason"], "time")

    def test_every_frame_ends_up_in_exactly_one_session(self):
        card = (Card().shoot(40, "ana").pause(15).shoot(7).pause(25).shoot(33, "ben")
                .pause(200).shoot(3, "ben").pause(95).shoot(21, ["ana", "cleo"]))
        shoots = card.split()
        got = [s.base for sh in shoots for s in sh.stacks]
        self.assertEqual(sorted(got), sorted(p[0].base for p in card.pairs))
        self.assertEqual(len(got), len(set(got)))
        self.assertEqual([sh.index for sh in shoots], list(range(1, len(shoots) + 1)))


class TestOnlyAFewFramesAreLookedAt(unittest.TestCase):
    """An i3 with 8 GB cannot look at three thousand faces before lunch."""

    def test_a_dozen_per_stretch_however_long_the_stretch(self):
        card = Card().shoot(1500, "ana", every_seconds=4).pause(30).shoot(40, "ben")
        picks = S.sample_for_identity(card.pairs)
        self.assertLessEqual(len(picks), 2 * S.PER_ATOM)
        self.assertGreaterEqual(len(picks), S.PER_ATOM)

    def test_sampling_does_not_change_the_answer(self):
        card = Card().shoot(300, "ana", every_seconds=5).pause(20).shoot(200, "ben")
        looked = {st.base for st in S.sample_for_identity(card.pairs)}
        card.faces = {b: f for b, f in card.faces.items() if b in looked}
        self.assertEqual([s.count for s in card.split()], [300, 200])


class TestWhatIsWrittenDownAndShown(unittest.TestCase):
    def test_the_reason_and_the_sample_are_in_the_proposal(self):
        src = (ROOT / "cullkit" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('"cut": sh.cut, "joined": sh.joined,', src)
        self.assertIn('"sample": [Path(p).stem for p in sh.sample],', src)

    def test_the_old_way_is_still_there_to_fall_back_on(self):
        src = (ROOT / "cullkit" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('"--by-scene"', src)
        self.assertTrue(callable(S.split_combined) and callable(S.merge_by_people))

    def test_only_the_face_models_that_are_read_are_loaded(self):
        src = (ROOT / "cullkit" / "faces.py").read_text(encoding="utf-8")
        self.assertIn('allowed_modules=["detection", "recognition", "landmark_2d_106"]', src)

    def test_the_page_says_why_and_merges_with_one_press(self):
        page = (ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("sh.cut.text", page)
        self.assertIn("Same shoot as the one above", page)


if __name__ == "__main__":
    unittest.main()
