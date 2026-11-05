"""AI-planned animations (storyboards) and AI video through ComfyUI.

Run from the repo root with:  python -m unittest discover tests
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_writer  # noqa: E402
import animate  # noqa: E402
import comfy_client  # noqa: E402


class StoryboardTests(unittest.TestCase):
    def setUp(self):
        self.a = Image.new("RGB", (400, 300), (200, 40, 120))
        self.b = Image.new("RGB", (400, 300), (20, 200, 90))

    def test_messy_ai_plans_are_cleaned_up(self):
        raw = {"steps": [
            {"type": "Show", "picture": "before", "duration": "1"},
            {"type": "zoom_in", "focus": "FACE", "to": 9},          # clamped
            {"type": "dance"},                                       # unknown: dropped
            {"action": "smoke bomb"},                                # not a step type
            {"type": "change", "effect": "Smoke Bomb", "color": "light purple", "to": "after"},
            {"type": "flash", "color": "#fff", "seconds": 30},       # clamped
        ]}
        steps = animate.normalize_plan(raw, has_second=True)
        self.assertEqual([s["type"] for s in steps], ["hold", "zoom", "transition", "flash"])
        self.assertEqual(steps[1]["to"], 3.0)
        self.assertEqual(steps[1]["focus"], "face")
        self.assertEqual((steps[2]["effect"], steps[2]["to"], steps[2]["color"]),
                         ("smoke", "second", "#b05cc9"))
        self.assertEqual((steps[3]["color"], steps[3]["seconds"]), ("#ffffff", 6))

    def test_second_picture_falls_back_without_one(self):
        steps = animate.normalize_plan([{"type": "transition", "effect": "wipe",
                                         "to": "second"}], has_second=False)
        self.assertEqual(steps[0]["to"], "first")

    def test_garbage_still_gives_a_playable_plan(self):
        self.assertEqual(animate.normalize_plan("nonsense", True)[0]["type"], "hold")

    def test_length_is_capped(self):
        steps = animate.normalize_plan([{"type": "hold", "seconds": 6}] * 10, True)
        self.assertLessEqual(sum(s["seconds"] for s in steps), animate.MAX_SECONDS)

    def test_every_step_describes_itself(self):
        steps = animate.normalize_plan([
            {"type": "hold"}, {"type": "zoom", "to": 1.5}, {"type": "zoom", "to": 1},
            {"type": "pan", "direction": "left"}, {"type": "shake"}, {"type": "pulse"},
            {"type": "flash"}, {"type": "transition", "effect": "smoke"}], True)
        lines = [animate.describe_step(s) for s in steps]
        self.assertTrue(all(isinstance(t, str) and t for t in lines))
        self.assertIn("Smoke bomb", lines[-1])
        self.assertIn("Zoom back out", lines[2])

    def test_storyboard_plays_in_order_and_keeps_timing(self):
        steps = animate.normalize_plan([
            {"type": "hold", "picture": "first", "seconds": 1},
            {"type": "zoom", "to": 2, "focus": "face", "seconds": 1},
            {"type": "shake", "seconds": 0.5},
            {"type": "transition", "effect": "smoke", "to": "second", "seconds": 1,
             "color": "#0000ff"},
            {"type": "hold", "picture": "second", "seconds": 1}], True)
        frames, durs = animate.render_storyboard(self.a, self.b, steps, fps=10,
                                                 faces={"first": (0.3, 0.3)}, loop=False)
        self.assertEqual(len(frames), len(durs))
        self.assertEqual(durs[0], 1000)                            # the opening hold
        self.assertEqual(frames[0].getpixel((5, 5)), (200, 40, 120))
        self.assertEqual(frames[-1].getpixel((5, 5)), (20, 200, 90))
        self.assertEqual(durs[-1], 1000)                           # the closing hold
        self.assertAlmostEqual(sum(durs), 1000 + 1000 + 600 + 1000 + 1000, delta=200)

    def test_loop_returns_to_the_opening_shot(self):
        steps = animate.normalize_plan([{"type": "hold"},
                                        {"type": "transition", "effect": "wipe",
                                         "to": "second"}], True)
        frames, _ = animate.render_storyboard(self.a, self.b, steps, fps=10, loop=True)
        self.assertLess(abs(frames[-1].getpixel((5, 5))[0] - 200), 40)


class _PlanHandler(BaseHTTPRequestHandler):
    reply = ""
    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _PlanHandler.seen.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(self.reply), 7):
            event = {"choices": [{"delta": {"content": self.reply[i:i + 7]}}]}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _PlanHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.settings = dict(ai_writer.DEFAULTS, model="hermes3:3b",
                            base_url=f"http://127.0.0.1:{cls.server.server_address[1]}/v1")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_plan_from_a_chatty_reply(self):
        _PlanHandler.reply = ("<think>let me plan</think>Sure! Here's the plan:\n```json\n"
                              '[{"type": "hold", "seconds": 1}, {"type": "transition", '
                              '"effect": "smoke", "to": "second", "color": "red"}]\n```\nEnjoy!')
        raw = ai_writer.plan_animation("red smoke to the after picture", animate.PLAN_GUIDE,
                                       True, self.settings)
        steps = animate.normalize_plan(raw, True)
        self.assertEqual(steps[1]["color"], "#e8322e")
        prompt = _PlanHandler.seen[-1]["messages"]
        self.assertIn("smoke", prompt[0]["content"])           # the step guide is sent
        self.assertEqual(prompt[-1]["content"], "red smoke to the after picture")

    def test_no_plan_in_reply_is_a_readable_error(self):
        _PlanHandler.reply = "I'm not sure what you mean."
        with self.assertRaises(ai_writer.AIWriterError):
            ai_writer.plan_animation("x", animate.PLAN_GUIDE, False, self.settings)


# A Wan-style image-to-video workflow in ComfyUI's API format.
WAN_WORKFLOW = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a woman", "clip": ["38", 0]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["38", 0]}},
    "52": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
    "50": {"class_type": "WanImageToVideo", "inputs": {
        "positive": ["6", 0], "negative": ["7", 0], "vae": ["39", 0], "start_image": ["52", 0],
        "width": 832, "height": 480, "length": 33, "batch_size": 1}},
    "3": {"class_type": "KSampler", "inputs": {
        "seed": 1, "steps": 20, "cfg": 6, "model": ["37", 0], "positive": ["50", 0],
        "negative": ["50", 1], "latent_image": ["50", 2]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
    "28": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["8", 0], "fps": 16,
                                                         "filename_prefix": "ComfyUI"}},
}


class _ComfyHandler(BaseHTTPRequestHandler):
    queued: list = []
    fail_prompt = False
    history_calls = 0

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/system_stats":
            self._json(200, {"system": {"comfyui_version": "0.3.99"},
                             "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync",
                                          "type": "cuda", "vram_total": 24 * 2**30}]})
        elif url.path == "/queue":
            self._json(200, {"queue_running": [], "queue_pending": []})
        elif url.path.startswith("/history/"):
            _ComfyHandler.history_calls += 1
            if _ComfyHandler.history_calls < 2:            # still working the first time
                self._json(200, {})
                return
            pid = url.path.rsplit("/", 1)[1]
            self._json(200, {pid: {"status": {"status_str": "success", "completed": True},
                                   "outputs": {
                                       "28": {"images": [{"filename": "ComfyUI_00001_.webp",
                                                          "subfolder": "", "type": "output"}]},
                                       "99": {"images": [{"filename": "preview.png",
                                                          "subfolder": "", "type": "temp"}]}}}})
        elif url.path == "/view":
            q = parse_qs(url.query)
            assert q["filename"] == ["ComfyUI_00001_.webp"], q
            frames = [Image.new("RGB", (64, 48), (i * 40, 80, 160)) for i in range(5)]
            buf = io.BytesIO()
            frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:],
                           duration=62, loop=0, lossless=True)
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(404, {})

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        if self.path == "/upload/image":
            self._json(200, {"name": "caption_creator_x_1.png", "subfolder": "",
                             "type": "input"})
        elif self.path == "/prompt":
            if _ComfyHandler.fail_prompt:
                self._json(400, {"error": {"message": "Prompt outputs failed validation"},
                                 "node_errors": {"37": {"class_type": "UNETLoader", "errors": [
                                     {"message": "Value not in list",
                                      "details": "unet_name: 'wan2.2.safetensors' not in []"}]}}})
                return
            _ComfyHandler.queued.append(json.loads(body)["prompt"])
            self._json(200, {"prompt_id": "abc123"})
        elif self.path == "/interrupt":
            self._json(200, {})


class ComfyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _ComfyHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.tmp = tempfile.mkdtemp()
        cls.wf_path = os.path.join(cls.tmp, "wan_api.json")
        with open(cls.wf_path, "w") as f:
            json.dump(WAN_WORKFLOW, f)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fill_finds_picture_and_prompt_through_the_video_node(self):
        wf, notes = comfy_client.fill_workflow(WAN_WORKFLOW, ["mine.png"], "she smiles",
                                               size=(600, 900), seed=1)
        self.assertEqual(wf["52"]["inputs"]["image"], "mine.png")
        self.assertEqual(wf["6"]["inputs"]["text"], "she smiles")     # positive, not negative
        self.assertEqual(wf["7"]["inputs"]["text"], "blurry")
        self.assertNotEqual(wf["3"]["inputs"]["seed"], 1)
        w, h = wf["50"]["inputs"]["width"], wf["50"]["inputs"]["height"]
        self.assertTrue(h > w and w % 32 == 0 and h % 32 == 0, (w, h))   # portrait now
        self.assertEqual(WAN_WORKFLOW["6"]["inputs"]["text"], "a woman")  # original untouched
        self.assertTrue(any("CLIPTextEncode" in n for n in notes))

    def test_placeholders_win(self):
        wf = json.loads(json.dumps(WAN_WORKFLOW))
        wf["7"]["inputs"]["text"] = "%prompt%, cinematic"
        wf["52"]["inputs"]["image"] = "%image%"
        out, _ = comfy_client.fill_workflow(wf, ["pic.png"], "she smiles")
        self.assertEqual(out["7"]["inputs"]["text"], "she smiles, cinematic")
        self.assertEqual(out["6"]["inputs"]["text"], "a woman")
        self.assertEqual(out["52"]["inputs"]["image"], "pic.png")

    def test_editor_format_is_explained(self):
        path = os.path.join(self.tmp, "ui.json")
        with open(path, "w") as f:
            json.dump({"nodes": [], "links": []}, f)
        with self.assertRaises(comfy_client.ComfyError) as ctx:
            comfy_client.load_workflow(path)
        self.assertIn("Export (API)", str(ctx.exception))

    def test_server_description(self):
        text = comfy_client.describe_server(comfy_client.check(self.url))
        self.assertEqual(text, "ComfyUI 0.3.99 · NVIDIA GeForce RTX 4090 (24 GB)")

    def test_whole_job(self):
        _ComfyHandler.history_calls = 0
        _ComfyHandler.fail_prompt = False
        messages = []
        frames, durs, notes = comfy_client.generate(
            self.url, self.wf_path, [Image.new("RGB", (300, 200), (255, 0, 0))],
            "she turns and smiles", messages.append)
        self.assertEqual(len(frames), 5)
        self.assertEqual(durs, [62] * 5)
        sent = _ComfyHandler.queued[-1]
        self.assertEqual(sent["52"]["inputs"]["image"], "caption_creator_x_1.png")
        self.assertEqual(sent["6"]["inputs"]["text"], "she turns and smiles")
        self.assertTrue(any("Generating" in m for m in messages))

    def test_workflow_errors_are_readable(self):
        _ComfyHandler.fail_prompt = True
        try:
            with self.assertRaises(comfy_client.ComfyError) as ctx:
                comfy_client.generate(self.url, self.wf_path, [Image.new("RGB", (8, 8))],
                                      "x", lambda *_: None)
        finally:
            _ComfyHandler.fail_prompt = False
        self.assertIn("UNETLoader", str(ctx.exception))
        self.assertIn("wan2.2.safetensors", str(ctx.exception))

    def test_unreachable_server(self):
        with self.assertRaises(comfy_client.ComfyError) as ctx:
            comfy_client.check("http://127.0.0.1:9")
        self.assertIn("--listen", str(ctx.exception))

    def test_video_output_becomes_frames(self):
        import imageio.v2 as imageio
        path = os.path.join(self.tmp, "clip.mp4")
        writer = imageio.get_writer(path, fps=16, codec="libx264")
        for i in range(8):
            writer.append_data(__import__("numpy").full((64, 96, 3), i * 30, "uint8"))
        writer.close()
        with open(path, "rb") as f:
            frames, durs = comfy_client.to_frames([("clip.mp4", f.read())], 16)
        self.assertEqual(len(frames), 8)
        self.assertEqual(durs[0], 62)


if __name__ == "__main__":
    unittest.main()
