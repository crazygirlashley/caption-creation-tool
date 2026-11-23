"""AI "Match the picture": the picture model lists the appearance, the
writer corrects the story's final description against it."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_writer  # noqa: E402

APPEARANCE = ("Hair: long, straight, platinum blonde\nEyes: blue\n"
              "Clothing: pink cropped tank top; skin-tight light blue jeans\n"
              "Shoes & accessories: white sneakers, gold hoops")
STORY = ("Mark took the pill. By morning he was gone — in his place stood a woman with "
         "short red hair, wearing a black miniskirt and a red blouse.")


class _Handler(BaseHTTPRequestHandler):
    replies: list = []
    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append(body)
        reply = _Handler.replies.pop(0) if _Handler.replies else ""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(reply), 9):
            event = {"choices": [{"delta": {"content": reply[i:i + 9]}}]}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class MatchPictureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.settings = dict(ai_writer.DEFAULTS, model="writer", vision_model="eyes",
                            base_url=f"http://127.0.0.1:{cls.server.server_address[1]}/v1")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.replies, _Handler.seen = [], []

    def test_describe_appearance_keeps_lines(self):
        _Handler.replies = [APPEARANCE]
        out = ai_writer.describe_appearance(Image.new("RGB", (64, 64)), self.settings)
        self.assertEqual(out.splitlines()[0], "Hair: long, straight, platinum blonde")
        sent = _Handler.seen[0]
        self.assertEqual(sent["model"], "eyes")                          # the picture model
        parts = sent["messages"][0]["content"]
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("Clothing:", parts[0]["text"])

    def test_describe_appearance_flags_minor(self):
        _Handler.replies = ["minor: Hair: brown"]
        out = ai_writer.describe_appearance(Image.new("RGB", (64, 64)), self.settings)
        self.assertTrue(ai_writer.picture_may_show_minor(out))

    def test_needs_a_picture_model(self):
        with self.assertRaises(ai_writer.AIWriterError):
            ai_writer.describe_appearance(Image.new("RGB", (8, 8)),
                                          dict(self.settings, vision_model=""))

    def test_instruction_carries_the_appearance(self):
        instr = ai_writer.edit_instruction("match", 40, picture=APPEARANCE)
        self.assertIn("skin-tight light blue jeans", instr)
        self.assertIn("at the end", instr)
        self.assertIn("before the change", instr)
        self.assertIn("about 40 words", instr)

    def test_minor_mark_is_stripped_from_instruction(self):
        instr = ai_writer.edit_instruction("match", 40, picture="MINOR: Hair: brown")
        self.assertNotIn("MINOR", instr)

    def test_stream_edit_rewrites_with_the_writer_model(self):
        fixed = STORY.replace("short red hair", "long platinum blonde hair").replace(
            "a black miniskirt and a red blouse", "skin-tight jeans and a pink crop top")
        _Handler.replies = [fixed]
        instr = ai_writer.edit_instruction("match", 30, picture=APPEARANCE)
        out = "".join(ai_writer.stream_edit(STORY, instr, 30, self.settings))
        self.assertEqual(out, fixed)
        sent = _Handler.seen[0]
        self.assertEqual(sent["model"], "writer")
        self.assertIn("platinum blonde", sent["messages"][0]["content"])
        self.assertEqual(sent["messages"][1]["content"], STORY)
        self.assertLessEqual(sent.get("temperature", 1), 0.3)           # a faithful edit

    def test_sexual_story_and_possible_minor_is_refused(self):
        instr = ai_writer.edit_instruction("match", 30, picture="MINOR: Hair: brown")
        with self.assertRaises(ai_writer.ContentBlocked):
            list(ai_writer.stream_edit("She moaned as he undressed her and they had sex.",
                                       instr, 30, self.settings, minor_context=True))
        self.assertEqual(_Handler.seen, [])                             # never sent

    def test_non_sexual_story_with_possible_minor_is_fine(self):
        _Handler.replies = ["She walked to school in jeans."]
        instr = ai_writer.edit_instruction("match", 10, picture="MINOR: Clothing: jeans")
        out = "".join(ai_writer.stream_edit("She walked to school in a skirt.", instr, 10,
                                            self.settings, minor_context=True))
        self.assertEqual(out, "She walked to school in jeans.")


class NoPictureTalkTests(unittest.TestCase):
    """AI writing never talks about the caption's picture."""

    @staticmethod
    def clean(text, step=5):
        chunks = [text[i:i + step] for i in range(0, len(text), step)]
        return "".join(ai_writer.no_picture_talk(iter(chunks)))

    def test_phrase_is_cut_from_the_sentence(self):
        self.assertEqual(self.clean("The woman in the caption's picture had emerged in all her "
                                    "radiant beauty."),
                         "The woman had emerged in all her radiant beauty.")
        self.assertEqual(self.clean("Her hair, as pictured, was now platinum blonde."),
                         "Her hair was now platinum blonde.")
        self.assertEqual(self.clean("The woman shown in the image above stretched."),
                         "The woman stretched.")

    def test_sentence_about_the_picture_is_dropped(self):
        self.assertEqual(self.clean("By morning he was gone. The caption's image shows a "
                                    "stunning redhead. She smiled."),
                         "By morning he was gone. She smiled.")
        self.assertEqual(self.clean("This picture captures her new life.\n\nShe kissed him."),
                         "\n\nShe kissed him.".lstrip(" "))

    def test_photos_inside_the_story_stay(self):
        for s in ("She looked at the photo on her desk and gasped.",
                  "The picture on the wall showed her old self."):
            self.assertEqual(self.clean(s), s)

    def test_prompts_forbid_it(self):
        self.assertIn(ai_writer.NO_PICTURE_TALK, ai_writer.SYSTEM_PROMPT)
        self.assertIn(ai_writer.NO_PICTURE_TALK, ai_writer.EDIT_SYSTEM_PROMPT)
        user = ai_writer.build_messages("He took the pill.", 60, "Red hair, green dress")[1]
        self.assertNotIn("caption's picture", user["content"])
        self.assertIn("never mention", user["content"])
        self.assertNotIn("caption's picture", ai_writer.edit_instruction("match", 40,
                                                                          picture="Hair: red"))


