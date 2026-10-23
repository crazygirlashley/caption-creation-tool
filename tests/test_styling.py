"""Inline caption styling, auto-contrast overlays and image framing.

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402


def _base_kwargs(**over):
    kw = dict(text="", cap_width=300, page_bg="#ffffff", font_family="Arial", font_size=24,
              font_color="#000000", padding=10)
    kw.update(over)
    return kw


class InlineStylingTests(unittest.TestCase):
    def test_markers_are_removed_and_flag_their_text(self):
        plain, flags = cc._styled_chars("**bold *it* bold** x *y*")
        self.assertEqual(plain, "bold it bold x y")
        self.assertEqual(flags[0], cc._STYLE_BOLD)
        self.assertEqual(flags[plain.index("it")], cc._STYLE_BOLD | cc._STYLE_ITALIC)
        self.assertEqual(flags[plain.index("x")], 0)
        self.assertEqual(flags[-1], cc._STYLE_ITALIC)

    def test_lone_asterisks_and_maths_are_left_alone(self):
        for text in ("5 * 3 * 2", "a star * here", "**"):
            self.assertEqual(cc._styled_chars(text)[0], text)

    def test_dialogue_is_flagged_including_curly_quotes(self):
        plain, flags = cc._styled_chars('She said “hi” and "bye".', quotes=True)
        self.assertTrue(flags[plain.index("hi")] & cc._STYLE_QUOTE)
        self.assertTrue(flags[plain.index("bye")] & cc._STYLE_QUOTE)
        self.assertFalse(flags[plain.index("said")] & cc._STYLE_QUOTE)

    def test_styled_wrap_keeps_every_word_and_fits(self):
        text = "*Slowly* the **change** took hold, " * 12
        lines = cc._styled_lines(text, "Arial", 24, False, 280, markup=True)
        words = " ".join("".join(seg for seg, _ in ln) for ln in lines).split()
        self.assertEqual(words, cc._styled_chars(text)[0].split())
        for ln in lines:
            self.assertLessEqual(cc._runs_w(ln, "Arial", 24, False), 280 + 1)

    def test_plain_text_renders_exactly_as_before(self):
        frame = Image.new("RGBA", (300, 300), (40, 80, 160, 255))
        text = "Nothing styled here, just words."
        a = cc.build_composite(frame, **_base_kwargs(text=text))
        b = cc.build_composite(frame, **_base_kwargs(text=text, inline_markup=True,
                                                      dialogue_color="#ff0000"))
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_dialogue_color_is_drawn(self):
        frame = Image.new("RGBA", (1000, 720), (40, 80, 160, 255))  # at the output floor: no rescale
        img = cc.build_composite(frame, **_base_kwargs(
            text='"Hello there"', inline_markup=True, dialogue_color="#ff0000", font_size=40))
        panel = img.crop((1000, 0, 1300, 720)).convert("RGB")
        self.assertIn((255, 0, 0), {c for _n, c in panel.getcolors(100000)})

    def test_faux_italic_for_a_font_without_an_italic_file(self):
        font, synth = cc._style_font("No Such Font Family", 30, False, True)
        self.assertTrue(synth)
        img = Image.new("RGBA", (200, 60), (255, 255, 255, 255))
        cc._draw_runs(ImageDraw.Draw(img), (5, 5), [("lean", cc._STYLE_ITALIC)],
                      "No Such Font Family", 30, False, (0, 0, 0), None)
        self.assertIsNotNone(ImageDraw.Draw(img) and img.convert("L").point(
            lambda v: 255 if v < 128 else 0).getbbox())

    def test_autofit_counts_styled_lines(self):
        text = "**Big bold words** that need room " * 20
        plain_fit = cc._fit_font_size(text.replace("*", ""), "Arial", 80, 300, 600, 10)
        styled_fit = cc._fit_font_size(text, "Arial", 80, 300, 600, 10, markup=True)
        self.assertLessEqual(styled_fit, plain_fit)


class AutoContrastTests(unittest.TestCase):
    def test_outline_only_where_needed(self):
        self.assertIsNone(cc._auto_outline([20, 10], "#ffffff", 30))      # white on dark
        self.assertEqual(cc._auto_outline([235, 10], "#ffffff", 30)[1], "#000000")  # white on white
        self.assertEqual(cc._auto_outline([20, 10], "#101010", 30)[1], "#ffffff")   # dark on dark
        self.assertIsNotNone(cc._auto_outline([30, 90], "#ffffff", 30))   # busy

    def test_resolve_adds_outlines_only_without_a_stroke(self):
        raw = dict(text="hi", chat_messages=None, field_size=20, width=200, pad=10, stroke_w=0,
                   stroke_color="#000000", bold=False, shadow=False, align="center",
                   fmt="Standard", layout="horizontal", caption_side="right", auto_size=False,
                   frame_size=(400, 400), scale_overlays=False, font_family="Arial",
                   page_bg="#ffffff", font_color="#ffffff", header_enabled=True,
                   header_text="T", header_font="Arial", header_size=30, footer_enabled=True,
                   footer_text="F", footer_font="Arial", footer_size=16, watermark_path="",
                   watermark_height=40, output_override=False, output_size_pct=0,
                   auto_contrast=True, overlay_luma={"header": [240, 5], "footer": [10, 5]})
        kw, _ = cc._resolve_composite_kwargs(raw)
        self.assertEqual(kw["header_outline"][1], "#000000")
        self.assertIsNone(kw["footer_outline"])
        kw, _ = cc._resolve_composite_kwargs(dict(raw, stroke_w=2))
        self.assertNotIn("header_outline", kw)


class FramingTests(unittest.TestCase):
    def test_zoom_keeps_size_and_pans(self):
        frame = Image.new("RGB", (200, 100), "white")
        frame.paste((255, 0, 0), (0, 0, 100, 100))   # left half red
        left = cc._apply_zoom(frame, 2.0, -1.0, 0.0)
        right = cc._apply_zoom(frame, 2.0, 1.0, 0.0)
        self.assertEqual(left.size, (200, 100))
        self.assertEqual(left.getpixel((150, 50)), (255, 0, 0))
        self.assertEqual(right.getpixel((50, 50)), (255, 255, 255))
        self.assertIs(cc._apply_zoom(frame, 1.0, 0.5, 0.5), frame)

    def test_watermark_corner_and_opacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            wm_path = os.path.join(tmp, "wm.png")
            Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(wm_path)
            frame = Image.new("RGBA", (1000, 720), (0, 0, 255, 255))  # no rescale
            img = cc.build_composite(frame, **_base_kwargs(
                text="x", watermark_path=wm_path, watermark_height=40,
                watermark_pos="tr", watermark_opacity=0.5)).convert("RGB")
            r, _g, b = img.getpixel((1000 - 8 - 20, 8 + 20))   # inside the top-right corner
            self.assertTrue(100 < r < 160 and 100 < b < 160, (r, b))   # half red over blue
            self.assertEqual(img.getpixel((8 + 20, 720 - 8 - 20)), (0, 0, 255))  # not bottom-left


if __name__ == "__main__":
    unittest.main()
