"""Find Online's GIFs-only and mature filters, blocked searches, and AI
search ideas (no network: the sources are replaced with fakes).

Run from the repo root with:  python -m unittest discover tests
"""

import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_writer  # noqa: E402
import web_lookup  # noqa: E402
from test_ai_animation import _PlanHandler  # noqa: E402


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.saved = (web_lookup._search_barnorama, web_lookup._search_acidcow,
                      web_lookup._search_sexcom, web_lookup._search_xgroovy_gifs,
                      web_lookup._search_xgroovy_photos, web_lookup._search_porngifs,
                      web_lookup._search_redgifs, web_lookup._search_pornpics,
                      web_lookup._search_commons)
        self.calls = []
        self.mature_calls = []

        def mature_source(name):
            def fake(q, page):
                self.mature_calls.append(name)
                return [{"title": f"{name}: redhead (3)", "url": name, "thumb_url": "",
                         "mature": True}]
            return fake
        web_lookup._search_xgroovy_gifs = mature_source("xg")
        web_lookup._search_xgroovy_photos = mature_source("xp")
        web_lookup._search_porngifs = mature_source("pg")
        web_lookup._search_redgifs = mature_source("rg")
        web_lookup._search_pornpics = mature_source("pp")
        self.commons_calls = []

        def commons(q, page):
            self.commons_calls.append(q)
            return [{"title": "Wikimedia Commons: redhead (4)", "url": "cm", "thumb_url": ""}]
        web_lookup._search_commons = commons

        def barn(q, page):
            return [{"title": "Sexy Redhead Babes", "url": "b1", "thumb_url": ""},
                    {"title": "Redhead NSFW Nudes", "url": "b2", "thumb_url": ""}]

        def acid(q, page):
            return [{"title": "Redheads in Lingerie", "url": "a1", "thumb_url": ""}]

        def sexcom(q, page, gifs_only=False):
            self.calls.append(gifs_only)
            return [{"title": "sex.com GIFs: redhead (5)", "url": "s1", "thumb_url": ""}]

        web_lookup._search_barnorama = barn
        web_lookup._search_acidcow = acid
        web_lookup._search_sexcom = sexcom

    def tearDown(self):
        (web_lookup._search_barnorama, web_lookup._search_acidcow,
         web_lookup._search_sexcom, web_lookup._search_xgroovy_gifs,
         web_lookup._search_xgroovy_photos, web_lookup._search_porngifs,
         web_lookup._search_redgifs, web_lookup._search_pornpics,
         web_lookup._search_commons) = self.saved

    def urls(self, **kw):
        return sorted(r["url"] for batch in web_lookup.search_stream("redhead", 1, **kw)
                      for r in batch)

    def test_mature_on_includes_everything(self):
        self.assertEqual(self.urls(), ["a1", "b1", "b2", "cm", "pg", "pp", "rg", "s1", "xg",
                                       "xp"])

    def test_mature_off_skips_the_explicit_sites_and_titles(self):
        self.assertEqual(self.urls(mature=False), ["a1", "b1", "cm"])
        self.assertEqual(self.calls, [])               # sex.com not even asked
        self.assertEqual(self.mature_calls, [])        # nor the other explicit sites

    def test_gifs_only_asks_for_gifs(self):
        self.urls(gifs_only=True)
        self.assertEqual(self.calls, [True])
        self.assertNotIn("xp", self.mature_calls)      # xgroovy photo albums skipped
        self.assertNotIn("pp", self.mature_calls)      # pornpics photo galleries skipped
        self.assertIn("rg", self.mature_calls)         # redgifs clips searched
        self.assertEqual(self.commons_calls, [])       # Commons (photos) skipped

    def test_searches_for_minors_are_never_run(self):
        for q in ("teen redhead", "loli", "schoolgirl", "16 year old", "high school girl"):
            self.assertTrue(web_lookup.is_blocked_query(q), q)
            self.assertEqual(list(web_lookup.search_stream(q)), [], q)
        self.assertFalse(web_lookup.is_blocked_query("curvy redhead lingerie"))


