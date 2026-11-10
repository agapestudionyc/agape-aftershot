"""The softness bar Sift learns from thumbs up and down."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cullkit import taste


class TestTheBarFollowsYourVotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(taste, "VOTES", Path(self.tmp.name) / "votes.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def vote(self, stem, v, r):
        return taste.record("E:\\Shoot", stem, v, blur_ratio=r)

    def test_nothing_is_learned_from_too_few_votes(self):
        self.vote("a", "soft", 0.7)
        self.vote("b", "sharp", 0.9)
        self.assertIsNone(taste.learned_blur_ratio())
        self.assertEqual(taste.summary()["ratio"], taste.DEFAULT_RATIO)

    def test_the_bar_lands_between_what_you_called_soft_and_sharp(self):
        for i, r in enumerate((0.55, 0.68, 0.74, 0.77)):
            self.vote(f"soft{i}", "soft", r)
        for i, r in enumerate((0.86, 0.95, 1.1, 1.3)):
            self.vote(f"sharp{i}", "sharp", r)
        got = taste.learned_blur_ratio()
        self.assertEqual(got["errors"], 0)
        self.assertGreater(got["ratio"], 0.77)
        self.assertLessEqual(got["ratio"], 0.86)

    def test_a_thumbs_down_on_a_sharp_frame_does_not_drag_the_bar_up(self):
        for i, r in enumerate((0.5, 0.6, 0.7)):
            self.vote(f"soft{i}", "soft", r)
        for i, r in enumerate((0.8, 0.9, 1.0, 1.1, 1.2)):
            self.vote(f"sharp{i}", "sharp", r)
        before = taste.learned_blur_ratio()["ratio"]
        self.vote("misfocus", "soft", 1.4)   # face missed, frame is crisp
        self.assertEqual(taste.learned_blur_ratio()["ratio"], before)

    def test_voting_again_replaces_and_none_takes_it_back(self):
        self.vote("a", "soft", 0.7)
        self.vote("a", "sharp", 0.7)
        self.assertEqual(taste.get("E:\\Shoot", "a")["vote"], "sharp")
        taste.record("E:\\Shoot", "a", None)
        self.assertIsNone(taste.get("E:\\Shoot", "a"))


if __name__ == "__main__":
    unittest.main()
