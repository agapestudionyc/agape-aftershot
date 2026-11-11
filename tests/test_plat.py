"""What Windows and macOS do differently, as far as this machine can check.

The macOS half of `cullkit/plat.py` has not been run on a Mac. These tests run
the parts of it that are plain folder-walking against temporary folders, which
is the most a Windows machine can honestly say about it.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import shutil
import tempfile
import unittest
from pathlib import Path

from cullkit import plat

ROOT = Path(__file__).resolve().parent.parent


class TestVolumes(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="cullkit-plat-"))
        (self.d / "Volumes" / "Photos").mkdir(parents=True)
        (self.d / "Volumes" / "NIKON" / "DCIM").mkdir(parents=True)
        (self.d / "Volumes" / ".hidden").mkdir()
        (self.d / "Volumes" / "Recovery").mkdir()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_the_startup_disk_comes_first_then_what_is_mounted(self):
        got = plat.posix_volumes(root=str(self.d), volumes_dir=str(self.d / "Volumes"))
        self.assertEqual(got[0]["mount"], str(self.d))
        self.assertEqual([v["label"] for v in got[1:]], ["NIKON", "Photos"])

    def test_a_camera_card_is_recognised_by_its_dcim_folder(self):
        got = {v["label"]: v for v in plat.posix_volumes(
            root=str(self.d), volumes_dir=str(self.d / "Volumes"))}
        self.assertEqual(got["NIKON"]["tag"], "SD")
        self.assertNotEqual(got["Photos"]["tag"], "SD")

    def test_system_and_hidden_volumes_are_left_out(self):
        labels = [v["label"] for v in plat.posix_volumes(
            root=str(self.d), volumes_dir=str(self.d / "Volumes"))]
        self.assertNotIn("Recovery", labels)
        self.assertNotIn(".hidden", labels)

    def test_no_volumes_folder_is_just_the_one_disk(self):
        got = plat.posix_volumes(root=str(self.d), volumes_dir=str(self.d / "nope"))
        self.assertEqual(len(got), 1)


class TestTheBin(unittest.TestCase):
    def test_moving_to_the_bin_keeps_the_file_and_never_replaces_one(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for text in ("first", "second"):
                (d / "a.txt").write_text(text)
                self.assertTrue(plat.move_to_bin(d / "a.txt", d / ".Trash"))
            self.assertFalse((d / "a.txt").exists())
            kept = sorted(p.read_text() for p in (d / ".Trash").iterdir())
            self.assertEqual(kept, ["first", "second"])

    def test_nothing_in_the_module_deletes(self):
        src = (ROOT / "cullkit" / "plat.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            for word in ("rmtree", "os.remove", ".unlink(", "shutil.move"):
                self.assertNotIn(word, code)


class TestDrivesAndPaths(unittest.TestCase):
    def test_two_folders_on_one_drive_are_on_the_same_drive(self):
        home = Path.home()
        self.assertTrue(plat.same_drive(home, home / "anything"))

    def test_the_top_of_a_drive_is_somewhere_writable(self):
        top = plat.drive_top(Path.home())
        self.assertTrue(top.is_dir())
        if not plat.IS_WIN:
            self.assertNotEqual(str(top), "/")

    def test_disk_usage_answers_and_never_raises(self):
        total, free = plat.disk_usage(Path.home())
        self.assertGreater(total, 0)
        self.assertEqual(plat.disk_usage("Q:/definitely/not/here/xyz"), (0, 0))


class TestNothingElseAsksWhichMachine(unittest.TestCase):
    """The point of plat.py is that it is the only place that knows."""

    def test_windows_only_calls_are_guarded(self):
        for name in ("importer.py", "files.py", "server.py"):
            src = (ROOT / "cullkit" / name).read_text(encoding="utf-8")
            for i, line in enumerate(src.splitlines()):
                if "ctypes.windll" not in line.split("#", 1)[0]:
                    continue
                before = "\n".join(src.splitlines()[max(0, i - 60):i + 1])
                self.assertTrue('os.name != "nt"' in before or "plat.IS_WIN" in before
                                or "except Exception" in before,
                                f"{name}:{i + 1} reaches for Windows without checking")

    def test_the_page_never_writes_a_separator_of_its_own(self):
        page = (ROOT / "cullkit" / "web" / "index.html").read_text(encoding="utf-8")
        bs = chr(92) * 2
        for bad in ("root + '" + bs + "' + name", ".split('" + bs + "').pop()"):
            self.assertNotIn(bad, page)
        self.assertIn('"sep": os.sep', (ROOT / "cullkit" / "server.py").read_text(encoding="utf-8"))

    def test_the_mac_launchers_have_unix_line_endings(self):
        for name in ("setup.command", "Agape Aftershot.command", "cull"):
            raw = (ROOT / name).read_bytes()
            self.assertTrue(raw.startswith(b"#!/bin/bash\n"), name)
            self.assertNotIn(b"\r", raw, name)


if __name__ == "__main__":
    unittest.main()
