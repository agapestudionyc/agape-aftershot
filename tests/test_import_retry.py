"""A card reader that misreads once must not fail a good file.

On 2026-10-20 an import stopped at DSC_4103 with "FAILED verify" while Windows
logged thousands of retried reads on the SD reader. The file itself was fine -
it copied cleanly by hand two days later. So a mismatch is read again before
it is called a failure, and one that never matches still fails.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cullkit import importer as I


class TestCopyRetriesAMisread(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cullkit-retry-"))
        self.card = self.tmp / "card"
        self.card.mkdir()
        self.src = self.card / "DSC_4103.NEF"
        self.src.write_bytes(b"raw" * 1000)
        self.dest = self.tmp / "dest"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _flaky_copy(self, bad_reads):
        real = shutil.copy2
        state = {"n": 0}

        def copy(a, b):
            real(a, b)
            state["n"] += 1
            if state["n"] <= bad_reads:
                Path(b).write_bytes(b"garbage")
        return copy

    def test_one_bad_read_is_copied_again(self):
        steps = []
        with mock.patch.object(I.shutil, "copy2", self._flaky_copy(1)):
            res = I.copy_verified([self.src], self.dest,
                                  on_step=lambda *a: steps.append(a[3]))
        self.assertTrue(res.ok)
        self.assertEqual(res.copied, 1)
        self.assertEqual((self.dest / self.src.name).read_bytes(), self.src.read_bytes())
        self.assertIn("copied on try 2", steps)
        self.assertFalse(list(self.dest.glob("*.part")))

    def test_a_file_that_never_matches_still_fails(self):
        with mock.patch.object(I.shutil, "copy2", self._flaky_copy(99)):
            res = I.copy_verified([self.src], self.dest)
        self.assertFalse(res.ok)
        self.assertEqual(res.copied, 0)
        self.assertIn("after 3 tries", res.failed[0][1])
        self.assertFalse((self.dest / self.src.name).exists())
        self.assertFalse(list(self.dest.glob("*.part")))


if __name__ == "__main__":
    unittest.main()
