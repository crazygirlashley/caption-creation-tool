"""App background image/GIF rendering (wallpaper.py).

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wallpaper  # noqa: E402


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _png(self, size=(40, 30), color=(200, 50, 50)):
        path = os.path.join(self.tmp.name, "bg.png")
        Image.new("RGB", size, color).save(path)
        return path

    def test_fit_covers_area_and_tile_repeats(self):
        img = Image.new("RGB", (40, 30), (255, 0, 0))
        img.paste((0, 0, 255), (0, 0, 20, 30))  # left half blue
        path = os.path.join(self.tmp.name, "half.png")
        img.save(path)
        w = wallpaper.load(path)
        fit = w.render(0, (400, 100), "fit")
        self.assertEqual(fit.size, (400, 100))
        tile = w.render(0, (100, 60), "tile")
        # Tiles repeat every 40px: same pixel one tile to the right.
        self.assertEqual(tile.getpixel((5, 5)), tile.getpixel((45, 5)))
        self.assertEqual(tile.getpixel((5, 5)), (0, 0, 255))

    def test_dim_blends_toward_color(self):
        w = wallpaper.load(self._png(color=(200, 200, 200)))
        out = w.render(0, (10, 10), "fit", dim=0.5, dim_color=(0, 0, 0))
        self.assertEqual(out.getpixel((5, 5)), (100, 100, 100))

    def test_long_gif_is_thinned_but_keeps_its_length(self):
        path = os.path.join(self.tmp.name, "anim.gif")
        frames = [Image.new("RGB", (20, 20), (i * 3, 0, 0)) for i in range(80)]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=50, loop=0)
        w = wallpaper.load(path)
        self.assertTrue(w.animated)
        self.assertEqual(len(w.frames), wallpaper.MAX_FRAMES)
        self.assertEqual(sum(w.durations), 80 * 50)

    def test_missing_or_broken_file_is_ignored(self):
        self.assertIsNone(wallpaper.load(os.path.join(self.tmp.name, "nope.png")))
        bad = os.path.join(self.tmp.name, "bad.gif")
        with open(bad, "wb") as f:
            f.write(b"GIF89a junk")
        self.assertIsNone(wallpaper.load(bad))


if __name__ == "__main__":
    unittest.main()
