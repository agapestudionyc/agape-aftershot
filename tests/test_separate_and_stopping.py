"""Three faults from one real run of Separate Projects: an over-split
algorithm, a completion button that finished nothing, and a stop that could
be pressed twice before the first press had actually landed.
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


class TestSeparateProjectsCallsTheMergeStep(unittest.TestCase):
    """33 proposed groups for four real shoots - the split never looked at
    who was still in front of the camera across a scene-change cut."""

    def test_cmd_shoots_calls_merge_by_people_not_identify_people(self):
        src = CLI.read_text(encoding="utf-8")
        self.assertIn("S.merge_by_people(", src)
        self.assertNotIn("S.identify_people(", src)

    def test_the_gap_used_to_merge_is_the_same_gap_that_split(self):
        # Merging across a real time break would undo a genuine boundary -
        # the same gap_minutes that made the cut is what bounds folding it
        # back, so a real break in the day is never crossed.
        src = CLI.read_text(encoding="utf-8")
        i = src.index("S.merge_by_people(")
        self.assertIn("gap_minutes=args.gap", src[i:i + 120])

    def test_the_final_count_says_when_it_folded_groups_back(self):
        src = CLI.read_text(encoding="utf-8")
        self.assertIn("folded back from", src)


class TestTheMergeUIExists(unittest.TestCase):
    """Even the best split will sometimes be wrong, so naming two proposals
    the same thing has always merged them - this makes that an explicit
    action instead of something you had to already know."""

    def test_every_shoot_gets_a_merge_picker(self):
        page = _page()
        self.assertIn("shoot-merge", page)
        self.assertIn("Merge into", page)

    def test_picking_one_copies_the_name_not_a_live_binding(self):
        page = _page()
        i = page.index("merge.onchange")
        block = page[i:i + 300]
        self.assertIn("nameInputs[sh.index].value = nameInputs[merge.value].value",
                      block)

    def test_who_is_in_each_group_is_still_shown_for_you_to_check(self):
        page = _page()
        i = page.index("async function showShoots(folder, into)")
        self.assertIn("sh.people", page[i:i + 2400])


class TestASoloTaskEndsWhenItsLastJobFinishes(unittest.TestCase):
    """Separate Projects, run on its own, said Done and did nothing else -
    the folders already existed, and the screen still showed the proposal
    for creating them."""

    def test_task_ends_is_defined_and_commit_is_in_it(self):
        page = _page()
        self.assertIn("const TASK_ENDS = {", page)
        i = page.index("const TASK_ENDS = {")
        self.assertIn("commit:", page[i:i + 300])

    def test_work_is_deliberately_not_in_it(self):
        """It already has its own finish screen; duplicating that here would
        be two endings fighting over one job."""
        page = _page()
        i = page.index("const TASK_ENDS = {")
        block = page[i:page.index("};", i)]
        self.assertNotIn("work:", block)

    def test_the_done_button_reads_task_ends_for_the_finishing_job(self):
        page = _page()
        self.assertIn("const ends = TASK_ENDS[(argv || [])[0]];", page)
        i = page.index("const ends = TASK_ENDS[(argv || [])[0]];")
        block = page[i:i + 700]
        self.assertIn("go('home')", block)
        self.assertIn("dismissPanel(logEl)", block)


class TestTheCompletionButtonKnowsWhereItIs(unittest.TestCase):
    """Inside an Aftershot run, "Done" said nothing about the flow bar's own
    Continue button waiting a scroll away for the exact same moment. Asked
    for directly: show the advance right where the result is."""

    def test_a_successful_job_in_a_run_offers_the_next_step(self):
        page = _page()
        i = page.index("const ends = TASK_ENDS[(argv || [])[0]];")
        block = page[i:i + 1400]
        self.assertIn("const chainNext = FLOW.on && !failed;", block)
        self.assertIn("'Next step", block)
        self.assertIn("flowAdvance()", block)

    def test_a_failed_job_never_offers_to_advance(self):
        page = _page()
        i = page.index("const ends = TASK_ENDS[(argv || [])[0]];")
        block = page[i:i + 700]
        self.assertIn("const failed = !!j.rc;", block)
        self.assertIn("chainNext = FLOW.on && !failed", block)
        self.assertIn("taskEnds = !!ends && !FLOW.on && !failed", block)

    def test_advancing_only_happens_if_the_flow_bar_agrees_it_is_ready(self):
        # flowAdvance() assumes the current stage is actually finished;
        # calling it on a stage that is not ready is the same class of bug
        # as the flow bar's own button not checking FLOW.ready.
        page = _page()
        i = page.index("doneBtn.onclick = () => {")
        block = page[i:i + 400]
        self.assertIn("if (FLOW.ready) flowAdvance();", block)


class TestStoppingCoversTheScreenUntilItActuallyLands(unittest.TestCase):
    """End task, pressed twice, with the pages glitching and disagreeing in
    between - BUSY dropped to zero the instant a stop was asked for, while
    the job itself does not notice until the file it is on finishes. Two
    presses landed in that exact gap."""

    def test_the_overlay_markup_exists(self):
        page = _page()
        self.assertIn('id="stopping"', page)

    def test_stop_my_jobs_shows_it_before_asking_and_hides_it_after(self):
        page = _page()
        i = page.index("async function stopMyJobs(")
        block = page[i:page.index("\nfunction stoppingOverlay", i)]
        self.assertIn("stoppingOverlay(true)", block)
        self.assertIn("stoppingOverlay(false)", block)
        # Shown before the network call, not after - it has to be up before
        # the gap it is covering begins.
        self.assertLess(block.index("stoppingOverlay(true)"),
                        block.index("fetch('/api/job/stop'"))

    def test_it_hides_even_when_the_request_fails(self):
        # A finally, not a plain call at the end - a failed fetch or a
        # timed-out wait must not leave the screen covered forever.
        page = _page()
        i = page.index("async function stopMyJobs(")
        block = page[i:page.index("\nfunction stoppingOverlay", i)]
        self.assertEqual(block.count("finally"), 2)

    def test_ending_a_task_actually_waits_for_the_stop(self):
        """The exact call site reported: End task on a standalone job."""
        page = _page()
        i = page.index("async function flowStop()")
        block = page[i:i + 1500]
        self.assertIn("await stopMyJobs()", block)

    def test_every_way_of_interrupting_a_job_goes_through_one_place(self):
        """Asked for directly: not just End task - any scenario that
        interrupts. The overlay lives inside stopMyJobs() itself rather than
        at each call site, so this is true by construction as long as
        nothing else asks the server to stop a job on its own."""
        page = _page()
        stop_calls = page.count("/api/job/stop")
        # stopMyJobs()'s own request, plus the pagehide beacon - a page
        # actually unloading has nothing left to show an overlay on, and is
        # the one interruption this deliberately does not cover. And Force
        # stop, whose button is on the curtain itself - it is only ever
        # pressed once the stop has been asked for and the screen covered.
        self.assertEqual(stop_calls, 3)
        self.assertIn("navigator.sendBeacon('/api/job/stop'", page)
        i = page.index("async function forceStop()")
        self.assertIn("/api/job/stop", page[i:i + 800])
        j = page.index('<div id="stopping">')
        self.assertIn('id="stop-force"', page[j:j + 800])

    def test_redo_and_leaving_mid_job_both_go_through_it_too(self):
        page = _page()
        self.assertIn("async function flowRedo()", page)
        i = page.index("async function flowRedo()")
        self.assertIn("await stopMyJobs()", page[i:i + 3000])
        j = page.index("function mayLeaveWork()")
        self.assertIn("stopMyJobs()", page[j:j + 700])


if __name__ == "__main__":
    unittest.main()
