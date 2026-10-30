"""Face-aware framing and text placement helpers (no detector needed).

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import caption_creator as cc  # noqa: E402


class FaceHelperTests(unittest.TestCase):
    def test_zoom_to_face_centers_it(self):
        face = (0.6, 0.2, 0.7, 0.3)                     # small face, upper right
        zoom, px, py = cc._zoom_to_face(face)
        self.assertGreater(zoom, 2)
        # Where does the face's center land after _apply_zoom?
        frame = Image.new("RGB", (1000, 1000), "black")
        frame.paste((255, 0, 0), (640, 240, 660, 260))    # mark the face center
        out = cc._apply_zoom(frame, zoom, px, py)
        box = out.convert("L").point(lambda v: 255 if v > 40 else 0).getbbox()
        cx, cy = (box[0] + box[2]) / 2 / 1000, (box[1] + box[3]) / 2 / 1000
        self.assertAlmostEqual(cx, 0.5, delta=0.03)
        self.assertLess(cy, 0.5)                        # a little above center

    def test_big_face_needs_no_zoom(self):
        self.assertEqual(cc._zoom_to_face((0.1, 0.0, 0.9, 0.9)), (1.0, 0.0, 0.0))

    def test_box_moves_off_a_face_to_the_nearest_clear_spot(self):
        faces = [(0.3, 0.4, 0.6, 0.6)]
        y = cc._place_off_faces((0.1, 0.45, 0.8, 0.1), faces)
        self.assertTrue(y + 0.1 <= 0.4 - 0.03 or y >= 0.6 + 0.03, y)
        self.assertEqual(cc._place_off_faces((0.1, 0.8, 0.8, 0.1), faces), 0.8)  # already clear
        self.assertEqual(cc._place_off_faces((0.0, 0.0, 1.0, 1.0), faces), 0.0)  # nowhere clear


if __name__ == "__main__":
    unittest.main()
