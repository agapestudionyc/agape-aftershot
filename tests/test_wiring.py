"""Every job the app can start must be wired up the same way.

Most of what went wrong on 2026-08-22 was one shape of mistake: a behaviour was
built for the job in front of us and quietly missed the others. Sift got a
folded folder picker and Import did not; adopting a Sift drew its result and
adopting anything else drew nothing; a job with no entry in `JOB_LABEL` was
announced to the phone as "Working".

Those are not bugs you find by looking at the screen, because the screen you
are looking at is the one that works. They are found by asking the same
question of every command at once, which is what this file does.

It reads `index.html` as text on purpose. There is no JavaScript runtime here,
and the alternative - trusting that the next person remembers - is what put the
holes there in the first place.
"""
import os

# Before any import of `cullkit`: the suite runs real jobs, and the app now
# journals every one. Without this they are filed in the user's own record,
# next to a real afternoon's work. Set here rather than only in the package
# `__init__`, which `unittest discover` does not import.
os.environ["CULLKIT_NO_JOURNAL"] = "1"

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "cullkit" / "web" / "index.html"
CLI = Path(__file__).resolve().parent.parent / "cullkit" / "cli.py"


def _page():
    return WEB.read_text(encoding="utf-8")


def _map_keys(name, src):
    """The keys of a `const NAME = { ... };` object literal."""
    start = src.index("const " + name)
    depth, i, seen = 0, src.index("{", start), 0
    j = i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    body = src[i + 1:j]
    # Top-level keys only, and several may share a line - JOB_LABEL puts four
    # on one. So walk it rather than reading it line by line, tracking both the
    # nesting depth and whether we are inside a quoted value.
    out, depth, quote, k = [], 0, "", 0
    while k < len(body):
        c = body[k]
        if quote:
            if c == quote and body[k - 1] != "\\":
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif depth == 0:
            m = re.match(r"([A-Za-z_]\w*)\s*:", body[k:])
            if m and (k == 0 or not re.match(r"[\w.]", body[k - 1])):
                out.append(m.group(1))
                k += m.end() - 1
        k += 1
    return out


def _cli_commands():
    """Every command the CLI actually offers."""
    return set(re.findall(r'sub\.add_parser\("([a-z-]+)"', CLI.read_text(encoding="utf-8")))


class TestEveryJobIsWiredTheSameWay(unittest.TestCase):
    """The commands the interface can launch, and what each one needs.

    Update this list when a new one is added — that is the point of it. A
    command missing from the maps below does not crash; it degrades quietly,
    which is worse.
    """

    LAUNCHED_BY_THE_UI = {
        "quality",   # Sift
        "shoots",    # Separate Projects
        "commit",    # ...and creating the folders it proposes
        "import",    # Import & Verify, and Aftershot's first step
        "preview",   # the darkroom, warming previews
        "poses",     # the darkroom, grouping near-identical runs
        "work",      # moving your picks out
        "clean",     # clearing cached previews
    }

    def setUp(self):
        self.src = _page()
        self.labels = set(_map_keys("JOB_LABEL", self.src))
        self.homes = set(_map_keys("JOB_HOME", self.src))

    def test_every_job_has_a_name_a_person_would_recognise(self):
        """Without one it is announced as "Working" — on the phone, in a
        notification, about a job that is moving photographs."""
        missing = sorted(self.LAUNCHED_BY_THE_UI - self.labels)
        self.assertEqual(missing, [], f"no JOB_LABEL for: {missing}")

    def test_every_job_can_be_picked_up_on_another_device(self):
        """No entry means the second device can only say "it is running on the
        computer" — no log, no progress, and no way to stop it from there."""
        missing = sorted(self.LAUNCHED_BY_THE_UI - self.homes)
        self.assertEqual(missing, [], f"no JOB_HOME for: {missing}")

    def test_a_job_that_can_be_picked_up_can_also_be_named(self):
        missing = sorted(self.homes - self.labels)
        self.assertEqual(missing, [], f"in JOB_HOME but unnamed: {missing}")

    def test_the_maps_only_mention_commands_that_exist(self):
        """A typo here fails silently: the lookup misses and the job falls back
        to the generic wording, which reads like a design decision."""
        real = _cli_commands()
        for name, keys in (("JOB_LABEL", self.labels), ("JOB_HOME", self.homes)):
            unknown = sorted(k for k in keys if k not in real)
            self.assertEqual(unknown, [], f"{name} names commands the CLI does "
                                          f"not have: {unknown}")

    def test_every_home_names_a_screen_and_a_log_panel(self):
        block = self.src[self.src.index("const JOB_HOME"):]
        block = block[:block.index("\n};")]
        # Anchored on each key, because a bare {…} also matches the little
        # arrow-function bodies inside an entry.
        starts = [(m.group(1), m.start()) for m in
                  re.finditer(r"^\s{2}([a-z]+):\s*\{", block, re.M)]
        self.assertTrue(starts, "no entries found at all")
        for i, (key, at) in enumerate(starts):
            end = starts[i + 1][1] if i + 1 < len(starts) else len(block)
            entry = block[at:end]
            self.assertIn("view:", entry, f"{key} names no screen")
            self.assertIn("log:", entry, f"{key} names no log panel")

    def test_the_log_panels_and_pickers_actually_exist_on_the_page(self):
        """A selector that matches nothing makes adoption fall back to the
        "it is running on the computer" message, silently and forever."""
        block = self.src[self.src.index("const JOB_HOME"):]
        block = block[:block.index("\n};")]
        for sel in set(re.findall(r"'(#[a-z-]+)'", block)):
            self.assertIn(f'id="{sel[1:]}"', self.src,
                          f"{sel} is not an element on the page")


