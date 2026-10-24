"""Before/After pictures and Series tools.

Run from the repo root with:  python -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402


def _kw(**over):
    kw = dict(text="", cap_width=300, page_bg="#ffffff", font_family="Arial", font_size=24,
              font_color="#000000", padding=10)
    kw.update(over)
    return kw


class BeforeAfterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.before = os.path.join(self.tmp.name, "before.png")
        Image.new("RGB", (300, 500), (255, 0, 0)).save(self.before)   # red

    def tearDown(self):
        self.tmp.cleanup()

    def test_side_by_side_puts_before_first(self):
        after = Image.new("RGBA", (700, 720), (0, 0, 255, 255))   # blue, at the output floor
        img = cc.build_composite(after, **_kw(before_path=self.before, ba_labels=("", "")))
        w, h = cc._ba_frame_size(700, 720, "side")
        self.assertEqual(img.size, (w + 300, h))
        self.assertEqual(img.getpixel((350, 360))[:3], (255, 0, 0))
        self.assertEqual(img.getpixel((w - 350, 360))[:3], (0, 0, 255))
        self.assertEqual(img.getpixel((w + 150, 360))[:3], (255, 255, 255))   # caption panel

    def test_stacked(self):
        after = Image.new("RGBA", (1000, 500), (0, 0, 255, 255))
        img = cc.build_composite(after, **_kw(before_path=self.before, ba_layout="stacked",
                                              ba_labels=("", "")))
        w, h = cc._ba_frame_size(1000, 500, "stacked")
        self.assertEqual(img.size, (w + 300, h))
        self.assertEqual(img.getpixel((500, 100))[:3], (255, 0, 0))
        self.assertEqual(img.getpixel((500, h - 100))[:3], (0, 0, 255))

    def test_small_source_is_paired_at_output_size(self):
        # Upscaled to the output floor: the pair must still split cleanly.
        after = Image.new("RGBA", (200, 150), (0, 0, 255, 255))
        img = cc.build_composite(after, **_kw(before_path=self.before, ba_labels=("", ""),
                                              upscale_filter="nearest"))
        self.assertGreaterEqual(img.height, 720)
        self.assertEqual(img.getpixel((20, img.height // 2))[:3], (255, 0, 0))
        mid = img.width - round(300 * img.height / 150) - 20
        self.assertEqual(img.getpixel((mid, img.height // 2))[:3], (0, 0, 255))

    def test_labels_are_drawn(self):
        after = Image.new("RGBA", (700, 720), (0, 0, 255, 255))
        plain = cc.build_composite(after, **_kw(before_path=self.before, ba_labels=("", "")))
        labeled = cc.build_composite(after, **_kw(before_path=self.before,
                                                  ba_labels=("Before", "After"),
                                                  font_color="#ffffff"))
        self.assertNotEqual(plain.crop((0, 0, 1400, 100)).tobytes(),
                            labeled.crop((0, 0, 1400, 100)).tobytes())

    def test_missing_before_file_is_ignored(self):
        after = Image.new("RGBA", (700, 720), (0, 0, 255, 255))
        a = cc.build_composite(after, **_kw())
        b = cc.build_composite(after, **_kw(before_path=os.path.join(self.tmp.name, "gone.png")))
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_resolve_passes_before_after_through(self):
        raw = dict(text="hi", chat_messages=None, field_size=20, width=200, pad=10, stroke_w=0,
                   stroke_color="#000000", bold=False, shadow=False, align="center",
                   fmt="Standard", layout="horizontal", caption_side="right", auto_size=True,
                   frame_size=cc._ba_frame_size(400, 400, "side"), scale_overlays=False,
                   font_family="Arial", page_bg="#ffffff", font_color="#000000",
                   header_enabled=False, footer_enabled=False, footer_text="",
                   footer_font="Arial", footer_size=16, watermark_path="",
                   watermark_height=40, output_override=False, output_size_pct=0,
                   before_path=self.before, ba_layout="side", ba_labels=["B", "A"],
                   ba_label_size=30)
        kw, _ = cc._resolve_composite_kwargs(raw)
        self.assertEqual(kw["before_path"], self.before)
        self.assertEqual(kw["ba_labels"], ("B", "A"))


class SeriesTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(cc._series_title({"name": "Pill Problems", "part": 7}),
                         "Pill Problems Part 7")
        self.assertEqual(cc._series_title({"name": "X", "part": 2, "template": "{series} #{n}"}),
                         "X #2")
        self.assertEqual(cc._series_title({"name": "X", "part": 2, "template": "{oops}"}),
                         "X Part 2")

    def test_safe_filename(self):
        self.assertEqual(cc._safe_filename('A: "B"/C?'), "A BC")
        self.assertEqual(cc._safe_filename("..."), "Untitled")

    def test_find_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            def save(fname, series):
                p = os.path.join(tmp, fname)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"app": "Caption Creator project", "series": series}, f)
                return p
            p6 = save("six.ccproj", {"name": "Pill Problems", "part": 6})
            save("other.ccproj", {"name": "Something Else", "part": 6})
            save("plain.ccproj", None)
            self.assertEqual(cc._find_series_part("pill problems", 6, [tmp]), p6)
            self.assertIsNone(cc._find_series_part("Pill Problems", 5, [tmp]))


if __name__ == "__main__":
    unittest.main()
