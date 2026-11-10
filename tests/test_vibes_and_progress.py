"""Two things found from a real evening of using the app: the transport row
still wore a colour left over from when there could only be three vibes, and
a long stage of Separate Projects had nothing at all to say about itself
while it worked.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "cullkit" / "web" / "index.html"
CLI = Path(__file__).resolve().parent.parent / "cullkit" / "cli.py"
SESSIONS = Path(__file__).resolve().parent.parent / "cullkit" / "sessions.py"


def _page():
    return WEB.read_text(encoding="utf-8")


class TestTheTransportRowWearsTheRealColour(unittest.TestCase):
    """He changed a vibe's colour and the round button followed it - the
    prev/pause/next row next to it stayed orange, because it was still
    reading three CSS rules keyed to a vibe's *slot number*
    (`#vibe-transport[data-mode="1"]{--tint:#ffb84d}`) rather than the vibe's
    actual configured colour. A fourth vibe had no rule here at all."""

    def test_the_hardcoded_slot_colours_are_gone(self):
        page = _page()
        self.assertNotIn('#vibe-transport[data-mode="1"]{--tint', page)
        self.assertNotIn('#vibe-transport[data-mode="2"]{--tint', page)
        self.assertNotIn('#vibe-transport[data-mode="3"]{--tint', page)

    def test_paint_transport_sets_tint_from_the_actual_vibe(self):
        page = _page()
        i = page.index("function paintTransport()")
        block = page[i:i + 1600]
        self.assertIn("row.style.setProperty('--tint'", block)
        self.assertIn("vibeAt(vibeMode)", block)

    def test_painting_the_button_keeps_the_transport_in_step(self):
        # A colour edit calls saveVibes() -> paintVibe(); if paintVibe()
        # never told the transport row, the two would drift again the next
        # time only one of them was repainted.
        page = _page()
        i = page.index("function paintVibe()")
        self.assertIn("paintTransport();", page[i:i + 1500])


class TestSeparateProjectsSaysSomethingWhileItWorks(unittest.TestCase):
    """He watched it "quedarse cargando" with no way to tell whether it was
    working or stuck - the face-grouping pass that folds over-split groups
    back together printed nothing at all while it ran, for as many frames as
    there were provisional groups."""

    def test_merge_by_people_reports_progress(self):
        src = SESSIONS.read_text(encoding="utf-8")
        i = src.index("def merge_by_people(")
        sig = src[i:src.index(")", i)]
        self.assertIn("on_progress=None", sig)
        body = src[i:i + 3200]
        self.assertIn("on_progress(si + 1, len(shoots))", body)

    def test_cli_wires_the_progress_through(self):
        src = CLI.read_text(encoding="utf-8")
        i = src.index("S.merge_by_people(")
        block = src[i:i + 300]
        self.assertIn("on_progress=lambda i, n: echo(f\"  {i}/{n}\")", block)

    def test_a_long_silent_stage_says_so_out_loud(self):
        page = _page()
        i = page.index("const heartbeat = setInterval(")
        block = page[i:i + 1400]
        self.assertIn("stalled === 60", block)
        self.assertIn("taking", block.lower())
        self.assertIn("logEvent('ui.stage-slow'", block)


class TestTheArchiveShortcutActuallySelects(unittest.TestCase):
    """"Al darle click no autoseleccionó mi folder." The button only set an
    internal variable and refreshed a text preview - the picker widget it
    sits above still showed whatever it had been showing, with nothing on
    screen that looked like a folder had been chosen."""

    def test_the_picker_exposes_a_way_to_drive_it(self):
        page = _page()
        self.assertIn("host._chooseFolder = choose;", page)

    def test_the_button_drives_the_real_picker_not_just_a_variable(self):
        page = _page()
        i = page.index("$('#imp-archive').onclick = () => {")
        block = page[i:i + 700]
        self.assertIn("dest._chooseFolder(root)", block)


class TestAftershotAdvancesItselfWhereNothingIsBeingDecided(unittest.TestCase):
    """Asked for directly, after watching the chain wait for a tap between
    every stage that had already succeeded: Import needs him to choose
    folders, and Separate Projects needs him to name and confirm groups -
    those are real decisions. Nothing about a checksum matching, or a sift's
    verdict, is a decision anybody is being asked to make, so the chain no
    longer stops to ask permission for either."""

    def test_a_clean_import_inside_a_run_advances_on_its_own(self):
        page = _page()
        i = page.index("$('#imp-go').onclick = () => {")
        block = page[i:i + 1200]
        self.assertIn("if (FLOW.on) {", block)
        # The old version waited on {ready: true, next: '...'} and a press of
        # Continue. This calls the same advance the button used to wait for.
        j = block.index("if (FLOW.on) {")
        chain_block = block[j:j + 900]
        self.assertIn("flowAdvance();", chain_block)

    def test_sift_inside_a_run_applies_and_advances_without_a_click(self):
        page = _page()
        i = page.index("async function flowSift()")
        block = page[i:i + 1400]
        # --apply, not just --sheet: the flagged frames actually move.
        self.assertIn("'quality', f, '--sheet', '--apply'", block)
        self.assertIn("flowAdvance();", block)

    def test_a_failed_sift_still_stops_and_waits(self):
        # Advancing past a step that did not finish would be the one thing
        # worse than asking too often.
        page = _page()
        i = page.index("async function flowSift()")
        block = page[i:i + 1400]
        self.assertIn("if (j && j.rc) {", block)
        self.assertIn("ready: true,", block[block.index("if (j && j.rc)"):])

    def test_separate_projects_itself_still_waits_for_a_person(self):
        # The one stage that is a real decision - naming and confirming the
        # proposed groups - must not have picked up auto-advance too.
        page = _page()
        i = page.index("if (FLOW.at === 0) {")
        block = page[i:i + 700]
        self.assertIn("ready: true, next: 'Folders are made →'", block)
        self.assertNotIn("flowAdvance();", block)


class TestASoloJobOffersTheSameNextStopAftershotWould(unittest.TestCase):
    """"Que tenga el botón Done como ya configuramos pero también un botón
    que diga el siguiente step, usando el SOP de Aftershot" - a job started
    on its own gets the same next stop the full run would have taken it to,
    offered next to Done rather than forced."""

    def test_import_offers_separate_projects_on_the_folder_it_made(self):
        page = _page()
        i = page.index("const SOLO_NEXT = {")
        block = page[i:page.index("};", i)]
        self.assertIn("import:", block)
        self.assertIn("'Separate Projects →'", block)
        self.assertIn("importDestFromArgv(argv)", block)

    def test_sift_offers_the_darkroom_on_the_same_folder(self):
        page = _page()
        i = page.index("const SOLO_NEXT = {")
        block = page[i:page.index("};", i)]
        self.assertIn("quality:", block)
        self.assertIn("'Into the darkroom →'", block)
        self.assertIn("devEnter(folder)", block)

    def test_separate_projects_is_not_in_it(self):
        # commit can leave several new folders behind - there is no single
        # unambiguous next stop to offer, so it is not offered one.
        page = _page()
        i = page.index("const SOLO_NEXT = {")
        block = page[i:page.index("};", i)]
        self.assertNotIn("commit:", block)

    def test_the_button_only_shows_up_once_a_folder_can_be_resolved(self):
        page = _page()
        self.assertIn("soloCand.ready(argv)", page)

    def test_it_never_shows_up_inside_a_run_or_after_a_failure(self):
        page = _page()
        i = page.index("const soloCand = !FLOW.on && !failed && SOLO_NEXT")
        self.assertGreaterEqual(i, 0)


if __name__ == "__main__":
    unittest.main()
