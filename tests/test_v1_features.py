"""Censor boxes, text watermarks, animated before pictures and the
picture's place in the export.

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


def _kw(**over):
    kw = dict(text="hello", cap_width=300, page_bg="#ffffff", font_family="Arial", font_size=24,
              font_color="#000000", padding=10)
    kw.update(over)
    return kw


def _noise(w=1000, h=720, seed=3):
    arr = np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)
    return Image.fromarray(arr).convert("RGBA")


class CensorTests(unittest.TestCase):
    def test_bar_blur_and_pixelate(self):
        frame = _noise()
        box = dict(x=0.1, y=0.1, w=0.3, h=0.3)
        bar = cc._apply_censors(frame, [dict(box, style="bar", color="#ff00ff")])
        self.assertEqual(bar.getpixel((250, 150))[:3], (255, 0, 255))
        self.assertEqual(bar.getpixel((600, 600)), frame.getpixel((600, 600)))   # untouched
        blur = np.asarray(cc._apply_censors(frame, [dict(box, style="blur")]).convert("L"), float)
        orig = np.asarray(frame.convert("L"), float)
        self.assertLess(blur[100:300, 150:350].std(), orig[100:300, 150:350].std() / 4)
        pix = np.asarray(cc._apply_censors(frame, [dict(box, style="pixelate")]).convert("L"))
        region = pix[100:270, 120:380]                  # inside the 100..400 x 72..288 box
        self.assertLess(len(np.unique(region)), 120)    # big flat blocks, not noise

    def test_censor_lands_on_the_picture_not_the_panel(self):
        frame = Image.new("RGBA", (300, 200), (0, 0, 255, 255))        # upscaled x3.6
        img = cc.build_composite(frame, **_kw(
            censors=[dict(x=0.0, y=0.0, w=0.5, h=0.5, style="bar", color="#00ff00")]))
        pic = cc._picture_rect(300, 200, 300, "horizontal", "right")
        self.assertEqual(img.getpixel((pic[0] + 10, pic[1] + 10))[:3], (0, 255, 0))
        self.assertEqual(img.getpixel((pic[2] - 10, pic[3] - 10))[:3], (0, 0, 255))

    def test_picture_rect_layouts(self):
        self.assertEqual(cc._picture_rect(1000, 720, 300, "horizontal", "right"),
                         (0, 0, 1000, 720))
        self.assertEqual(cc._picture_rect(1000, 720, 300, "horizontal", "left"),
                         (300, 0, 1300, 720))
        self.assertEqual(cc._picture_rect(1280, 720, 200, "vertical", "right"),
                         (0, 0, 1280, 720))
        self.assertEqual(cc._picture_rect(1280, 720, 300, "overlay", "right"), (0, 0, 1280, 720))


class TextWatermarkTests(unittest.TestCase):
    def test_text_watermark_drawn_in_its_corner(self):
        frame = Image.new("RGBA", (1000, 720), (0, 0, 255, 255))
        plain = cc.build_composite(frame, **_kw(watermark_height=80)).convert("RGB")
        marked = cc.build_composite(frame, **_kw(
            watermark_height=80, watermark_text="@someone", watermark_text_color="#ffff00",
            watermark_pos="br")).convert("RGB")
        corner = marked.crop((600, 600, 1000, 720))
        self.assertIn((255, 255, 0), {c for _n, c in corner.getcolors(100000)})
        self.assertEqual(marked.crop((0, 0, 400, 400)).tobytes(),
                         plain.crop((0, 0, 400, 400)).tobytes())


class AnimatedBeforeTests(unittest.TestCase):
    def test_before_gif_follows_the_frame_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "before.gif")
            frames = [Image.new("RGB", (200, 200), c) for c in ((255, 0, 0), (0, 255, 0))]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
            after = Image.new("RGBA", (700, 720), (0, 0, 255, 255))
            colors = []
            for i in range(3):
                img = cc.build_composite(after, **_kw(before_path=path, ba_labels=("", ""),
                                                      frame_index=i))
                colors.append(img.getpixel((300, 360))[:3])
            self.assertEqual(colors, [(255, 0, 0), (0, 255, 0), (255, 0, 0)])   # loops


if __name__ == "__main__":
    unittest.main()
