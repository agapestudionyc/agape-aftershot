"""Asked for directly, twice: first that pressing Overview on a phone should
look like the room grows out of the button rather than swapping instantly,
then - after seeing it on a real phone - that the growth was drifting toward
the middle of the screen instead of the button, and that the outside of the
circle read as flat black instead of glass, with no light at its edge.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "cullkit" / "web" / "index.html"


def _page():
    return WEB.read_text(encoding="utf-8")


class TestOverviewBubblesOpenAndShut(unittest.TestCase):

    def test_both_directions_exist(self):
        page = _page()
        self.assertIn("function bubbleOpenHome(btn)", page)
        self.assertIn("function bubbleCloseHome(btn, to)", page)

    def test_the_glass_and_ring_elements_exist(self):
        page = _page()
        self.assertIn('<div id="bubble-glass"></div>', page)
        self.assertIn('<div id="bubble-ring"></div>', page)
        self.assertIn("#bubble-glass{", page)
        self.assertIn("#bubble-ring{", page)

    def test_the_glass_is_never_a_flat_colour(self):
        # He saw solid black outside the circle - that was just --paper
        # showing through an unmasked area, not an intentional layer. The
        # fix is a real frosted-glass background plus a blur, not a colour.
        page = _page()
        i = page.index("#bubble-glass{")
        block = page[i:i + 300]
        self.assertIn("var(--glass)", block)
        self.assertIn("backdrop-filter", block)
        for theme_block_start in ("--glass:rgba(255,255,255,.5);",
                                  "--glass:rgba(10,10,10,.42);"):
            self.assertIn(theme_block_start, page)

    def test_the_ring_glows_in_the_system_green(self):
        page = _page()
        i = page.index("#bubble-ring{")
        block = page[i:i + 300]
        self.assertIn("var(--accent)", block)
        self.assertIn("box-shadow", block)

    def test_origin_is_read_from_the_button_alone(self):
        page = _page()
        i = page.index("function bubbleOrigin(btn)")
        block = page[i:i + 300]
        self.assertIn("btn.getBoundingClientRect()", block)
        self.assertIn("cx: br.left + br.width / 2", block)
        self.assertIn("cy: br.top + br.height / 2", block)

    def test_origin_is_read_before_go_can_reshuffle_the_pill(self):
        # The pill shows a different set of buttons depending on the screen,
        # so navigating first and measuring the button afterwards means
        # measuring it in the *destination* screen's pill layout, not the one
        # that was actually on screen when it was pressed - which is exactly
        # what sent an earlier version of this drifting off the button.
        page = _page()
        i = page.index("function bubbleOpenHome(btn)")
        block = page[i:i + 400]
        self.assertLess(block.index("bubbleOrigin(btn)"),
                        block.index("go('home');                         // instant"))
        j = page.index("function bubbleCloseHome(btn, to)")
        blockc = page[j:j + 400]
        self.assertIn("bubbleOrigin(btn)", blockc)

    def test_the_hole_is_a_mask_not_a_clip_on_the_real_screen(self):
        page = _page()
        i = page.index("function bubbleRun(")
        block = page[i:i + 1400]
        self.assertIn("maskImage", block)
        self.assertIn("webkitMaskImage", block)
        self.assertIn("radial-gradient(circle at", block)

    def test_closing_replays_the_same_envelope_backwards(self):
        # "en reversa" is meant literally: one easing curve, one ring
        # envelope, read forwards for opening and with (1 - progress) for
        # closing - not a second, independently-tuned close animation.
        page = _page()
        self.assertIn("bubbleEase(opening ? p : 1 - p)", page)
        self.assertIn("bubbleRingLight(opening ? p : 1 - p)", page)

    def test_the_sidebar_gets_no_bubble(self):
        page = _page()
        i = page.index("function bubbleOpenHome(btn)")
        self.assertIn("RAIL.matches", page[i:i + 250])
        j = page.index("function bubbleCloseHome(btn, to)")
        self.assertIn("RAIL.matches", page[j:j + 250])

    def test_reduced_motion_still_gets_the_swap(self):
        page = _page()
        i = page.index("function bubbleOpenHome(btn)")
        block = page[i:page.index("function bubbleCloseHome")]
        self.assertIn("prefers-reduced-motion", block)
        self.assertIn("go('home')", block)

    def test_closing_navigates_only_after_the_hole_finishes_shutting(self):
        page = _page()
        i = page.index("function bubbleCloseHome(btn, to)")
        block = page[i:i + 400]
        self.assertIn("bubbleRun(origin, false, 420, () => go(to));", block)

    def test_entering_overview_goes_through_the_bubble(self):
        page = _page()
        i = page.index("document.querySelectorAll('nav button').forEach(b =>")
        block = page[i:i + 1200]
        self.assertIn("return bubbleOpenHome(b);", block)

    def test_leaving_overview_by_its_own_button_goes_through_the_bubble(self):
        # Every guard (idle run, a run in progress, mayLeaveWork) still runs
        # first - the bubble only replaces the final go(to), same as before.
        page = _page()
        i = page.index("document.querySelectorAll('nav button').forEach(b =>")
        block = page[i:i + 1200]
        self.assertIn("leavingHome ? bubbleCloseHome(b, to) : go(to)", block)
        self.assertIn("if (leavingHome) return bubbleCloseHome(b, to);", block)


if __name__ == "__main__":
    unittest.main()
