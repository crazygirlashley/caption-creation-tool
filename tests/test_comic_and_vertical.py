"""Comic strip builder (comic.py) and the Vertical switch's format merging."""
import os
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comic  # noqa: E402
import caption_creator as cc  # noqa: E402


def _still(color="#336699", size=(400, 300)):
    return [(Image.new("RGB", size, color), 0)]


class ComicLayoutTests(unittest.TestCase):
    def test_grid_shape(self):
        self.assertEqual(comic.grid_shape(4, "Row"), (4, 1))
        self.assertEqual(comic.grid_shape(3, "Column"), (1, 3))
        self.assertEqual(comic.grid_shape(5, "Grid", 2), (2, 3))
        self.assertEqual(comic.grid_shape(2, "Grid", 4), (2, 1))

    def test_still_strip_size(self):
        opts = dict(layout="Row", shape="Square", panel_width=100, gutter=10, border=2)
        frames, durs = comic.render([{}, {}], [_still(), _still()], opts)
        self.assertEqual(len(frames), 1)
        self.assertEqual(durs, [0])
        # 2 panels of 100 + borders 2*2 each + 3 gaps of 10
        self.assertEqual(frames[0].size, (2 * 104 + 30, 104 + 20))

    def test_title_and_below_captions_add_height(self):
        base = dict(layout="Row", shape="Square", panel_width=200, gutter=10, border=2)
        plain = comic.render([{}], [_still()], base)[0][0]
        titled = comic.render([{"caption": "Below it"}], [_still()],
                              dict(base, title="Title", caption_place="Below the panel"))[0][0]
        self.assertGreater(titled.height, plain.height)
        self.assertEqual(titled.width, plain.width)

    def test_grid_centers_last_row(self):
        geo = comic.layout_geometry([{}] * 3, dict(comic.DEFAULTS, layout="Grid", columns=2),
                                    comic._default_font, (400, 300))
        (x0, _), (x1, _), (x2, _) = geo["spots"]
        self.assertGreater(x2, x0)
        self.assertLess(x2, x1)

    def test_animated_panel_loops(self):
        anim = [(Image.new("RGB", (200, 200), c), 100) for c in ("red", "green", "blue")]
        frames, durs = comic.render([{}, {}], [anim, _still()], dict(panel_width=80))
        self.assertEqual(len(frames), 3)
        self.assertEqual(sum(durs), 300)

    def test_identical_frames_merge(self):
        slow = [(Image.new("RGB", (50, 50), c), 400) for c in ("red", "blue")]
        fast = [(Image.new("RGB", (50, 50), c), 100) for c in ("red", "blue")]
        frames, durs = comic.render([{}, {}], [slow, fast], dict(panel_width=60))
        self.assertEqual(sum(durs), 800)
        self.assertEqual(len(frames), 8)   # fast panel changes every 100 ms

    def test_cover_crop_keeps_focus(self):
        img = Image.new("RGB", (400, 100), "black")
        img.paste("white", (350, 0, 400, 100))
        out = comic.cover_crop(img, (100, 100), focus=(0.95, 0.5))
        self.assertEqual(out.getpixel((95, 50)), (255, 255, 255))

    def test_bubble_and_caption_draw(self):
        cell = comic.render_panel(Image.new("RGB", (300, 300), "#000000"),
                                  {"caption": "Narration", "bubble": "Hello!",
                                   "face": (120, 120, 180, 190)},
                                  (300, 300), dict(comic.DEFAULTS), comic._default_font)
        colors = {c for _, c in cell.getcolors(1 << 16)}
        self.assertIn((255, 255, 255), colors)                     # bubble
        self.assertIn((255, 243, 176), colors)                     # caption box

    def test_wrap_breaks_long_words(self):
        font = comic._default_font(20)
        for line in comic.wrap("a" * 80 + " short", font, 120):
            self.assertLessEqual(font.getlength(line), 120)

    def test_load_panel_frames(self):
        with tempfile.TemporaryDirectory() as d:
            gif = os.path.join(d, "a.gif")
            fr = [Image.new("RGB", (40, 40), c) for c in ("red", "blue")]
            fr[0].save(gif, save_all=True, append_images=fr[1:], duration=80, loop=0)
            seq = comic.load_panel_frames(gif)
            self.assertEqual(len(seq), 2)
            self.assertEqual(seq[0][1], 80)
            png = os.path.join(d, "b.png")
            fr[0].save(png)
            self.assertEqual(comic.load_panel_frames(png)[0][1], 0)


class VerticalSwitchFormatTests(unittest.TestCase):
    STD = {"name": "Standard", "font_color": "#111111", "dynamic_width": True}

    def test_twin_hidden(self):
        twin = dict(self.STD, name="Standard (Vertical)", layout="vertical", cap_panel_size=300)
        merged = cc._merge_vertical_twins({"Standard": self.STD, "Standard (Vertical)": twin})
        self.assertEqual(list(merged), ["Standard"])

    def test_customised_twin_kept(self):
        twin = dict(self.STD, name="Standard (Vertical)", layout="vertical",
                    font_color="#ff0000")
        merged = cc._merge_vertical_twins({"Standard": self.STD, "Standard (Vertical)": twin})
        self.assertIn("Standard (Vertical)", merged)

    def test_lone_vertical_format_kept(self):
        mine = {"name": "Mine (Vertical)", "layout": "vertical"}
        self.assertIn("Mine (Vertical)", cc._merge_vertical_twins({"Mine (Vertical)": mine}))

    def test_orientation_not_a_difference(self):
        self.assertFalse(cc._format_differs(self.STD, dict(self.STD, layout="vertical")))
        self.assertTrue(cc._format_differs(self.STD, dict(self.STD, layout="overlay")))

    def test_twin_base(self):
        self.assertEqual(cc._vertical_twin_base("Standard (Vertical)", {"Standard": {}}),
                         "Standard")
        self.assertIsNone(cc._vertical_twin_base("Standard (Vertical)", {}))
        self.assertIsNone(cc._vertical_twin_base("Standard", {"Standard": {}}))


if __name__ == "__main__":
    unittest.main()