class TestEveryPickerFoldsWhenAnswered(unittest.TestCase):
    """The folder list closing once you have chosen is not per-screen
    behaviour; it happens in `choose()`, which every picker shares. This guards
    the shape of that, since moving it into any one caller is how it would come
    to apply to one job and not the rest."""

    def test_the_fold_lives_in_the_shared_chooser(self):
        src = _page()
        start = src.index("function choose(path)")
        body = src[start:start + 900]
        self.assertIn("PICKED[id] = path", body)
        self.assertIn("lockPicker(id)", body)

    def test_the_darkroom_picker_is_the_only_one_excluded(self):
        """It folds itself into its three doors already. Any other exclusion
        would mean a job whose folder list stays open under a running bar."""
        src = _page()
        start = src.index("function choose(path)")
        body = src[start:start + 900]
        excluded = re.findall(r"id !== '(#[a-z-]+)'", body)
        self.assertEqual(excluded, ["#pick-dev"])


class TestEveryPanelCanBePutAway(unittest.TestCase):
    """Two jobs' answers on one screen, with no way to dismiss either.

    That was reported against Sift, and the first fix was a two-line lookup
    naming Sift's result host and Separate Projects'. Same mistake in a smaller
    font: there are seven log panels on this page and any of them can leave
    something behind. So each one names itself and its result in the markup —
    and this is what stops the next panel being added without them.
    """

    def setUp(self):
        self.src = _page()
        self.logs = re.findall(r"<pre class=\"log[^\"]*\"([^>]*)>", self.src)

    def test_every_panel_says_what_it_is(self):
        """The header reads "Working" without one — on a job that is quite
        specifically moving photographs."""
        nameless = [a for a in self.logs if "data-name=" not in a]
        self.assertEqual(nameless, [], f"log panels with no data-name: {nameless}")

    def test_every_named_result_host_is_real(self):
        """A selector that matches nothing fails silently: the old result is
        simply never cleared, which is the bug this was written for."""
        for attrs in self.logs:
            m = re.search(r'data-result="(#[a-z-]+)"', attrs)
            if m:
                self.assertIn(f'id="{m.group(1)[1:]}"', self.src,
                              f"{m.group(1)} is not an element on the page")

    def test_the_panel_carries_both_controls(self):
        """Fold it away, or take it off the screen. Built once in
        `progressFor`, so every panel gets them or none does."""
        start = self.src.index("function progressFor(logEl)")
        body = self.src[start:start + 1600]
        self.assertIn("pbtn pmin", body, "no fold control")
        self.assertIn("pbtn pclose", body, "no remove control")
        self.assertIn("dismissPanel(logEl)", body)

    def test_a_running_job_cannot_be_tidied_away(self):
        """Making a running job's panel vanish leaves it running with nothing
        on screen able to stop it."""
        start = self.src.index("function dismissPanel(logEl)")
        body = self.src[start:start + 700]
        # Anchored, not merely present. `if (false && RUNNING_PANELS...)` still
        # contains the words and would have sailed through a substring check -
        # which it did, the first time this was written.
        self.assertRegex(body, r"(?m)^  if \(RUNNING_PANELS\.has\(logEl\)\)$")

    def test_starting_a_job_clears_the_screen_it_lands_on(self):
        """Its own leftovers and the panel next to it. `v-import` holds two
        log panels and so does `v-dev`; before this, one job's numbers could
        sit directly above another's."""
        start = self.src.index("async function run(argv, logEl")
        body = self.src[start:start + 4000]
        # Both must be live statements. Commenting one out leaves the words on
        # the page, so match the whole line from its own indent.
        self.assertRegex(body, r"(?m)^  clearResultFor\(logEl\);")
        self.assertRegex(body, r"(?m)^  clearOtherPanels\(logEl\);")

    def test_nothing_is_cleared_out_from_under_something_using_it(self):
        """The darkroom is moved inside the run's stage during Develop, and
        the Sift result contains a panel of its own. Emptying either host
        would tear a live thing off the page."""
        self.assertIn("!host.querySelector('#v-dev')", self.src)
        self.assertIn("host.contains(logEl)", self.src)


if __name__ == "__main__":
    unittest.main()
