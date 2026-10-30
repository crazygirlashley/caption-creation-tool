"""Text boxes placed on the picture ("Text Boxes" format, layout "overlay").

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402

BLUE = (0, 0, 255, 255)


def _kw(**over):
    kw = dict(text="ignored in overlay", cap_width=300, page_bg="#ffffff", font_family="Arial",
              font_size=24, font_color="#000000", padding=10, layout="overlay")
    kw.update(over)
    return kw


def _box(**over):
    b = dict(text="Hello there", x=0.1, y=0.1, w=0.4, size=40, bg="#ff0000", bg_opacity=1.0,
             outline=0, color="#ffffff")
    b.update(over)
    return b


class TextBoxTests(unittest.TestCase):
    def test_no_panel_just_the_picture(self):
        frame = Image.new("RGBA", (1280, 720), BLUE)     # at the output floor: no rescale
        img = cc.build_composite(frame, **_kw(text_boxes=[]))
        self.assertEqual(img.size, (1280, 720))
        self.assertEqual(img.convert("RGB").getcolors(10), [(1280 * 720, (0, 0, 255))])

    def test_box_is_drawn_where_placed(self):
        frame = Image.new("RGBA", (1280, 720), BLUE)
        img = cc.build_composite(frame, **_kw(text_boxes=[_box()])).convert("RGB")
        g = cc._tb_layout(_box(), 1280, 720, 1.0)
        self.assertEqual((g["x"], g["y"], g["w"]), (128, 72, 512))
        self.assertEqual(img.getpixel((g["x"] + 3, g["y"] + g["h"] // 2)), (255, 0, 0))
        self.assertEqual(img.getpixel((g["x"] - 5, g["y"] + g["h"] // 2)), (0, 0, 255))
        self.assertEqual(img.getpixel((g["x"] + g["w"] + 5, g["y"] + 5)), (0, 0, 255))
        inside = img.crop((g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"]))
        self.assertIn((255, 255, 255), {c for _n, c in inside.getcolors(100000)})   # the text

    def test_later_boxes_are_on_top(self):
        frame = Image.new("RGBA", (1280, 720), BLUE)
        a = _box(text="", bg="#ff0000")
        b = _box(text="", bg="#00ff00")
        img = cc.build_composite(frame, **_kw(text_boxes=[a, b])).convert("RGB")
        self.assertEqual(img.getpixel((140, 80)), (0, 255, 0))

    def test_small_picture_keeps_box_position_when_upscaled(self):
        frame = Image.new("RGBA", (320, 180), BLUE)      # scaled 4x to the floor
        img = cc.build_composite(frame, **_kw(text_boxes=[_box(text="")]))
        self.assertEqual(img.size, (1280, 720))
        g = cc._tb_layout(_box(text=""), 1280, 720, 4.0)
        self.assertEqual(g["w"], 512)
        mid = g["y"] + g["h"] // 2      # (the corners are rounded)
        self.assertEqual(img.getpixel((g["x"] + 4, mid))[:3], (255, 0, 0))
        self.assertEqual(img.getpixel((g["x"] - 4, mid))[:3], (0, 0, 255))

    def test_more_text_makes_a_taller_box(self):
        short = cc._tb_layout(_box(text="One line"), 1280, 720, 1.0)
        long = cc._tb_layout(_box(text="Many words " * 30), 1280, 720, 1.0)
        self.assertGreater(long["h"], short["h"] * 3)

    def test_half_transparent_background(self):
        frame = Image.new("RGBA", (1280, 720), BLUE)
        img = cc.build_composite(frame, **_kw(text_boxes=[_box(text="", bg_opacity=0.5)]))
        r, _g, b = img.convert("RGB").getpixel((140, 80))
        self.assertTrue(110 < r < 145 and 110 < b < 145, (r, b))

    def test_resolve_overlay(self):
        raw = dict(text="caption", chat_messages=None, field_size=20, width=0, pad=10,
                   stroke_w=0, stroke_color="#000000", bold=False, shadow=False, align="center",
                   fmt="Text Boxes", layout="overlay", caption_side="right", auto_size=True,
                   frame_size=(400, 300), scale_overlays=False, font_family="Arial",
                   page_bg="#ffffff", font_color="#000000", header_enabled=False,
                   footer_enabled=False, footer_text="", footer_font="Arial", footer_size=16,
                   watermark_path="", watermark_height=40, output_override=False,
                   output_size_pct=0, text_boxes=[_box()])
        kw, size = cc._resolve_composite_kwargs(raw)
        self.assertEqual(kw["text"], "")
        self.assertEqual(kw["text_boxes"], [_box()])
        self.assertEqual(size, 20)    # no Auto-fit search without a panel
        self.assertEqual(cc._words_that_fit("a b c", raw), 3)

    def test_speech_bubble_reaches_its_tail(self):
        frame = Image.new("RGBA", (1280, 720), BLUE)
        box = _box(text="Hi", shape="speech", bg="#ffffff", tail_x=0.5, tail_y=0.8,
                   border=0, color="#000000")
        img = cc.build_composite(frame, **_kw(text_boxes=[box])).convert("RGB")
        g = cc._tb_layout(box, 1280, 720, 1.0)
        # Near the tip (just above it, toward the bubble) is bubble-white.
        tip_x, tip_y = 640, 576
        cx, cy = g["x"] + g["w"] / 2, g["y"] + g["h"] / 2
        px, py = tip_x + (cx - tip_x) * 0.1, tip_y + (cy - tip_y) * 0.1
        r, g_, _b = img.getpixel((round(px), round(py)))
        self.assertTrue(r > 230 and g_ > 230, (r, g_))     # bubble white (edges anti-aliased)
        self.assertEqual(img.getpixel((20, 700)), (0, 0, 255))

    def test_thought_bubble_and_border(self):
        frame = Image.new("RGBA", (1280, 720), BLUE)
        box = _box(text="Hmm", shape="thought", bg="#ffffff", border=3,
                   border_color="#ff0000", color="#000000")
        img = cc.build_composite(frame, **_kw(text_boxes=[box])).convert("RGB")
        colors = {c for _n, c in img.getcolors(1_000_000)}
        self.assertIn((255, 255, 255), colors)
        self.assertTrue(any(r > 200 and g < 60 and b < 60 for r, g, b in colors))   # border

    def test_gradient_text_uses_both_colors(self):
        frame = Image.new("RGBA", (1280, 720), (128, 128, 128, 255))
        box = _box(text="GRADIENT TEXT\nSECOND LINE", bg="", size=60, color="#ff0000",
                   color2="#0000ff", w=0.8)
        img = cc.build_composite(frame, **_kw(text_boxes=[box])).convert("RGB")
        g = cc._tb_layout(box, 1280, 720, 1.0)
        top = img.crop((g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"] // 3))
        bottom = img.crop((g["x"], g["y"] + 2 * g["h"] // 3, g["x"] + g["w"], g["y"] + g["h"]))
        reddest = max(c[0] - c[2] for _n, c in top.getcolors(1_000_000))
        bluest = max(c[2] - c[0] for _n, c in bottom.getcolors(1_000_000))
        self.assertGreater(reddest, 120)
        self.assertGreater(bluest, 120)

    def test_rotated_box_still_renders_in_place(self):
        frame = Image.new("RGBA", (1280, 720), BLUE)
        flat = cc.build_composite(frame, **_kw(text_boxes=[_box(text="")]))
        tilted = cc.build_composite(frame, **_kw(text_boxes=[_box(text="", angle=20)]))
        self.assertEqual(tilted.size, flat.size)
        self.assertNotEqual(flat.tobytes(), tilted.tobytes())
        g = cc._tb_layout(_box(text=""), 1280, 720, 1.0)
        center = (g["x"] + g["w"] // 2, g["y"] + g["h"] // 2)
        self.assertEqual(tilted.convert("RGB").getpixel(center), (255, 0, 0))

    def test_short_hex_colors(self):
        self.assertEqual(cc._hex_to_rgb("#f0c"), (255, 0, 204))

    def test_other_layouts_ignore_text_boxes(self):
        frame = Image.new("RGBA", (1000, 720), BLUE)
        a = cc.build_composite(frame, **_kw(layout="horizontal", text="x"))
        b = cc.build_composite(frame, **_kw(layout="horizontal", text="x", text_boxes=[_box()]))
        self.assertEqual(a.tobytes(), b.tobytes())


if __name__ == "__main__":
    unittest.main()
