"""AI writer client tests against a mock OpenAI-compatible server.

Run from the repo root with:  python -m unittest discover tests
"""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_writer  # noqa: E402

# What the mock streams back, split so tags and words straddle chunk
# boundaries the way real servers emit them.
CHUNKS = ["<thi", "nk>planning the", " story...</th", "ink>\n\n",
          "I woke up", " feeling ", "different.", " \"Hi sis.\""]


class _Handler(BaseHTTPRequestHandler):
    requests_seen: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            self._json(200, {"data": [{"id": "mistral-nemo"}, {"id": "llama3.1"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.requests_seen.append((dict(self.headers), body))
        if body["model"] == "missing":
            self._json(404, {"error": {"message": "model 'missing' not found"}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in CHUNKS:
            event = {"choices": [{"delta": {"content": c}}]}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class AIWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def settings(self, **kw):
        s = dict(ai_writer.DEFAULTS, base_url=self.base, model="mistral-nemo")
        s.update(kw)
        return s

    def test_lists_models(self):
        self.assertEqual(ai_writer.list_models(self.base), ["llama3.1", "mistral-nemo"])

    def test_streams_story_without_reasoning_block(self):
        text = "".join(ai_writer.stream_story("a prompt", 120, self.settings()))
        self.assertEqual(text, "I woke up feeling different. \"Hi sis.\"")

    def test_request_shape(self):
        _Handler.requests_seen.clear()
        list(ai_writer.stream_story("  my prompt  ", 200, self.settings(api_key="k")))
        headers, body = _Handler.requests_seen[-1]
        self.assertEqual(headers.get("Authorization"), "Bearer k")
        self.assertTrue(body["stream"])
        self.assertEqual(body["messages"][1], {"role": "user", "content": "my prompt"})
        self.assertIn("about 200 words", body["messages"][0]["content"])

    def test_server_error_is_readable(self):
        with self.assertRaises(ai_writer.AIWriterError) as ctx:
            list(ai_writer.stream_story("p", 60, self.settings(model="missing")))
        self.assertIn("404", str(ctx.exception))

    def test_unreachable_server(self):
        with self.assertRaises(ai_writer.AIWriterError) as ctx:
            ai_writer.list_models("http://127.0.0.1:9/v1", timeout=2)
        self.assertIn("Couldn't reach", str(ctx.exception))

    def test_requires_model(self):
        with self.assertRaises(ai_writer.AIWriterError):
            list(ai_writer.stream_story("p", 60, self.settings(model="")))

    def test_cancel_stops_stream(self):
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(list(ai_writer.stream_story("p", 60, self.settings(), cancel)), [])


class ThinkFilterTests(unittest.TestCase):
    def test_tag_split_at_every_position(self):
        text = "a<think>hidden</think>b<think>x</think>c"
        for size in range(1, len(text) + 1):
            f = ai_writer._ThinkFilter()
            out = "".join(f.feed(text[i:i + size]) for i in range(0, len(text), size))
            self.assertEqual(out + f.flush(), "abc", f"chunk size {size}")

    def test_lone_angle_bracket_passes_through(self):
        f = ai_writer._ThinkFilter()
        self.assertEqual(f.feed("x < y and <b>") + f.flush(), "x < y and <b>")


if __name__ == "__main__":
    unittest.main()
