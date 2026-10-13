"""Rendering/export regression tests.

Run from the repo root with:  python -m unittest discover tests

Everything here uses synthetic images built in a temp dir, so the suite
needs no source files, fonts beyond Arial, or network access.
"""

import json
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageSequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402

PANEL_BG = "#ff00ff"


def _make_gif(path: str, n: int = 12, size=(200, 140)) -> list:
    """A small animated GIF with a moving square and uneven frame timing.
    Returns the per-frame durations written."""
    frames, durations = [], []
    for i in range(n):
        im = Image.new("RGB", size, (40, 90, 160))
        d = ImageDraw.Draw(im)
        x = 10 + i * 12
        d.rectangle((x, 40, x + 40, 90), fill=(250, 200, 30))
        frames.append(im)
        durations.append(30 + 10 * (i % 3))
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0)
    return durations


def _raw(frame_size, **overrides) -> dict:
    """A _gather_raw_params()-shaped dict with sensible defaults."""
    raw = dict(
        text="", chat_messages=None, bubble_color="blue", field_size=18,
        width=160, pad=12, stroke_w=0, stroke_color="#ffffff", bold=False,
        shadow=False, align="center", fmt="Standard", layout="horizontal",
        caption_side="right", auto_size=True, frame_size=frame_size,
        source_is_gif=True, font_family="Arial", page_bg=PANEL_BG,
        font_color="#000000", header_enabled=False, footer_enabled=False,
        footer_text="", footer_font="Arial", footer_size=16,
        watermark_path="", watermark_height=32, output_override=False,
        output_size_pct=0.0,
    )
    raw.update(overrides)
    return raw


def _panel_ink_bbox(out: Image.Image, frame_size, raw) -> tuple:
    """(top, bottom, left, right) of non-background pixels inside the
    caption panel, plus the panel's (height, width)."""
    fw, fh = frame_size
    arr = np.asarray(out.convert("RGB")).astype(int)
    H, W, _ = arr.shape
    if raw["layout"] == "vertical":
        ph = round(H * raw["width"] / (fh + raw["width"]))
        panel = arr[H - ph + 2:, :]
    else:
        pw = round(W * raw["width"] / (fw + raw["width"]))
        panel = arr[:, W - pw + 2:]
    bg = np.array(Image.new("RGB", (1, 1), PANEL_BG).getpixel((0, 0)))
    ink = np.abs(panel - bg).sum(2) > 60
    rows = np.where(ink.any(1))[0]
    cols = np.where(ink.any(0))[0]
    return rows, cols, ink.shape


class AutoFitTests(unittest.TestCase):
    frame = Image.new("RGBA", (350, 242), (30, 30, 30, 255))

    def assertFits(self, **overrides):
        raw = _raw(self.frame.size, **overrides)
        kwargs, size = cc._resolve_composite_kwargs(raw)
        out = cc.build_composite(self.frame, **kwargs)
        rows, cols, (ph, pw) = _panel_ink_bbox(out, self.frame.size, raw)
        self.assertTrue(len(rows), "nothing was drawn")
        msg = f"text touches the panel edge at size {size}: {overrides}"
        self.assertLess(rows[-1], ph - 2, msg)
        self.assertGreater(cols[0], 1, msg)
        self.assertLess(cols[-1], pw - 2, msg)
        return size

    def test_paragraph(self):
        self.assertFits(text="She never expected the pill to work this fast. " * 4)

    def test_long_word_wraps_instead_of_overflowing(self):
        self.assertFits(text="Supercalifragilisticexpialidocious transformation")

    def test_url(self):
        self.assertFits(text="see https://www.deviantart.com/someuser/art/caption-part-5 now")

    def test_stroke_and_bold(self):
        self.assertFits(text="She never expected the pill to work. " * 3, stroke_w=4, bold=True)

    def test_vertical_layout(self):
        self.assertFits(text="She never expected the pill to work this fast. " * 3,
                        layout="vertical", width=120)

    def test_chat_long_word(self):
        self.assertFits(chat_messages=[
            {"text": "A" * 60 + "h", "sender": "them"},
            {"text": "just a vitamin I swear", "sender": "me"},
        ])

    def test_output_override_extremes(self):
        text = "She never expected the pill to work this fast. " * 3
        self.assertFits(text=text, output_override=True, output_size_pct=0)
        self.assertFits(text=text, output_override=True, output_size_pct=100)

    def test_fit_is_quarter_point(self):
        size = self.assertFits(text="Short caption")
        self.assertEqual(size * 4, int(size * 4))


