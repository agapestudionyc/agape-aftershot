"""33 proposed shoots for four real ones.

Splitting on a colour-and-light signal finds every pose change a session
makes, not just the ones that end it - a step to a different wall, a client
turning under the window, a change of backdrop mid-shoot. None of that is a
new shoot. The one signal that says so plainly is the one the split never
looked at: the same people are still in front of the camera.

These tests never touch a real face model - `faces.cluster` and its
neighbours are stood in for with plain objects, so what is being proved is
the merge logic itself: which groups fold back together, which stay apart,
and why.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from cullkit import sessions as S


class FakeStack:
    """Just enough of a Stack for merge_by_people to move around."""
    def __init__(self, base):
        self.base = base
        self.primary = f"/x/{base}.NEF"
        self.frame = int(base.split("_")[1])


def make_shoot(index, bases, start, end, camera="Nikon Z fc"):
    sh = S.Shoot(index=index, stacks=[FakeStack(b) for b in bases])
    sh.start, sh.end = start, end
    sh.camera = camera
    return sh


class FakeGroup:
    """Stands in for faces.PersonGroup - no real embeddings involved."""
    def __init__(self, name, faces):
        self.name = name
        self.faces = faces
        self.images = list({f.image for f in faces})


def fake_face(image, who):
    """A face tagged with who it belongs to, for the fake cluster() to sort by."""
    return SimpleNamespace(image=image, _who=who)


def fake_cluster(faces, backend_name="test"):
    """Groups by the `_who` tag instead of an embedding - the same shape
    real clustering returns (a list of groups, each with .faces/.images/.name),
    without needing a real face model loaded for the test."""
    by_who = {}
    for f in faces:
        by_who.setdefault(f._who, []).append(f)
    return [FakeGroup(who, fs) for who, fs in by_who.items()]


def fake_backend(on_msg=None):
    return SimpleNamespace(name="test")


class TestFoldingBackTogether(unittest.TestCase):
    """The exact report: four real shoots, split into far more proposals by
    a scene-change signal that fires on every pose change."""

    def setUp(self):
        self.t0 = datetime(2026, 8, 23, 16, 0, 0)

        def minutes(n):
            return self.t0 + timedelta(minutes=n)

        # Four real shoots, each artificially split into 2-3 scene-based
        # pieces the way the real detector over-splits on pose changes.
        self.shoots = [
            make_shoot(1, ["DSC_1", "DSC_2"], minutes(0), minutes(2)),
            make_shoot(2, ["DSC_3", "DSC_4"], minutes(2), minutes(5)),
            make_shoot(3, ["DSC_5", "DSC_6"], minutes(5), minutes(9)),
            # A real gap here - a different shoot, a different family.
            make_shoot(4, ["DSC_7", "DSC_8"], minutes(60), minutes(63)),
            make_shoot(5, ["DSC_9", "DSC_10"], minutes(63), minutes(66)),
            # An object shoot - nobody in it, must never merge with anything.
            make_shoot(6, ["DSC_11", "DSC_12"], minutes(120), minutes(123)),
            # A lone individual - single real shoot, one raw group.
            make_shoot(7, ["DSC_13", "DSC_14"], minutes(180), minutes(183)),
        ]
        # Every shoot's samples "extract" to themselves - extract_many just
        # needs to hand back something with .ok and .out per source.
        self.extract_patch = patch(
            "cullkit.previews.extract_many",
            side_effect=lambda srcs, cache_dir, long_side=None:
                [SimpleNamespace(ok=True, out=s) for s in srcs])
        self.extract_patch.start()
        self.addCleanup(self.extract_patch.stop)

        # Which people (by name) appear in the samples of each raw group,
        # keyed by the source path extract_many was told to hand back -
        # matches the /x/{base}.NEF paths FakeStack.primary produces.
        self.who = {
            1: ["Maria", "Maria"],       # recurs within the group
            2: ["Maria"],                # only one frame sampled - still counts
            3: ["Maria", "Maria"],
            4: ["Jose", "Jose"],
            5: ["Jose", "Jose"],
            6: [],                       # the plushie: no faces at all
            7: ["Carla", "Carla"],
        }

        def fake_analyse(paths, backend, min_face_frac=0.0):
            si = None
            for s in self.shoots:
                if paths and paths[0] in [st.primary for st in s.stacks]:
                    si = s.index
            names = self.who.get(si, [])
            return [fake_face(p, n) for p, n in zip(paths * 2, names)][:len(names)]

        self.analyse_patch = patch("cullkit.faces.analyse_previews",
                                   side_effect=fake_analyse)
        self.analyse_patch.start()
        self.addCleanup(self.analyse_patch.stop)

        patch("cullkit.faces.cluster", side_effect=fake_cluster).start()
        self.addCleanup(patch.stopall)
        patch("cullkit.faces.name_from_library", side_effect=lambda g, b: g).start()
        patch("cullkit.faces.get_backend", side_effect=fake_backend).start()

    def test_four_real_shoots_come_back_as_four(self):
        out = S.merge_by_people(self.shoots, "/cache", gap_minutes=45)
        self.assertEqual(len(out), 4)

    def test_the_frames_all_carried_over(self):
        # Snapshot before the call: merging mutates the shoots' own .stacks
        # lists in place, so the count has to be taken before, not after.
        before = sum(len(sh.stacks) for sh in self.shoots)
        out = S.merge_by_people(self.shoots, "/cache", gap_minutes=45)
        self.assertEqual(sum(len(sh.stacks) for sh in out), before)

    def test_a_real_gap_is_never_crossed_even_with_the_same_person(self):
        # Two Maria groups an hour apart must not merge just because they
        # share a name - only a short gap plus a shared person merges.
        shoots = [
            make_shoot(1, ["DSC_1"], self.t0, self.t0 + timedelta(minutes=2)),
            make_shoot(2, ["DSC_2"], self.t0 + timedelta(hours=3),
                      self.t0 + timedelta(hours=3, minutes=2)),
        ]
        with patch("cullkit.faces.analyse_previews",
                   side_effect=lambda paths, backend, min_face_frac=0.0:
                       [fake_face(p, "Maria") for p in paths]):
            out = S.merge_by_people(shoots, "/cache", gap_minutes=45)
        self.assertEqual(len(out), 2)

    def test_a_shoot_with_nobody_in_it_never_merges(self):
        out = S.merge_by_people(self.shoots, "/cache", gap_minutes=45)
        objectShoot = next(sh for sh in out if not sh.people)
        self.assertEqual(len(objectShoot.stacks), 2)   # its own two frames only

    def test_people_are_recorded_on_the_result(self):
        out = S.merge_by_people(self.shoots, "/cache", gap_minutes=45)
        by_people = {tuple(sh.people): sh for sh in out}
        self.assertIn(("Maria",), by_people)
        self.assertIn(("Jose",), by_people)
        self.assertIn((), by_people)      # the object shoot
        self.assertIn(("Carla",), by_people)

    def test_indices_are_renumbered_after_folding(self):
        out = S.merge_by_people(self.shoots, "/cache", gap_minutes=45)
        self.assertEqual([sh.index for sh in out], list(range(1, len(out) + 1)))

    def test_nothing_to_merge_is_a_no_op(self):
        out = S.merge_by_people([], "/cache", gap_minutes=45)
        self.assertEqual(out, [])

    def test_different_people_adjacent_in_time_do_not_merge(self):
        # Back-to-back in time, but a different subject - two real shoots
        # right after one another, not one over-split shoot.
        shoots = [
            make_shoot(1, ["DSC_1"], self.t0, self.t0 + timedelta(minutes=2)),
            make_shoot(2, ["DSC_2"], self.t0 + timedelta(minutes=2),
                      self.t0 + timedelta(minutes=4)),
        ]
        who = {1: "Maria", 2: "Jose"}

        def analyse(paths, backend, min_face_frac=0.0):
            si = 1 if "DSC_1" in paths[0] else 2
            return [fake_face(paths[0], who[si])] * 2
        with patch("cullkit.faces.analyse_previews", side_effect=analyse):
            out = S.merge_by_people(shoots, "/cache", gap_minutes=45)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
