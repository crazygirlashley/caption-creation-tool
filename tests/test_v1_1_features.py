"""Picture-aware AI writing, box snapping and styles, frame dropping and
the theme editor.

Run from the repo root with:  python -m unittest discover tests
"""

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_writer  # noqa: E402
import animate  # noqa: E402
import caption_creator as cc  # noqa: E402
import themes  # noqa: E402

REPO_THEMES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "themes")


class _VisionHandler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _VisionHandler.seen.append(body)
        if body["model"] == "text-only":
            data = b'{"error": {"message": "model does not support images"}}'
            self.send_response(400)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in ("A woman with long  red hair ", "in a pink\n dress."):
            event = {"choices": [{"delta": {"content": c}}]}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class PictureAwareWritingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _VisionHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.settings = dict(ai_writer.DEFAULTS, model="writer",
                            base_url=f"http://127.0.0.1:{cls.server.server_address[1]}/v1",
                            vision_model="moondream")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_describe_sends_picture_to_the_vision_model(self):
        _VisionHandler.seen.clear()
        img = Image.new("RGB", (2000, 1000), (200, 50, 120))
        text = ai_writer.describe_image(img, self.settings)
        self.assertEqual(text, "A woman with long red hair in a pink dress.")
        body = _VisionHandler.seen[-1]
        self.assertEqual(body["model"], "moondream")      # not the writing model
        parts = body["messages"][0]["content"]
        url = next(p for p in parts if p["type"] == "image_url")["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        sent = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        self.assertEqual(max(sent.size), 768)              # shrunk before sending

    def test_no_vision_model_is_a_readable_error(self):
        with self.assertRaises(ai_writer.AIWriterError):
            ai_writer.describe_image(Image.new("RGB", (8, 8)),
                                     dict(self.settings, vision_model=""))

    def test_model_that_cannot_see_says_so(self):
        with self.assertRaises(ai_writer.AIWriterError) as ctx:
            ai_writer.describe_image(Image.new("RGB", (8, 8)),
                                     dict(self.settings, vision_model="text-only"))
        self.assertIn("can see images", str(ctx.exception))

    def test_story_prompt_includes_the_description(self):
        msgs = ai_writer.build_messages("She wakes up changed.", 120, "A redhead in pink.")
        self.assertIn("A redhead in pink.", msgs[1]["content"])
        self.assertTrue(msgs[1]["content"].endswith("She wakes up changed."))
        self.assertEqual(ai_writer.build_messages("x", 120)[1]["content"], "x")

    def test_vision_setup_saves_the_vision_role(self):
        saved = {}
        orig = (ai_writer.ollama_running, ai_writer.ollama_models, ai_writer.save_settings,
                ai_writer.load_settings)
        try:
            ai_writer.ollama_running = lambda *a, **k: True
            ai_writer.ollama_models = lambda *a, **k: ["moondream:latest"]
            ai_writer.save_settings = saved.update
            ai_writer.load_settings = lambda: dict(ai_writer.DEFAULTS, model="hermes3:8b")
            out = ai_writer.setup_ollama("moondream", lambda *a: None, role="vision_model")
        finally:
            (ai_writer.ollama_running, ai_writer.ollama_models, ai_writer.save_settings,
             ai_writer.load_settings) = orig
        self.assertEqual(out["vision_model"], "moondream")
        self.assertEqual(out["model"], "hermes3:8b")       # writing model untouched
        self.assertEqual(saved["vision_model"], "moondream")


class SnapTests(unittest.TestCase):
    def test_box_center_snaps_to_picture_center(self):
        targets = cc._snap_targets(0, 400, [])
        shift, guides = cc._snap_shift((150, 246), targets, 7)   # center 198
        self.assertEqual(shift, 2)
        self.assertEqual(guides, [200])

    def test_edge_snaps_to_margin_and_other_boxes(self):
        targets = cc._snap_targets(0, 1000, [(500, 700)])
        self.assertEqual(cc._snap_shift((34, 200), targets, 7), (-4, [30]))     # 3% margin
        shift, guides = cc._snap_shift((705, 900), targets, 7)
        self.assertEqual((shift, guides), (-5, [700]))                          # other box

    def test_far_from_everything_doesnt_snap(self):
        targets = cc._snap_targets(0, 1000, [])
        self.assertEqual(cc._snap_shift((100, 160), targets, 7), (0.0, []))


class BoxStyleTests(unittest.TestCase):
    def test_style_keeps_text_place_and_scales_size_with_picture(self):
        src = dict(cc._TB_DEFAULT, text="Narrator", x=0.1, y=0.2, size=40, font="Georgia",
                   color="#ff00aa", shape="speech", bg="#ffffff")
        style = cc._tb_style_of(src, pic_h=1000)
        self.assertNotIn("text", style)
        self.assertNotIn("x", style)
        target = dict(text="Hello", x=0.5, y=0.6, size=12)
        cc._tb_apply_style(target, style, pic_h=2000)
        self.assertEqual((target["text"], target["x"], target["y"]), ("Hello", 0.5, 0.6))
        self.assertEqual(target["size"], 80.0)       # same share of a taller picture
        self.assertEqual((target["font"], target["color"], target["shape"]),
                         ("Georgia", "#ff00aa", "speech"))

    def test_style_survives_json(self):
        style = cc._tb_style_of(dict(cc._TB_DEFAULT), pic_h=None)
        self.assertEqual(json.loads(json.dumps(style)), style)


class FrameStepTests(unittest.TestCase):
    def test_merged_delays_keep_the_length(self):
        self.assertEqual(cc._merge_delays([80] * 7, 2), [160, 160, 160, 80])
        self.assertEqual(sum(cc._merge_delays([30, 40, 50, 60, 70], 3)), 250)
        self.assertEqual(cc._merge_delays([10, 20], 1), [10, 20])

    def test_export_worker_keeps_every_second_frame(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "src.gif")
            frames = [Image.new("RGB", (120, 90), (i * 30, 40, 200 - i * 20)) for i in range(8)]
            frames[0].save(src, save_all=True, append_images=frames[1:], duration=80, loop=0)
            out = os.path.join(tmp, "out.gif")
            job = dict(source_path=src, kind="gif", output_path=out, as_mp4=False,
                       params=dict(text="Hi", cap_width=60, page_bg="#ffffff",
                                   font_family="Arial", font_size=12, font_color="#000000",
                                   padding=4),
                       fps=12.5, total_frames=8, durations=[80] * 8, gif_colors=None,
                       trim=[0, 7], speed=1.0, frame_step=2)
            job_path = os.path.join(tmp, "job.json")
            with open(job_path, "w") as f:
                json.dump(job, f)
            cc._export_worker_main(job_path)
            with Image.open(out) as im:
                delays = []
                for i in range(im.n_frames):
                    im.seek(i)
                    delays.append(im.info["duration"])
            self.assertEqual(len(delays), 4)
            self.assertEqual(sum(delays), 640)        # still the whole 0.64s
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AnimateTests(unittest.TestCase):
    def setUp(self):
        self.a = Image.new("RGB", (400, 300), (200, 40, 120))
        self.b = Image.new("RGB", (300, 500), (20, 200, 90))

    def test_every_still_effect(self):
        for key in set(animate.STILL_EFFECTS.values()):
            frames, durs = animate.still_frames(self.a, key, 2, 10)
            self.assertEqual(len(frames), len(durs))
            self.assertTrue(all(f.size == (400, 300) for f in frames), key)

    def test_bounce_returns_to_the_start(self):
        frames, durs = animate.still_frames(self.a, "zoom_in", 2, 10, bounce=True)
        self.assertEqual(len(frames), 2 * 20 - 2)      # forwards, then back without repeats
        self.assertEqual(durs[0], 400)                 # a pause at each end

    def test_every_transition_goes_from_a_to_b(self):
        for key in set(animate.TRANSITIONS.values()):
            frames, durs = animate.transition_frames(self.a, self.b, key, 1, 1, 1, 10,
                                                     loop_back=False)
            self.assertEqual(frames[0].getpixel((200, 150)), (200, 40, 120), key)
            self.assertEqual(frames[-1].getpixel((200, 150)), (20, 200, 90), key)
            self.assertEqual((durs[0], durs[-1]), (1000, 1000), key)
            self.assertEqual(frames[-1].size, (400, 300), key)   # b cropped to a's shape

    def test_smoke_uses_the_chosen_color_and_covers_the_swap(self):
        frames, _ = animate.transition_frames(self.a, self.b, "smoke", 1, 2, 1, 10,
                                              loop_back=False, color="#0000ff")
        mid = frames[len(frames) * 4 // 10]               # end of the burst: all smoke
        r, g, b = mid.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
        self.assertGreater(b, max(r, g) + 60)

    def test_loop_back_ends_where_it_started(self):
        frames, _ = animate.transition_frames(self.a, self.b, "crossfade", fps=10)
        self.assertLess(abs(frames[-1].getpixel((5, 5))[0] - 200), 30)

    def test_generated_webp_exports_to_gif_with_its_timing(self):
        tmp = tempfile.mkdtemp()
        try:
            frames, durs = animate.transition_frames(self.a, self.b, "wipe", 0.5, 0.5, 0.5, 10)
            src = os.path.join(tmp, "anim.webp")
            animate.save_webp(src, frames, durs)
            self.assertEqual(sum(cc._file_delays(src)), sum(durs))
            out = os.path.join(tmp, "out.gif")
            job = dict(source_path=src, kind="gif", output_path=out, as_mp4=False,
                       params=dict(text="", cap_width=0, page_bg="#ffffff",
                                   font_family="Arial", font_size=12, font_color="#000000",
                                   padding=4, layout="overlay",
                                   text_boxes=[dict(text="Smoke!", x=0.1, y=0.1, w=0.8)]),
                       fps=10, total_frames=len(cc._file_delays(src)), durations=None,
                       gif_colors=None, trim=None, speed=1.0, dither=True)
            job_path = os.path.join(tmp, "job.json")
            with open(job_path, "w") as f:
                json.dump(job, f)
            cc._export_worker_main(job_path)
            with Image.open(out) as im:
                total = 0
                for i in range(im.n_frames):
                    im.seek(i)
                    total += im.info["duration"]
            self.assertEqual(total, sum(durs))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ThemeEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = os.path.join(self.tmp, "themes")
        shutil.copytree(REPO_THEMES, self.dir)
        self.saved = (themes.RESOURCE_DIR, themes._THEMES_DIR, themes._palettes_cache)
        themes.RESOURCE_DIR = self.tmp
        themes._THEMES_DIR = self.dir
        themes._palettes_cache = None

    def tearDown(self):
        themes.RESOURCE_DIR, themes._THEMES_DIR, themes._palettes_cache = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_derived_palettes_are_readable(self):
        for bg, fg, accent in (("#ffffff", "#222222", "#c45ab3"),
                               ("#1b1b1f", "#eeeeee", "#ffcc00")):
            p = themes.derive_palette(bg, fg, accent)
            self.assertEqual({k for k, _ in themes.COLOR_ROLES}, set(p))
            self.assertGreater(abs(themes.luma(p["fg"]) - themes.luma(p["input_bg"])), 120)
            self.assertGreater(abs(themes.luma(p["accent_fg"]) - themes.luma(p["accent"])), 60)

    def test_save_load_and_delete_a_custom_theme(self):
        pic = os.path.join(self.tmp, "my pic.png")
        Image.new("RGB", (40, 40), (255, 0, 0)).save(pic)
        data = dict(themes.theme_data("Sissy"), name="Lacy", background=pic,
                    background_mode="tile")
        data["accent"] = "#123456"
        self.assertEqual(themes.save_custom_theme(data), "Lacy")
        self.assertIn("Lacy", themes.get_palettes(force_reload=True))
        self.assertEqual(themes.get_palettes()["Lacy"]["accent"], "#123456")
        bg = themes.theme_background("Lacy")
        self.assertEqual(os.path.dirname(bg["path"]), self.dir)    # copied in beside it
        self.assertEqual(bg["mode"], "tile")
        themes.delete_custom_theme("Lacy")
        self.assertNotIn("Lacy", themes.get_palettes(force_reload=True))
        self.assertFalse(os.path.exists(bg["path"]))

    def test_builtin_names_are_protected(self):
        with self.assertRaises(ValueError):
            themes.save_custom_theme(dict(themes.theme_data("Dark"), name="Dark"))
        with self.assertRaises(ValueError):
            themes.delete_custom_theme("Goth")

    def test_preview_shows_unsaved_edits_until_reload(self):
        data = dict(themes.theme_data("Dark"), accent="#00ff00")
        themes.preview_palette("preview", data)
        self.assertEqual(themes.accent("preview")["accent"], "#00ff00")
        themes.get_palettes(force_reload=True)
        self.assertNotIn("preview", themes.get_palettes())


if __name__ == "__main__":
    unittest.main()
