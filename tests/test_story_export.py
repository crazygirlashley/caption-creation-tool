"""Long story mode (full story in the description, opening on the image)
and per-site export limits.

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402

STORY = " ".join(f"Sentence number {i} tells a little more of the story." for i in range(200))


def _raw(**over):
    raw = dict(text=STORY, chat_messages=None, field_size=20, width=300, pad=10, stroke_w=0,
               stroke_color="#000000", bold=False, shadow=False, align="center",
               fmt="Standard", layout="horizontal", caption_side="right", auto_size=True,
               frame_size=(1000, 720), scale_overlays=False, font_family="Arial",
               page_bg="#ffffff", font_color="#000000", header_enabled=False,
               footer_enabled=False, footer_text="", footer_font="Arial", footer_size=16,
               watermark_path="", watermark_height=40, output_override=False,
               output_size_pct=0, min_px=20)
    raw.update(over)
    return raw


class LongStoryTests(unittest.TestCase):
    def test_excerpt_is_an_opening_that_fits_and_ends_a_sentence(self):
        raw = _raw()
        text = cc._story_excerpt(STORY, raw, 20)
        body, ending = text.rsplit("\n\n", 1)
        self.assertEqual(ending, cc._STORY_ENDING)
        self.assertTrue(STORY.startswith(body))
        self.assertTrue(body.endswith("."))
        self.assertLess(len(body.split()), len(STORY.split()))
        self.assertTrue(cc._fits_at(text, raw, 20))
        # ...and is the longest such opening: one more sentence wouldn't fit.
        n = len(body.split())
        longer = " ".join(STORY.split()[:n + 10]) + "\n\n" + cc._STORY_ENDING
        self.assertFalse(cc._fits_at(longer, raw, 20))

    def test_short_story_is_left_whole(self):
        self.assertEqual(cc._story_excerpt("Just a short one.", _raw(), 20), "Just a short one.")

    def test_image_text_modes(self):
        self.assertEqual(cc._image_text(_raw()), STORY)                       # mode off
        self.assertEqual(cc._image_text(_raw(story_mode=True, story_image_text="Mine.")),
                         "Mine.")
        shown = cc._image_text(_raw(story_mode=True, story_ending="More below!"))
        self.assertTrue(shown.endswith("\n\nMore below!"))
        kw, _ = cc._resolve_composite_kwargs(_raw(story_mode=True))
        self.assertEqual(kw["text"], cc._image_text(_raw(story_mode=True)))
        self.assertGreaterEqual(kw["font_size"], 20)   # readable

    def test_fixed_font_size_uses_that_size(self):
        small = cc._image_text(_raw(story_mode=True, auto_size=False, field_size=14))
        big = cc._image_text(_raw(story_mode=True, auto_size=False, field_size=30))
        self.assertGreater(len(small.split()), len(big.split()))


class SiteLimitTests(unittest.TestCase):
    def test_over_limit_message(self):
        self.assertIsNone(cc._over_site_limit_msg(4_000_000, "X / Twitter", "image"))
        self.assertIn("5 MB image", cc._over_site_limit_msg(6_000_000, "X / Twitter", "image"))
        self.assertIn("80 MB", cc._over_site_limit_msg(90 * 1024 * 1024, "DeviantArt", "gif"))
        self.assertIsNone(cc._over_site_limit_msg(10**10, "No limit", "gif"))

    def test_still_is_shrunk_under_the_limit(self):
        noise = Image.fromarray(np.random.default_rng(1).integers(
            0, 255, (600, 800, 3), dtype=np.uint8))
        with tempfile.TemporaryDirectory() as tmp:
            for ext in (".png", ".jpg", ".webp"):
                path = os.path.join(tmp, "out" + ext)
                notes = cc._save_still(noise, path, limit=300_000)
                self.assertLessEqual(os.path.getsize(path), 300_000, ext)
                self.assertTrue(notes, ext)
                Image.open(path).verify()

    def test_no_limit_keeps_the_image_as_is(self):
        img = Image.new("RGB", (5000, 300), "pink")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.png")
            self.assertEqual(cc._save_still(img, path), [])
            self.assertEqual(Image.open(path).size, (5000, 300))
            notes = cc._save_still(img, path, max_side=4096)
            self.assertEqual(Image.open(path).size, (4096, 246))
            self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
