"""Find Online (web_lookup) tests — offline: no requests to the real sites.

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_lookup as wl  # noqa: E402


class TitleFilterTests(unittest.TestCase):
    def test_minor_framed_titles_are_excluded(self):
        for title in ("Top 10 Child Stars Who Grew Up Hot",
                      "Teen Girls At The Beach",
                      "Childhood Photos Of Models",
                      "Cute Kids And Their Moms",
                      "High School Reunion Photos"):
            self.assertTrue(wl._is_excluded_title(title), title)

    def test_ordinary_person_galleries_pass(self):
        for title in ("32 Stunning Blonde Women Who Define Classic Beauty",
                      "25 Adorable Brunette Girls",
                      "Bikini Girls (21 pics)"):
            self.assertFalse(wl._is_excluded_title(title), title)

    def test_keywords_never_request_minors(self):
        self.assertNotIn("teen", wl.extract_keywords("a petite teen blonde girl in a dress"))


class RankingTests(unittest.TestCase):
    def test_synonyms_match_gender_terms(self):
        results = [{"title": "Funny Pranks At Work"},
                   {"title": "25 Adorable Brunette Girls"}]
        ranked = wl._rank_results(results, "brunette woman")
        self.assertEqual(ranked[0]["title"], "25 Adorable Brunette Girls")

    def test_person_galleries_rank_above_listicles(self):
        results = [{"title": "Blonde Memes That Are Too Funny"},
                   {"title": "Sexy Blonde Bombshells Ignite The Scene"}]
        ranked = wl._rank_results(results, "blonde")
        self.assertEqual(ranked[0]["title"], "Sexy Blonde Bombshells Ignite The Scene")

    def test_primary_term_still_comes_first(self):
        results = [{"title": "Sexy Gorgeous Hot Girls"},
                   {"title": "Redhead Memes"}]
        self.assertEqual(wl._rank_results(results, "redhead")[0]["title"], "Redhead Memes")

    def test_hair_color_spelling_variants(self):
        self.assertTrue(wl._term_pattern("blonde").search("hot blond girls"))
        self.assertTrue(wl._term_pattern("redhead").search("gingers at the beach"))


class SimilarQueryTests(unittest.TestCase):
    def test_uses_title_attributes_and_folds_plurals(self):
        self.assertEqual(wl.similar_query("25 Adorable Brunette Girls"), "brunette girls")
        self.assertEqual(wl.similar_query("Beauty Brunettes (35 pics)"), "brunette")
        self.assertEqual(wl.similar_query("Hot Babes (30 pics)"), "babes")


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.orig = (wl._search_barnorama, wl._search_acidcow, wl._search_sexcom)
        wl._search_sexcom = lambda q, page: []  # keep these tests offline

    def tearDown(self):
        wl._search_barnorama, wl._search_acidcow, wl._search_sexcom = self.orig

    def test_multiword_query_also_searches_distinctive_term_and_dedupes(self):
        queries = []

        def fake_barnorama(q, page):
            queries.append(q)
            return [{"title": "Brunette Girls", "url": "https://b/1", "thumb_url": ""}]

        def fake_acidcow(q, page):
            queries.append(q)
            return [{"title": "Beauty Brunettes", "url": f"https://a/{q}", "thumb_url": ""}]

        wl._search_barnorama, wl._search_acidcow = fake_barnorama, fake_acidcow
        results = wl.search("brunette woman")
        self.assertEqual(sorted(set(queries)), ["brunette", "brunette woman"])
        urls = [r["url"] for r in results]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn("https://b/1", urls)

    def test_failing_source_does_not_block_the_other(self):
        import requests

        def broken(q, page):
            raise requests.ConnectionError("down")

        wl._search_barnorama = broken
        wl._search_acidcow = lambda q, page: [{"title": "Bikini Girls", "url": "https://a/1",
                                               "thumb_url": ""}]
        self.assertEqual([r["url"] for r in wl.search("bikini")], ["https://a/1"])


class SexComTests(unittest.TestCase):
    """_search_sexcom against a fake session — no network."""

    class _Resp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def setUp(self):
        self.calls = []
        self.orig = wl._SESSION.get

        def fake_get(url, params=None, timeout=None):
            self.calls.append((url, dict(params or {})))
            return self._Resp({"data": [
                {"uri": "/images/pinporn/2020/01/01/1.jpg", "title": "Gorgeous blonde in red"},
                {"uri": "/images/pinporn/2020/01/01/2.jpg", "title": "Blonde #teen #petite"},
                {"uri": "", "title": "no image"},
            ]})
        wl._SESSION.get = fake_get

    def tearDown(self):
        wl._SESSION.get = self.orig

    def test_groups_pins_and_filters_titles(self):
        posts = wl._search_sexcom("blonde woman", 1)
        self.assertEqual([p["title"] for p in posts],
                         ["sex.com pictures: blonde (1)", "sex.com GIFs: blonde (1)"])
        self.assertEqual(self.calls[0][1]["search"], "blonde")
        self.assertEqual(self.calls[0][1]["sexual-orientation"], "straight")
        items = wl.fetch_gallery(posts[0]["url"])
        self.assertEqual(items[0]["url"], "https://cdn.sex.com/images/pinporn/2020/01/01/1.jpg")
        self.assertTrue(items[0]["thumb"].endswith("?width=300"))
        self.assertTrue(wl.fetch_gallery(posts[1]["url"])[0]["gif"])

    def test_male_subjects_use_gay_orientation(self):
        wl._search_sexcom("muscular men", 1)
        self.assertEqual(self.calls[0][1]["sexual-orientation"], "gay")
        self.assertEqual(self.calls[0][1]["search"], "muscular")


class GalleryItemTests(unittest.TestCase):
    def test_gif_detection_and_thumb_fallback(self):
        item = wl._gallery_item("https://cdn.x/pics/a.GIF")
        self.assertTrue(item["gif"])
        self.assertEqual(item["thumb"], item["url"])
        self.assertFalse(wl._gallery_item("https://x/a.jpg", "https://x/a-240.jpg")["gif"])

    def test_srcset_min_width(self):
        from bs4 import BeautifulSoup
        img = BeautifulSoup('<img src="full.jpg" srcset="full.jpg 640w, small.jpg 150w, '
                            'mid.jpg 240w">', "html.parser").img
        self.assertEqual(wl._smallest_srcset_url(img), "small.jpg")
        self.assertEqual(wl._smallest_srcset_url(img, 200), "mid.jpg")
        self.assertEqual(wl._smallest_srcset_url(img, 9999), "full.jpg")


class FaceCountTests(unittest.TestCase):
    def test_blank_image_has_no_faces(self):
        faces = wl.count_faces(Image.new("RGB", (300, 300), (200, 180, 160)))
        self.assertIn(faces, (0, None))  # None only if OpenCV isn't installed


if __name__ == "__main__":
    unittest.main()
