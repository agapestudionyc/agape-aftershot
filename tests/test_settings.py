"""Settings: one door, one way out, and nothing left calling an element that
is no longer on the page.

Everything here comes from the first real session of using the settings
screen. Two of these were found by trying it, not by reading it: a plain
`$('#id').onclick = ...` against an id that had just been deleted from the
markup throws at script-load time and would have taken the whole page down
with it, silently, the next time anybody opened the app. The other was the
navigation-stack bug itself - Settings borrowing the pill's Overview/Back
toggle, which is built for switching between jobs and has no business
deciding where "back" lands from a page that isn't one.
"""
import os

os.environ["CULLKIT_NO_JOURNAL"] = "1"

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "cullkit" / "web" / "index.html"
SERVER = Path(__file__).resolve().parent.parent / "cullkit" / "server.py"


def _page():
    return WEB.read_text(encoding="utf-8")


def _server():
    return SERVER.read_text(encoding="utf-8")


class TestEveryIdOnClickHasAnElement(unittest.TestCase):
    """The regression that would have broken the whole page.

    `$('#foo').onclick = ...` against a selector that matches nothing sets a
    property on `null` and throws - and because this is one `<script>` block,
    every top-level statement after it never runs. Removing a button from the
    markup without removing its handler is exactly how that happens, and it
    happened three times in one pass while this file was being rebuilt.
    """

    def test_no_click_handler_targets_a_missing_id(self):
        page = _page()
        ids_in_markup = set(re.findall(r'\bid="([\w-]+)"', page))
        handled = set(re.findall(r"\\$\('#([\w-]+)'\)\.on(?:click|change)\s*=", page))
        missing = sorted(h for h in handled if h not in ids_in_markup)
        self.assertFalse(missing, f"handlers registered with no matching element: {missing}")


class TestSettingsIsNotPartOfTheJobPill(unittest.TestCase):
    """Settings has exactly one way in (the button on Overview) and exactly
    one way out (its own Back button in the settings header). It used to
    borrow the floating pill's Overview icon as well, and that pill's
    "tap Overview again to toggle back" logic - built for hopping between
    Sift and Import - would then set `PREV_VIEW = 'settings'`. A second tap
    on Overview, read as "back", landed back on Settings instead of wherever
    the pill's Back button actually promises to go."""

    def test_paint_nav_hides_the_whole_pill_in_settings(self):
        page = _page()
        i = page.index("function paintNav(view) {")
        block = page[i:i + 1600]
        self.assertIn("inSettings", block)
        self.assertIn("$('#tab-new').classList.toggle('hide', inSettings", block)
        self.assertIn("$('#tab-back').classList.toggle('hide',\n    inSettings", block)
        self.assertIn("$('#tab-home').classList.toggle('hide', inSettings", block)

    def test_settings_has_its_own_single_exit(self):
        page = _page()
        self.assertIn('id="settings-back"', page)
        self.assertIn("$('#settings-back').onclick = () => go('home');", page)

    def test_entering_settings_does_not_go_through_the_pill(self):
        page = _page()
        # go-settings is its own button, not one of the `nav button` elements
        # the PREV_VIEW toggle logic is attached to.
        i = page.index('id="go-settings"')
        self.assertNotIn('data-v="settings"', page[max(0, i - 200):i + 200])
        self.assertNotIn('data-v="settings"', page)