class CompositeTests(unittest.TestCase):
    def test_output_floor(self):
        out = cc.build_composite(Image.new("RGBA", (300, 200)), text="x", cap_width=200,
                                 page_bg="#ffffff", font_family="Arial", font_size=20,
                                 font_color="#000000", padding=10)
        self.assertEqual(out.size, (1800, 720))

    def test_fractional_font_size_renders(self):
        kw = dict(text="Hello", cap_width=200, page_bg="#ffffff", font_family="Arial",
                  font_color="#000000", padding=10)
        a = cc.build_composite(Image.new("RGBA", (300, 200)), font_size=20, **kw)
        b = cc.build_composite(Image.new("RGBA", (300, 200)), font_size=20.5, **kw)
        self.assertNotEqual(a.tobytes(), b.tobytes())

    def test_overlay_cache_matches_frame_content(self):
        kw = dict(text="Hello", cap_width=200, page_bg="#ffffff", font_family="Arial",
                  font_size=20, font_color="#000000", padding=10, shadow=True)
        red = cc.build_composite(Image.new("RGBA", (300, 200), "red"), **kw)
        blue = cc.build_composite(Image.new("RGBA", (300, 200), "blue"), **kw)
        self.assertEqual(red.getpixel((5, 5))[:3], (255, 0, 0))
        self.assertEqual(blue.getpixel((5, 5))[:3], (0, 0, 255))

    def test_auto_watermark_height_scales_with_image(self):
        self.assertEqual(cc._auto_watermark_height((350, 242)), 36)
        self.assertEqual(cc._auto_watermark_height((2000, 1500)), 225)


class GifExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src.gif")
        self.durations = _make_gif(self.src)
        with Image.open(self.src) as frame:
            size = frame.size
        raw = _raw(size, text="She never expected the pill to work this fast.",
                   shadow=True)
        self.params, _ = cc._resolve_composite_kwargs(raw)

    def tearDown(self):
        self.tmp.cleanup()

    def _export(self, deferred: bool = False) -> str:
        out = os.path.join(self.tmp.name, "out.gif")
        job = os.path.join(self.tmp.name, "job.json")
        # A deferred (single-frame preview) source only knows frame 1's
        # timing, so the GUI sends durations=None and fps from that frame.
        durations = None if deferred else self.durations
        fps = 1000.0 / self.durations[0] if deferred else 25.0
        with open(job, "w", encoding="utf-8") as f:
            json.dump(dict(source_path=self.src, kind="gif", output_path=out,
                           as_mp4=False, params=self.params, fps=fps,
                           total_frames=len(self.durations), durations=durations,
                           gif_colors=None), f)
        cc._export_worker_main(job)
        return out

    def test_frame_timing_matches_source(self):
        out = self._export()
        with Image.open(out) as im:
            got = [f.info["duration"] for f in ImageSequence.Iterator(im)]
        self.assertEqual(got, self.durations)

    def test_deferred_source_keeps_per_frame_timing(self):
        out = self._export(deferred=True)
        with Image.open(out) as im:
            got = [f.info["duration"] for f in ImageSequence.Iterator(im)]
        self.assertEqual(got, self.durations)

    def test_static_panel_not_reencoded(self):
        out = self._export()
        with Image.open(out) as im:
            panel_x = im.width - round(im.width * 160 / (200 + 160))
            for i in range(1, im.n_frames):
                im.seek(i)
                right = im.tile[0].extents[2]
                self.assertLessEqual(right, panel_x + 2, f"frame {i} re-encodes the panel")

    def test_estimate_matches_export(self):
        out = self._export()
        with Image.open(self.src) as im:
            frames = [cc.build_composite(f.convert("RGBA"), **self.params)
                      for f in ImageSequence.Iterator(im)]
        est = cc._estimate_anim_bytes(frames, False, 25.0, self.durations, None)
        self.assertEqual(est, os.path.getsize(out))


class Mp4ToGifTimingTests(unittest.TestCase):
    """fps-timed sources: GIF stores centisecond delays and can't go below
    20ms, so total length has to be preserved by spreading the rounding and
    dropping frames above 50fps."""

    def _export(self, fps: float, n: int) -> tuple:
        import imageio.v2 as imageio
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.mp4")
            writer = imageio.get_writer(src, fps=fps, codec="libx264", quality=8)
            for i in range(n):
                im = Image.new("RGB", (192, 128), (40, 90, 160))
                ImageDraw.Draw(im).rectangle((i * 2, 30, i * 2 + 30, 70), fill=(250, 200, 30))
                writer.append_data(np.asarray(im))
            writer.close()
            out = os.path.join(tmp, "out.gif")
            job = os.path.join(tmp, "job.json")
            params, _ = cc._resolve_composite_kwargs(_raw((192, 128), text="caption",
                                                          source_is_gif=False))
            with open(job, "w", encoding="utf-8") as f:
                json.dump(dict(source_path=src, kind="mp4", output_path=out, as_mp4=False,
                               params=params, fps=fps, total_frames=n, durations=None,
                               gif_colors=None), f)
            cc._export_worker_main(job)
            with Image.open(out) as im:
                delays = [f.info["duration"] for f in ImageSequence.Iterator(im)]
        return delays

    def test_30fps_keeps_total_length(self):
        delays = self._export(30.0, 30)
        self.assertEqual(sum(delays), 1000)
        self.assertEqual(len(delays), 30)

    def test_60fps_drops_frames_but_keeps_length(self):
        delays = self._export(60.0, 60)
        self.assertEqual(sum(delays), 1000)
        self.assertEqual(len(delays), 30)
        self.assertGreaterEqual(min(delays), 20)


if __name__ == "__main__":
    unittest.main()
