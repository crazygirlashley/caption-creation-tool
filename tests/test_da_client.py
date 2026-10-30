"""DeviantArt request fields (no network).

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import da_client  # noqa: E402


class PublishFieldTests(unittest.TestCase):
    def test_mature_by_default(self):
        fields = dict(da_client.publish_fields("1"))
        self.assertEqual(fields["is_mature"], "true")
        self.assertEqual(fields["mature_level"], "strict")

    def test_options_become_form_fields(self):
        stack = da_client.StashId("11")
        stack.itemid = "22"
        fields = da_client.publish_fields(stack, {
            "mature_level": "moderate", "mature_classes": ["nudity", "sexual", "bogus"],
            "tags": ["sissy", "tg"], "galleryids": ["ABC"], "ai_generated": False,
            "allow_comments": True})
        self.assertIn(("itemid", "22"), fields)
        self.assertIn(("mature_level", "moderate"), fields)
        self.assertEqual([v for k, v in fields if k == "mature_classification[]"],
                         ["nudity", "sexual"])
        self.assertEqual([v for k, v in fields if k == "tags[]"], ["sissy", "tg"])
        self.assertIn(("galleryids[]", "ABC"), fields)
        self.assertIn(("is_ai_generated", "false"), fields)
        self.assertIn(("allow_comments", "true"), fields)

    def test_not_mature_sends_no_rating(self):
        fields = da_client.publish_fields("1", {"mature": False})
        self.assertIn(("is_mature", "false"), fields)
        self.assertFalse(any(k.startswith("mature") for k, _ in fields))

    def test_clean_tags(self):
        self.assertEqual(da_client.clean_tags("pink pill, sissy, #tg-caption, Sissy"),
                         ["pink_pill", "sissy", "tg_caption"])
        self.assertEqual(da_client.clean_tags("pink pill sissy"), ["pink", "pill", "sissy"])
        self.assertEqual(da_client.clean_tags(["a!", "", "b c"]), ["a", "bc"])


if __name__ == "__main__":
    unittest.main()