class TestArchiveIsTheOnlyFolderSetting(unittest.TestCase):
    """`work_root` and a separate "quick" folder were tried and asked to be
    removed - the archive is the only folder worth a quick setting, and the
    working folder stays exactly where `cull config --work-root` left it."""

    def test_only_one_folder_row(self):
        page = _page()
        self.assertEqual(page.count('data-setfolder='), 1)
        self.assertIn('data-setfolder="archive_root"', page)
        self.assertNotIn("cfg-work", page)
        self.assertNotIn("cfg-quick", page)
        self.assertNotIn("quick_folder", page)

    def test_the_quick_button_reads_archive_root_directly(self):
        page = _page()
        i = page.index("function paintQuickButton()")
        block = page[i:i + 500]
        self.assertIn("STATE.config.archive_root", block)
        self.assertNotIn("quick_folder", block)

    def test_the_server_only_accepts_archive_root(self):
        src = _server()
        i = src.index('if path == "/api/settings":')
        block = src[i:i + 900]
        code = "\n".join(ln for ln in block.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertIn('"archive_root" in data', code)
        self.assertNotIn('"work_root"', code)
        self.assertNotIn('"quick_folder"', code)
        self.assertNotIn('"gap_minutes"', code)


class TestSeparationTuningIsNotAUserSetting(unittest.TestCase):
    """Andres didn't like the recommendation - the algorithm decides the gap
    and the scene threshold, and there is no manual override in Settings."""

    def test_no_gap_or_scene_controls(self):
        page = _page()
        self.assertNotIn("cfg-gap", page)
        self.assertNotIn("cfg-scene", page)
        self.assertNotIn("How projects are separated", page)


class TestNotificationsAreASwitch(unittest.TestCase):
    def test_a_real_toggle_exists(self):
        page = _page()
        self.assertIn('id="push-toggle"', page)
        self.assertIn('class="switch"', page)
        self.assertIn("$('#push-toggle').onclick", page)

    def test_the_test_button_sits_under_the_switch(self):
        page = _page()
        i = page.index('id="push-switch"')
        j = page.index('id="push-test"')
        self.assertLess(i, j)


class TestVibesShowWhatIsActuallyConfigured(unittest.TestCase):
    """Three vibes already exist. Opening Settings has to show those three,
    with their real playlists, not a blank slate waiting to be told about
    them - and each one needs a way to lose a single track, empty its whole
    playlist, or be deleted outright."""

    def test_the_editor_lists_every_configured_vibe(self):
        page = _page()
        self.assertIn("function paintVibeEditor()", page)
        i = page.index("function paintVibeEditor()")
        self.assertIn("VIBE_DEFS.forEach((v, i) => host.appendChild(", page[i:i + 400])

    def test_the_resting_state_is_one_line_not_a_form(self):
        """Andres asked for this directly: the vibes box was too expanded.
        Only the vibe being edited gets the full card - everything else is a
        single compact row with Edit and Delete."""
        page = _page()
        self.assertIn("function vibeRow(v, i)", page)
        i = page.index("function paintVibeEditor()")
        self.assertIn("v.id === VIBE_EDITING ? vibeCard(v, i) : vibeRow(v, i)",
                      page[i:i + 400])
        j = page.index("function vibeRow(v, i)")
        row = page[j:j + 1200]
        self.assertIn("'act ghost', 'Edit'", row)
        self.assertIn("'act ghost', 'Delete'", row)

    def test_editing_one_vibe_does_not_open_a_second_form(self):
        page = _page()
        self.assertIn("let VIBE_EDITING = null;", page)
        # Opening one sets the single slot; there is no list of open ones.
        self.assertIn("VIBE_EDITING = v.id; paintVibeEditor();", page)
        # And there is a way back to the row.
        i = page.index("function vibeCard(v, i)")
        self.assertIn("'act ghost', 'Done'", page[i:i + 2200])

    def test_the_said_line_has_a_visible_limit(self):
        page = _page()
        self.assertIn("const VIBE_SAYS_MAX", page)
        i = page.index("function vibeCard(v, i)")
        block = page[i:i + 1600]
        self.assertIn("says.maxLength = VIBE_SAYS_MAX", block)
        self.assertIn("VIBE_SAYS_MAX} characters", block)

    def test_builtin_tracks_are_listed_by_name_not_just_counted(self):
        page = _page()
        i = page.index("function vibeCard(v, i)")
        block = page[i:i + 4000]
        self.assertIn("(v.tracks || []).forEach(t => {", block)
        self.assertIn("prettyTrack(t.name)", block)

    def test_three_ways_to_remove_music(self):
        page = _page()
        i = page.index("function vibeCard(v, i)")
        block = page[i:i + 5000]
        # One track - a small × beside it, not a text button that would
        # crowd a list of several.
        self.assertIn("'trk-x', '×'", block)
        self.assertIn("/api/vibes/forget-track", block)
        # The whole playlist, vibe kept.
        self.assertIn("Clear this playlist", block)
        self.assertIn("/api/vibes/clear-tracks", block)
        # The vibe itself - reachable straight from the collapsed row too,
        # not only from inside the open editor.
        self.assertIn("async function removeVibe(v, i)", page)
        self.assertIn("removeVibe(v, i)", block)

    def test_clearing_a_shipped_vibes_playlist_restores_the_defaults(self):
        page = _page()
        self.assertIn("SHIPPED_VIBE_IDS", page)
        i = page.index("Clear this playlist")
        self.assertIn("SHIPPED_VIBE_IDS.includes(v.id)", page[i - 200:i + 900])

    def test_the_server_has_a_clear_tracks_route_that_keeps_the_vibe(self):
        src = _server()
        self.assertIn('if path == "/api/vibes/clear-tracks":', src)
        i = src.index('if path == "/api/vibes/clear-tracks":')
        block = src[i:i + 400]
        self.assertIn("V.forget_all(vid)", block)
        # Only the music goes - never the vibe's own settings file.
        self.assertNotIn("V.save(", block)


class TestTheActivityLogIsHandedOverNotRead(unittest.TestCase):
    def test_exactly_one_button(self):
        page = _page()
        i = page.index('<h2>Activity log</h2>')
        card_end = page.index('</div>', page.index('</div>', i))
        block = page[i:card_end]
        self.assertEqual(block.count('<button'), 1)
        self.assertIn('id="jr-save"', block)

    def test_it_does_not_depend_on_anything_shown_on_screen_first(self):
        page = _page()
        i = page.index("$('#jr-save').onclick")
        block = page[i:i + 500]
        self.assertIn("/api/journal?limit=5000", block)
        self.assertNotIn("JR_ROWS", block)


class TestSavingAVibeDoesNotOrphanWhatIsOnScreen(unittest.TestCase):
    """Andres reported it directly: clicking an icon while building a
    playlist did not select it. `saveVibes()` is a round trip, and it used to
    come back and replace every object in `VIBE_DEFS` with a freshly built
    one - so a second click, landing on a card before that round trip
    finished, wrote onto the object the card was drawn for, which was no
    longer the one in `VIBE_DEFS`. The click looked like it did nothing."""

    def test_the_save_updates_objects_in_place(self):
        page = _page()
        i = page.index("async function saveVibes(said)")
        block = page[i:i + 1700]
        self.assertIn("Object.assign(v, saved)", block)
        # The tell of the old bug: rebuilding the array from scratch with
        # spread syntax makes a new object even for an id that already had
        # one.
        self.assertNotIn("({...(had[v.id]", block)

    def test_the_array_itself_is_not_replaced_either(self):
        # `VIBE_DEFS = [...]` would break any code still holding the old
        # array reference the same way replacing the objects inside it did.
        page = _page()
        i = page.index("async function saveVibes(said)")
        block = page[i:i + 1700]
        self.assertNotIn("VIBE_DEFS = r.vibes", block)


class TestALostVibeFetchHealsItself(unittest.TestCase):
    """He also hit this the same evening: opening the app in the exact
    window the computer was restarting left Settings saying "no vibes yet"
    for good, because the one failed fetch was never retried. The server
    always answers with at least the three it ships with - an empty list
    client-side can only mean the request itself did not land."""

    def test_a_failed_fetch_does_not_erase_a_good_answer(self):
        src = _page()
        i = src.index("async function loadVibes()")
        block = src[i:i + 1300]
        self.assertIn("VIBES_FAILED = true", block)
        self.assertNotIn("VIBE_DEFS = [];", block)

    def test_the_regular_poll_retries_it(self):
        page = _page()
        i = page.index("paintQuickButton();")
        block = page[i:i + 500]
        self.assertIn("!VIBE_DEFS.length || VIBES_FAILED", block)
        self.assertIn("loadVibes()", block)


class TestTheShootLibraryIsAHistoryNotAFossil(unittest.TestCase):
    """"Se ha quedado siempre estático en el mismo" - the list was sorted by
    the order shoots were first recorded, so a folder sifted again tonight
    stayed buried wherever it landed the day it was born. It was never
    static; it just never moved forward with anything you actually did."""

    def test_it_sorts_by_when_it_actually_happened(self):
        page = _page()
        i = page.index("function historyEntries()")
        block = page[i:i + 600]
        self.assertIn("sort((a, b) => b.when.localeCompare(a.when))", block)
        self.assertNotIn(".reverse()", block)

    def test_the_open_list_is_grouped_by_month(self):
        page = _page()
        # Grouped into Today / Yesterday / This week / month / year shelves
        # now, and the month shelves are still named by monthLabel.
        self.assertIn("function monthLabel(when)", page)
        i = page.index("function paintShoots()")
        self.assertIn("bucketLabel(", page[i:i + 2200])
        j = page.index("function bucketLabel(")
        self.assertIn("monthLabel(", page[j:j + 800])

    def test_the_export_carries_the_dates_the_grouping_uses(self):
        page = _page()
        i = page.index("$('#sh-export').onclick")
        block = page[i:i + 1300]
        self.assertIn("'created'", block)
        self.assertIn("'last_touched'", block)


class TestHistoryIncludesImportsNotJustSiftedShoots(unittest.TestCase):
    """He copied a card at 00:26 and it never appeared anywhere in the app -
    the list only ever read `STATE.shoots`, the ledger's record of cull
    outcomes, and a card that is only ever imported has no cull outcome. It
    was never frozen; it was answering a narrower question than the one he
    was actually asking of it."""

    def test_the_server_sends_every_import_not_the_last_eight(self):
        src = SERVER.read_text(encoding="utf-8")
        i = src.index('"imports":')
        self.assertNotIn("[-8:]", src[i:i + 60])

    def test_both_halves_of_the_ledger_feed_one_timeline(self):
        page = _page()
        i = page.index("function historyEntries()")
        block = page[i:i + 700]
        self.assertIn("STATE.shoots", block)
        self.assertIn("STATE.imports", block)
        self.assertIn("kind: 'sift'", block)
        self.assertIn("kind: 'import'", block)

    def test_an_import_row_says_what_it_actually_did(self):
        page = _page()
        i = page.index("function historyRow(e)")
        block = page[i:i + 900]
        self.assertIn("im.copied", block)
        self.assertIn("im.source", block)
        self.assertIn("im.dest", block)


class TestOverviewIsOneCardNotThree(unittest.TestCase):
    """Asked for directly, after two earlier redesigns that still weren't it:
    a status you can read at a glance (fine / working / needs attention), the
    current task if one is running, what got done today, and the technical
    facts - quiet, at the bottom - in that order, in one card instead of
    three that used to disagree about what mattered."""

    def test_system_status_live_and_all_time_are_gone(self):
        page = _page()
        self.assertNotIn("<h2>System status</h2>", page)
        self.assertNotIn("<h2>Live</h2>", page)
        self.assertNotIn("<h2>All time</h2>", page)
        self.assertNotIn("function paintStats()", page)
        self.assertNotIn('id="live-stats"', page)

    def test_one_status_word_not_three_technical_states(self):
        page = _page()
        self.assertIn("function overviewStatus()", page)
        i = page.index("function overviewStatus()")
        block = page[i:i + 900]
        self.assertIn("'Working'", block)
        self.assertIn("'Needs attention'", block)
        self.assertIn("'All good'", block)
        self.assertNotIn("STATE_WORDS", page)

    def test_a_failed_job_or_an_open_run_needs_attention(self):
        page = _page()
        i = page.index("function overviewStatus()")
        block = page[i:i + 900]
        self.assertIn("!last.ok && !last.stopped", block)
        self.assertIn("p.state === 'interrupted'", block)

    def test_todays_activity_reads_the_same_history_as_the_history_card(self):
        """So the two can never disagree about what counts as today."""
        page = _page()
        i = page.index("function paintToday()")
        block = page[i:i + 800]
        self.assertIn("historyEntries()", block)
        self.assertIn("today", block)

    def test_the_technical_facts_are_last_and_smallest(self):
        page = _page()
        self.assertIn("function paintTechFooter()", page)
        i = page.index('id="ov-tech"')
        # It sits after ov-today in the markup, not before it.
        self.assertGreater(i, page.index('id="ov-today"'))

    def test_nothing_calls_the_removed_functions(self):
        page = _page()
        self.assertNotIn("paintProgramState()", page)
        self.assertNotIn("paintStats();", page)
        self.assertIn("paintOverview();", page)


if __name__ == "__main__":
    unittest.main()
