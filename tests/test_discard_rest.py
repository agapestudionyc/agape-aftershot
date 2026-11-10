"""After the darkroom moves the picks out, the rest of the shoot goes to the
discards - as one folder, on the same drive, with an undo journal - and only
when that cannot take a pick with it.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cullkit import actions, cli, ledger
from tests.make_fixtures import build


class DiscardRest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cullkit-test-"))
        self.shoot = build(self.root)
        self.work = self.root / "TRABAJO"
        self.bin = self.root / "_DESCARTABLES"
        self._ledger, self._config = ledger.LEDGER, ledger.CONFIG
        ledger.LEDGER = self.root / "ledger.json"
        ledger.CONFIG = self.root / "config.json"
        ledger.record_shoot(self.shoot)
        ledger.record_cache(self.shoot)

    def tearDown(self):
        ledger.LEDGER, ledger.CONFIG = self._ledger, self._config
        shutil.rmtree(self.root, ignore_errors=True)

    def work_cmd(self, *extra):
        argv = ["work", str(self.shoot), "1200", "1210",
                "--dest", str(self.work), "--into", "shoot Selects",
                "--discard-rest", "--discards", str(self.bin), *extra]
        args = cli.build_parser().parse_args(argv)
        return args.func(args)

    def test_dry_run_moves_nothing(self):
        self.assertEqual(self.work_cmd(), 0)
        self.assertTrue(self.shoot.is_dir())
        self.assertFalse(self.bin.exists())

    def test_rest_goes_to_the_discards_and_comes_back(self):
        self.assertEqual(self.work_cmd("--apply"), 0)
        picks = self.work / "shoot Selects"
        self.assertTrue((picks / "DSC_1200.NEF").is_file())
        self.assertTrue((picks / "DSC_1210.NEF").is_file())
        self.assertFalse(self.shoot.exists())
        moved = self.bin / "shoot"
        self.assertTrue((moved / "DSC_1201.NEF").is_file())
        self.assertFalse((moved / "DSC_1200.NEF").exists())
        # the ledger follows the folder
        paths = [s["path"] for s in ledger.load()["shoots"]]
        self.assertIn(str(moved.resolve()), paths)
        self.assertIn(str(moved.resolve()), ledger.load()["caches"])
        # and it can be put back
        journal = next((moved / "_cullkit").glob("undo-*-discards.json"))
        actions.undo(journal, apply=True)
        self.assertTrue((self.shoot / "DSC_1201.NEF").is_file())
        self.assertFalse(moved.exists())

    def test_never_when_the_selection_goes_inside_the_shoot(self):
        argv = ["work", str(self.shoot), "1200", "--dest", str(self.shoot),
                "--into", "Selects", "--discard-rest", "--discards", str(self.bin),
                "--apply"]
        args = cli.build_parser().parse_args(argv)
        self.assertEqual(args.func(args), 0)
        self.assertTrue((self.shoot / "Selects" / "DSC_1200.NEF").is_file())
        self.assertFalse(self.bin.exists())

    def test_never_when_copying(self):
        self.assertEqual(self.work_cmd("--copy", "--apply"), 0)
        self.assertTrue((self.shoot / "DSC_1200.NEF").is_file())
        self.assertFalse(self.bin.exists())

    def test_name_taken_in_the_discards(self):
        (self.bin / "shoot").mkdir(parents=True)
        self.assertEqual(self.work_cmd("--apply"), 0)
        self.assertTrue((self.bin / "shoot (2)" / "DSC_1201.NEF").is_file())


if __name__ == "__main__":
    unittest.main()
