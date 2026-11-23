"""Themed formats, their bundled fonts, and caption panel patterns."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ThemedFormatTests(unittest.TestCase):
    def test_formats_exist_and_fonts_are_bundled(self):
        fonts = cc._scan_all_fonts()
        for name in cc._THEMED_FORMATS:
            with open(os.path.join(REPO, "formats", f"{name}.json"), encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["name"], name)
            self.assertIn(data.get("panel_style"), cc._PANEL_STYLES.values())
            for key in ("font_family", "header_font", "footer_font"):
                self.assertIn(data[key], fonts, f"{name} {key}")
                self.assertTrue(fonts[data[key]].startswith(os.path.join(REPO, "fonts")),
                                f"{name} {key} isn't a bundled font")

    def test_every_bundled_font_has_a_license(self):
        lic = os.listdir(os.path.join(REPO, "fonts", "licenses"))
        self.assertGreaterEqual(len(lic), 10)
        for name in lic:
            with open(os.path.join(REPO, "fonts", "licenses", name), encoding="utf-8") as f:
                self.assertIn("SIL OPEN FONT LICENSE", f.read().upper())


class PanelPatternTests(unittest.TestCase):
    def test_plain(self):
        img = cc._panel_background(40, 30, "#102030", "")
        self.assertEqual(img.size, (40, 30))
        self.assertEqual(img.getpixel((5, 5)), (16, 32, 48, 255))

    def test_every_pattern_draws(self):
        for style in filter(None, cc._PANEL_STYLES.values()):
            img = cc._panel_background(160, 120, "#000000", f"{style}:#ffffff")
            self.assertEqual(img.size, (160, 120))
            self.assertGreater(len(img.getcolors(1 << 16) or [0, 0]), 1, style)

    def test_gradient_runs_top_to_bottom(self):
        img = cc._panel_background(10, 100, "#000000", "gradient:#ffffff")
        self.assertLess(img.getpixel((5, 0))[0], 10)
        self.assertGreater(img.getpixel((5, 99))[0], 245)

    def test_bad_color_falls_back(self):
        img = cc._panel_background(20, 20, "#000000", "stars:nonsense")
        self.assertEqual(img.size, (20, 20))

    def test_pattern_is_not_a_format_difference_by_default(self):
        self.assertFalse(cc._format_differs({"name": "A"}, {"name": "A", "panel_style": ""}))
        self.assertTrue(cc._format_differs({}, {"panel_style": "hearts"}))


if __name__ == "__main__":
    unittest.main()
