"""Spell check / grammar hints and the AI assistant's helpers.

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_writer  # noqa: E402
import caption_creator as cc  # noqa: E402
import spellcheck as sc  # noqa: E402


@unittest.skipUnless(sc.available(), "needs assets/english_words.txt")
class SpellingTests(unittest.TestCase):
    def test_flags_typos_not_names_contractions_or_emoji(self):
        text = ("Megan said she'd come. I couldn't believe it ❤️ 💕. "
                "The hair was tickeling my nose, wierd.")
        self.assertEqual([w for _s, _e, w in sc.misspellings(text)], ["tickeling", "wierd"])

    def test_curly_apostrophes(self):
        self.assertEqual(sc.misspellings("She’d said it wasn’t real."), [])

    def test_suggestions_rank_the_obvious_fix_first(self):
        self.assertEqual(sc.suggestions("tickeling")[0], "tickling")
        self.assertEqual(sc.suggestions("Megna")[0], "Megan")
        self.assertEqual(sc.suggestions("wierd")[0], "weird")

    def test_user_dictionary(self):
        orig = sc.USER_DICT_PATH
        with tempfile.TemporaryDirectory() as tmp:
            sc.USER_DICT_PATH = os.path.join(tmp, "user_dictionary.txt")
            try:
                self.assertFalse(sc.is_known("Xandria"))
                sc.add_to_dictionary("Xandria")
                self.assertTrue(sc.is_known("xandria"))
            finally:
                sc.USER_DICT_PATH = orig
                sc._user_words.discard("xandria")


class GrammarTests(unittest.TestCase):
    def fixes(self, text):
        return [(text[s:e], fix) for s, e, fix, _msg in sc.grammar_issues(text)]

    def test_common_slips(self):
        got = self.fixes("i saw a elephant and and she could of known. it was alot ,really.Also")
        for expected in (("i", "I"), ("a", "an"), ("and and", "and"),
                         ("could of", "could have"), ("alot", "a lot"), (" ,", ","),
                         (".", ". "), ("i", "I")):
            self.assertIn(expected, got)

    def test_no_false_alarms(self):
        text = ("The U.S. team won. e.g. this works. “What?” she asked. "
                "I had had enough. An hour later, a university friend called.")
        self.assertEqual(self.fixes(text)[:1], [("e", "E")])  # only the real one


class AssistantHelperTests(unittest.TestCase):
    def test_edit_instructions(self):
        self.assertIn("about 80 words", ai_writer.edit_instruction("shorten", 80))
        self.assertIn("Fix only spelling", ai_writer.edit_instruction("grammar", 50))
        self.assertEqual(ai_writer.edit_instruction("custom", 0, "make it teasing"),
                         "make it teasing.")

    def test_words_that_fit_shrinks_long_text_in_a_small_panel(self):
        raw = dict(frame_size=(350, 242), layout="horizontal", width=120, pad=12,
                   stroke_w=0, output_override=False, output_size_pct=0.0,
                   font_family="Arial", bold=False)
        short = "She woke up different."
        self.assertEqual(cc._words_that_fit(short, raw), 4)
        long_text = "word " * 400
        fit = cc._words_that_fit(long_text, raw)
        self.assertTrue(10 < fit < 400, fit)
        # A bigger minimum size fits fewer words.
        self.assertLess(cc._words_that_fit(long_text, raw, 40), fit)

    def test_long_caption_on_a_big_image_counts_as_fitting(self):
        """Regression: ~1,300 words beside a 1122x1402 image export at ~22px
        and read fine; the old height-based rule wanted ~59px text and
        claimed only a fraction fit."""
        text = ("I used to think hypnosis was a load of nonsense back in 2021. " * 120)
        text = " ".join(text.split()[:1334])
        raw = dict(frame_size=(1122, 1402), layout="horizontal", width=1402, pad=5,
                   stroke_w=0, output_override=False, output_size_pct=0.0,
                   font_family="Arial", bold=False)
        self.assertEqual(cc._words_that_fit(text, raw), 1334)


if __name__ == "__main__":
    unittest.main()
