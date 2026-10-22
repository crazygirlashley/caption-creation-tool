"""Project/recent-files helpers.

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402
import recent_store  # noqa: E402


class FormatDiffTests(unittest.TestCase):
    def test_defaults_and_caption_text_dont_count(self):
        stored = {"name": "X", "font_family": "Tahoma", "header_text": "old"}
        snap = dict(stored, header_text="Part 6", caption_side="right", padding=20)
        self.assertFalse(cc._format_differs(stored, snap))

    def test_real_setting_change_counts(self):
        stored = {"font_family": "Tahoma"}
        self.assertTrue(cc._format_differs(stored, {"font_family": "Tahoma", "padding": 14}))


class RecentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = recent_store.PATH
        recent_store.PATH = os.path.join(self.tmp.name, "recent.json")

    def tearDown(self):
        recent_store.PATH = self.orig
        self.tmp.cleanup()

    def test_newest_first_deduped_and_capped(self):
        for i in range(15):
            recent_store.add("searches", f"q{i}")
        recent_store.add("searches", "Q3")
        got = recent_store.get("searches")
        self.assertEqual(got[0], "Q3")
        self.assertEqual(len(got), recent_store.MAX_ITEMS)
        self.assertEqual(sum(1 for g in got if g.lower() == "q3"), 1)

    def test_missing_files_are_dropped(self):
        real = os.path.join(self.tmp.name, "a.gif")
        open(real, "wb").close()
        recent_store.add("files", os.path.join(self.tmp.name, "gone.gif"))
        recent_store.add("files", real)
        self.assertEqual(recent_store.get("files"), [os.path.abspath(real)])

    def test_unreadable_store_is_empty(self):
        with open(recent_store.PATH, "w") as f:
            f.write("{not json")
        self.assertEqual(recent_store.get("files"), [])


if __name__ == "__main__":
    unittest.main()
