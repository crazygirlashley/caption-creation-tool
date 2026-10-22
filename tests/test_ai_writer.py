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


class _OllamaHandler(BaseHTTPRequestHandler):
    """Just enough of Ollama's native API for the setup flow."""
    models: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/api/version":
            self._json({"version": "0.0-test"})
        elif self.path == "/api/tags":
            self._json({"models": [{"name": m} for m in _OllamaHandler.models]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        if body["model"] == "nope":
            self.wfile.write(b'{"error":"pull model manifest: file does not exist"}\n')
            return
        for line in ({"status": "pulling manifest"},
                     {"status": "pulling abc", "total": 1000, "completed": 250},
                     {"status": "pulling abc", "total": 1000, "completed": 1000},
                     {"status": "success"}):
            self.wfile.write((json.dumps(line) + "\n").encode())
            self.wfile.flush()
        _OllamaHandler.models.append(body["model"])

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class OllamaSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_pull_reports_progress_and_finishes(self):
        seen = []
        ai_writer.pull_model("hermes3:3b", lambda t, f: seen.append(f), base=self.base)
        self.assertIn(0.25, seen)
        self.assertIn(1.0, seen)

    def test_pull_error_is_readable(self):
        with self.assertRaises(ai_writer.AIWriterError) as ctx:
            ai_writer.pull_model("nope", lambda t, f: None, base=self.base)
        self.assertIn("does not exist", str(ctx.exception))

    def test_recommended_model_follows_ram(self):
        self.assertEqual(ai_writer.recommended_model(8), "hermes3:3b")
        self.assertEqual(ai_writer.recommended_model(16), "dolphin-mistral")
        self.assertEqual(ai_writer.recommended_model(64),
                         "huihui_ai/mistral-small-abliterated:24b")

    def test_checksum_mismatch_is_rejected(self):
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "OllamaSetup.exe")
            with open(path, "wb") as f:
                f.write(b"installer bytes")
            good = hashlib.sha256(b"installer bytes").hexdigest()
            ai_writer.verify_sha256(path, good.upper())
            with self.assertRaises(ai_writer.AIWriterError):
                ai_writer.verify_sha256(path, "0" * 64)

    @unittest.skipUnless(sys.platform == "win32", "Authenticode is Windows-only")
    def test_signature_check_rejects_unsigned_installer(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "OllamaSetup.exe")
            with open(fake, "wb") as f:
                f.write(b"MZ" + b"\0" * 1024)
            with self.assertRaises(ai_writer.AIWriterError):
                ai_writer.verify_signature(fake)

    @unittest.skipUnless(sys.platform == "win32", "Authenticode is Windows-only")
    def test_signature_check_accepts_validly_signed_file(self):
        signed = os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell",
                              "v1.0", "powershell.exe")
        self.assertIn("Microsoft", ai_writer.verify_signature(signed))
        with self.assertRaises(ai_writer.AIWriterError):
            ai_writer.verify_signature(signed, expected_signer="Ollama")

    def test_setup_flow_installs_starts_and_pulls(self):
        """Fresh PC: download → verify → install → start → pull → save."""
        calls = []
        state = {"installed": False, "running": False}
        saved = {}
        patches = {
            "find_ollama_exe": lambda: "C:/fake/ollama.exe" if state["installed"] else None,
            "ollama_running": lambda base=None, timeout=2.0: state["running"],
            "latest_ollama_installer": lambda: ("https://x/OllamaSetup.exe", "ab" * 32, "v1"),
            "download_file": lambda url, dest, p, c=None, label="": calls.append("download"),
            "verify_sha256": lambda path, expected: calls.append("checksum"),
            "verify_signature": lambda path, **kw: calls.append("signature"),
            "run_installer": lambda path: (calls.append("install"),
                                           state.update(installed=True)),
            "start_ollama": lambda exe, base=None, wait=60: (calls.append("start"),
                                                             state.update(running=True)),
            "ollama_models": lambda base=None: [],
            "pull_model": lambda name, p, c=None, base=None: calls.append(f"pull {name}"),
            "save_settings": lambda s: saved.update(s),
            "load_settings": lambda: dict(ai_writer.DEFAULTS),
        }
        originals = {k: getattr(ai_writer, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(ai_writer, k, v)
            settings = ai_writer.setup_ollama("hermes3:3b", lambda t, f: None)
        finally:
            for k, v in originals.items():
                setattr(ai_writer, k, v)
        self.assertEqual(calls, ["download", "checksum", "signature", "install", "start",
                                 "pull hermes3:3b"])
        self.assertEqual(settings["model"], "hermes3:3b")
        self.assertEqual(saved["base_url"], ai_writer.PRESETS["Ollama"])


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
