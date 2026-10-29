"""The GIF encoder's fast paths keep their guarantees.

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageSequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402


def _frames(n=8):
    """A moving photo-like area on the left, a static "caption panel" right."""
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (120, 400, 3), dtype=np.uint8)
    out = []
    for i in range(n):
        arr = np.zeros((120, 400, 3), np.uint8)
        arr[:, :200] = np.roll(base[:, :200], i * 5, axis=1)      # moves
        arr[:, 200:] = (250, 200, 220)                              # static panel
        arr[40:80, 230:370] = (20, 20, 20)                          # static "text"
        out.append(Image.fromarray(arr))
    return out


class GifEncoderTests(unittest.TestCase):
    def _decode(self, path):
        with Image.open(path) as g:
            return [np.asarray(f.convert("RGB")) for f in ImageSequence.Iterator(g)]

    def test_dithered_static_panel_is_identical_every_frame(self):
        frames = _frames()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.gif")
            cc._write_gif(path, iter(frames), frames[::2], 40.0, None, dither=True)
            decoded = self._decode(path)
        self.assertEqual(len(decoded), len(frames))
        for d in decoded[1:]:
            np.testing.assert_array_equal(d[:, 200:], decoded[0][:, 200:])
        for d, f in zip(decoded, frames):     # and the picture is close to the source
            self.assertLess(np.abs(d.astype(int) - np.asarray(f, int)).mean(), 25)

    def test_unsampled_change_in_static_area_is_still_encoded(self):
        frames = _frames()
        changed = np.array(frames[5])
        changed[10:20, 300:320] = (0, 255, 0)          # outside the sampled moving box
        frames[5] = Image.fromarray(changed)
        samples = [frames[0], frames[2], frames[4]]    # frame 5 not sampled
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.gif")
            cc._write_gif(path, iter(frames), samples, 40.0, None, dither=True)
            decoded = self._decode(path)
        r, g, b = decoded[5][15, 310]
        self.assertTrue(g > 150 and r < 100, (r, g, b))

    def test_subsampled_palette_keeps_the_panel_colors(self):
        frames = [f.resize((2400, 720), Image.NEAREST) for f in _frames(12)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "n.gif")
            cc._write_gif(path, iter(frames), frames, [50] * 12, None, dither=False)
            decoded = self._decode(path)
        self.assertEqual(tuple(decoded[0][10, 2000]), (250, 200, 220))
        self.assertEqual(tuple(decoded[0][360, 1800]), (20, 20, 20))


if __name__ == "__main__":
    unittest.main()