class _FakeResp:
    def __init__(self, text="", data=None):
        self.text, self._data = text, data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class MatureSourceTests(unittest.TestCase):
    def setUp(self):
        self.saved_get = web_lookup._SESSION.get
        self.saved_tags = web_lookup._porngifs_tags

    def tearDown(self):
        web_lookup._SESSION.get = self.saved_get
        web_lookup._porngifs_tags = self.saved_tags

    def test_xgroovy_gifs_parsed_and_labelled_mature(self):
        html = ('<div class="item"><div class="gif-wrap" data-full="https://x/g/1_1080p_gif.mp4/" '
                'data-poster="https://i/1.jpg"></div><div class="title-wrapper">'
                '<a class="title-link" title="Curvy redhead">x</a></div></div>'
                '<div class="item"><div class="gif-wrap" data-full="https://x/g/2.mp4/" '
                'data-poster="https://i/2.jpg"></div><a class="title-link" '
                'title="Teen redhead">x</a></div>')
        web_lookup._SESSION.get = lambda url, **k: _FakeResp(html)
        posts = web_lookup._search_xgroovy_gifs("redhead", 1)
        self.assertTrue(posts[0]["mature"])
        items = web_lookup.fetch_gallery(posts[0]["url"])
        self.assertEqual([i["url"] for i in items], ["https://x/g/1_1080p_gif.mp4/"])  # no teen
        self.assertTrue(items[0]["gif"])

    def test_xgroovy_album_keeps_only_its_own_pictures(self):
        html = ('<img src="https://photos.xgroovy.com/contents/albums/main/420x999/823000/823616/1.jpg">'
                '<img src="https://photos.xgroovy.com/contents/albums/main/420x999/823000/823616/2.jpg">'
                '<img src="https://photos.xgroovy.com/contents/albums/main/420x999/179000/179040/9.jpg">')
        web_lookup._SESSION.get = lambda url, **k: _FakeResp(html)
        items = web_lookup.fetch_gallery("https://xgroovy.com/photos/823616/some-album/")
        self.assertEqual([i["url"] for i in items], [
            "https://photos.xgroovy.com/contents/albums/sources/823000/823616/1.jpg",
            "https://photos.xgroovy.com/contents/albums/sources/823000/823616/2.jpg"])

    def test_porngifs_tags_matched_from_the_query(self):
        web_lookup._porngifs_tags = {"maid", "blonde", "ginger", "lingerie", "jesse jane",
                                     "teen"}
        self.assertEqual(web_lookup._porngifs_tags_for("redhead maid"), ["ginger", "maid"])
        self.assertEqual(web_lookup._porngifs_tags_for("blondes"), ["blonde"])
        self.assertEqual(web_lookup._porngifs_tags_for("jesse jane"), [])   # a big name
        self.assertEqual(web_lookup._porngifs_tags_for("teen"), [])

    def test_porngifs_results(self):
        web_lookup._porngifs_tags = {"maid"}
        web_lookup._SESSION.get = lambda url, **k: _FakeResp(
            data=[{"src": "38828"}, {"src": "bad"}, {"src": "5351"}])
        posts = web_lookup._search_porngifs("maid", 1)
        self.assertEqual(posts[0]["title"], "porngifs.com: maid (4)")   # two requests of 2
        self.assertTrue(posts[0]["mature"])
        items = web_lookup.fetch_gallery(posts[0]["url"])
        self.assertEqual(items[0]["url"], "https://cdn.porngifs.com/img/38828")

    def test_redgifs_clips_and_skipped_tags(self):
        def get(url, **k):
            if url.endswith("/auth/temporary"):
                return _FakeResp(data={"token": "t"})
            self.assertEqual(k["headers"]["Authorization"], "Bearer t")
            return _FakeResp(data={"gifs": [
                {"tags": ["Redhead", "Lingerie"],
                 "urls": {"hd": "https://m/A.mp4", "thumbnail": "https://m/A-mobile.jpg"}},
                {"tags": ["Redhead", "Teen"],
                 "urls": {"hd": "https://m/B.mp4", "thumbnail": "https://m/B.jpg"}},
                {"tags": ["Hentai", "Redhead"],
                 "urls": {"hd": "https://m/C.mp4", "thumbnail": "https://m/C.jpg"}},
                {"tags": ["Redhead"],
                 "urls": {"hd": "https://m/D-large.jpg", "thumbnail": "https://m/D-small.jpg"}},
            ]})
        web_lookup._SESSION.get = get
        web_lookup._redgifs_token = None
        posts = web_lookup._search_redgifs("redhead", 1)
        self.assertEqual(posts[0]["title"], "redgifs: redhead (2)")
        self.assertTrue(posts[0]["mature"])
        items = web_lookup.fetch_gallery(posts[0]["url"])
        self.assertEqual([(i["url"], i["gif"]) for i in items],
                         [("https://m/A.mp4", True), ("https://m/D-large.jpg", False)])

    def test_pornpics_galleries_skip_teens_and_celebrities_only(self):
        web_lookup._SESSION.get = lambda url, **k: _FakeResp(data=[
            {"desc": "European girls with model-like bodies pose naked outdoors 123",
             "g_url": "https://www.pornpics.com/galleries/a-1/", "t_url": "https://c/1.jpg"},
            {"desc": "Redheaded teen Megan Murkovski poses 456",
             "g_url": "https://www.pornpics.com/galleries/b-2/", "t_url": "https://c/2.jpg"},
            {"desc": "Curvy amateur Emma Magnolia strips 789",
             "g_url": "https://www.pornpics.com/galleries/c-3/", "t_url": "https://c/3.jpg"},
            {"desc": "Taylor Swift lookalike poses 999",
             "g_url": "https://www.pornpics.com/galleries/d-4/", "t_url": "https://c/4.jpg"},
        ])
        posts = web_lookup._search_pornpics("redhead", 1)
        self.assertEqual([p["title"] for p in posts],
                         ["European girls with model-like bodies pose naked outdoors",
                          "Curvy amateur Emma Magnolia strips"])
        html = ('<a class="rel-link" href="https://cdni.pornpics.com/1280/x/1.jpg"></a>'
                '<a class="rel-link" href="https://cdni.pornpics.com/1280/x/2.jpg"></a>'
                '<div id="tiles"><img data-src="https://cdni.pornpics.com/460/x/1.jpg">'
                '<img data-src="https://cdni.pornpics.com/460/x/2.jpg"></div>')
        web_lookup._SESSION.get = lambda url, **k: _FakeResp(html)
        items = web_lookup.fetch_gallery(posts[0]["url"])
        self.assertEqual(items[1], {"url": "https://cdni.pornpics.com/1280/x/2.jpg",
                                    "thumb": "https://cdni.pornpics.com/460/x/2.jpg",
                                    "gif": False})

    def test_commons_files(self):
        web_lookup._SESSION.get = lambda url, **k: _FakeResp(data={"query": {"pages": {
            "1": {"index": 2, "title": "File:Redhead in Central Park.jpg", "imageinfo": [{
                "url": "https://upload.wikimedia.org/a/Redhead.jpg?utm=x",
                "thumburl": "https://thumb.wikimedia.org/a/330px-Redhead.jpg?utm=y",
                "mime": "image/jpeg"}]},
            "2": {"index": 1, "title": "File:Scan.pdf", "imageinfo": [{
                "url": "https://upload.wikimedia.org/a/Scan.pdf", "thumburl": "https://t/x.jpg",
                "mime": "application/pdf"}]}}}})
        posts = web_lookup._search_commons("redhead", 1)
        self.assertEqual(posts[0]["title"], "Wikimedia Commons: redhead (1)")
        self.assertFalse(posts[0].get("mature"))
        items = web_lookup.fetch_gallery(posts[0]["url"])
        self.assertEqual(items[0]["url"], "https://upload.wikimedia.org/a/Redhead.jpg")
        self.assertEqual(items[0]["thumb"], "https://thumb.wikimedia.org/a/330px-Redhead.jpg")

    def test_download_file_types(self):
        saved = web_lookup.download_bytes
        web_lookup.download_bytes = lambda url: b"x"
        try:
            for url, ext in (("https://x/get_file/1/a/1_1080p_gif.mp4/", ".mp4"),
                             ("https://cdn.porngifs.com/img/38828", ".gif"),
                             ("https://x/a/b.png", ".png"), ("https://x/a/b", ".jpg")):
                path = web_lookup.download_to_tempfile(url)
                try:
                    self.assertTrue(path.endswith(ext), (url, path))
                finally:
                    os.unlink(path)
        finally:
            web_lookup.download_bytes = saved


class SuggestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _PlanHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.settings = dict(ai_writer.DEFAULTS, model="m",
                            base_url=f"http://127.0.0.1:{cls.server.server_address[1]}/v1")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_ideas_are_cleaned_and_minors_dropped(self):
        _PlanHandler.reply = ('Here you go: ["Blonde Bimbo", "blonde bimbo", "teen blonde", '
                              '"curvy maid outfit in a big mansion", "pink lingerie"]')
        ideas = ai_writer.suggest_searches("He took the pill and woke up a blonde bimbo.",
                                           self.settings)
        self.assertEqual(ideas, ["blonde bimbo", "curvy maid outfit in", "pink lingerie"])
        sent = _PlanHandler.seen[-1]["messages"]
        self.assertIn("never use words for minors", sent[0]["content"])

    def test_long_story_keeps_start_and_end(self):
        _PlanHandler.reply = '["redhead"]'
        story = "START " + "x " * 5000 + " THE END"
        ai_writer.suggest_searches(story, self.settings)
        text = _PlanHandler.seen[-1]["messages"][1]["content"]
        self.assertTrue(text.startswith("START") and text.endswith("THE END"))
        self.assertLess(len(text), 6100)

    def test_empty_story(self):
        with self.assertRaises(ai_writer.AIWriterError):
            ai_writer.suggest_searches("  ", self.settings)


if __name__ == "__main__":
    unittest.main()
