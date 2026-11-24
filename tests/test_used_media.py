import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw

import used_media


def _picture(seed: int) -> Image.Image:
    img = Image.new("RGB", (320, 240), (seed * 40 % 255, 90, 160))
    d = ImageDraw.Draw(img)
    for i in range(6):
        x = (seed * 53 + i * 47) % 280
        d.ellipse((x, i * 35, x + 60, i * 35 + 50), fill=(255 - i * 30, seed * 20 % 255, 40))
    return img


class UsedMediaTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "used_media.csv")
        patcher = mock.patch.object(used_media, "PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)
        used_media._cache["mtime"] = None

    def test_nothing_is_used_without_a_log(self):
        self.assertFalse(used_media.is_used("https://x.com/a.gif", _picture(1)))

    def test_url_match_ignores_scheme_www_and_query(self):
        used_media.record("https://www.cdn.example.com/a/b.gif?width=200")
        self.assertTrue(used_media.is_used("http://cdn.example.com/a/b.gif"))
        self.assertFalse(used_media.is_used("https://cdn.example.com/a/c.gif"))

    def test_resized_recompressed_copy_matches_but_other_picture_doesnt(self):
        used_media.record(None, _picture(1), title="one")
        copy = _picture(1).resize((130, 97))
        tmp = os.path.join(self.dir.name, "copy.jpg")
        copy.save(tmp, quality=55)
        self.assertTrue(used_media.is_used(img=Image.open(tmp)))
        self.assertFalse(used_media.is_used(img=_picture(4)))

    def test_record_skips_duplicates(self):
        self.assertTrue(used_media.record("https://a.com/1.gif", _picture(2)))
        self.assertFalse(used_media.record("https://a.com/1.gif"))
        with open(self.path, newline="", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 1)

    def test_old_log_without_hash_column_is_upgraded(self):
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            f.write("date,url,found_on,title,batch,output_file\n"
                    "2026-11-24,https://a.com/old.gif,site,Old,first,out.gif\n")
        used_media.record(None, _picture(3), title="New")
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        self.assertEqual(reader.fieldnames, used_media.FIELDS)
        self.assertEqual([r["title"] for r in rows], ["Old", "New"])
        self.assertTrue(used_media.is_used("https://a.com/old.gif"))
        self.assertTrue(used_media.is_used(img=_picture(3)))

    def test_fingerprint_uses_first_frame_of_an_animation(self):
        frames = [_picture(5), _picture(6)]
        path = os.path.join(self.dir.name, "anim.gif")
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
        anim = Image.open(path)
        anim.seek(1)
        self.assertEqual(used_media.fingerprint(anim),
                         used_media.fingerprint(Image.open(path).convert("RGB")))


if __name__ == "__main__":
    unittest.main()