class AgeCheckFallbackTests(unittest.TestCase):
    """Picture models that describe instead of answering YES/NO (Moondream
    through Ollama): the description decides, strictly."""

    def test_adult_only_when_clearly_described_as_one(self):
        f = ai_writer.description_suggests_minor
        self.assertFalse(f("A woman with red hair wearing lingerie poses on a bed."))
        self.assertFalse(f("The image features a man in a suit."))
        for text in ("A young woman in a bikini.", "A girl in a school uniform.",
                     "A teenage boy.", "The image shows a person dancing.",
                     "A man and his son.", ""):
            self.assertTrue(f(text), text)

    def test_looks_underage_uses_the_description(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            s = dict(ai_writer.DEFAULTS, model="w", vision_model="eyes",
                     base_url=f"http://127.0.0.1:{server.server_address[1]}/v1")
            img = Image.new("RGB", (32, 32))
            for reply, expected in (("NO", False), ("Yes.", True),
                                    ("The image features a woman in a red dress.", False),
                                    ("The image features a girl on a swing.", True)):
                _Handler.replies = [reply]
                self.assertEqual(ai_writer.looks_underage(img, s), expected, reply)
        finally:
            server.shutdown()


class MoreEroticTests(unittest.TestCase):
    """AI Edit → Make it more erotic."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.settings = dict(ai_writer.DEFAULTS, model="writer",
                            base_url=f"http://127.0.0.1:{cls.server.server_address[1]}/v1")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.replies, _Handler.seen = [], []

    def test_offered_and_worded(self):
        self.assertIn("spicy", ai_writer.EDIT_ACTIONS)
        instr = ai_writer.edit_instruction("spicy", 120)
        self.assertIn("about 120 words", instr)
        self.assertIn("final line", instr)

    def test_adult_story_is_rewritten(self):
        _Handler.replies = ["Hotter version."]
        out = "".join(ai_writer.stream_edit(STORY, ai_writer.edit_instruction("spicy", 40),
                                            40, self.settings))
        self.assertEqual(out, "Hotter version.")
        self.assertIn("erotic", _Handler.seen[0]["messages"][0]["content"])

    def test_story_about_a_minor_is_refused(self):
        story = "Jenny, a 15-year-old, walked home from school in her uniform."
        with self.assertRaises(ai_writer.ContentBlocked):
            list(ai_writer.stream_edit(story, ai_writer.edit_instruction("spicy", 40), 40,
                                       self.settings))
        self.assertEqual(_Handler.seen, [])

    def test_flagged_picture_is_refused(self):
        with self.assertRaises(ai_writer.ContentBlocked):
            list(ai_writer.stream_edit(STORY, ai_writer.edit_instruction("spicy", 40), 40,
                                       self.settings, minor_context=True))
        self.assertEqual(_Handler.seen, [])

    def test_other_edits_of_young_characters_still_work(self):
        _Handler.replies = ["Jenny, 15, walked home from school."]
        out = "".join(ai_writer.stream_edit(
            "Jenny, 15, walked home from school.", ai_writer.edit_instruction("grammar", 8),
            8, self.settings))
        self.assertEqual(out, "Jenny, 15, walked home from school.")


if __name__ == "__main__":
    unittest.main()
