"""Caption story writer backed by a local / self-hosted LLM.

Talks to any server exposing the OpenAI-compatible chat API — Ollama,
LM Studio, KoboldCpp, llama.cpp's llama-server, text-generation-webui — so
the user picks whatever open model they run themselves; nothing is sent to
a hosted service unless they point the URL at one. Pure (no Tk), so the
CaptionApp dialog and the tests share it.
"""

import json
import os
import threading
from typing import Iterator, Optional

import requests

from app_paths import BASE_DIR

SETTINGS_PATH = os.path.join(BASE_DIR, "ai_settings.json")

# Default OpenAI-compatible endpoints of the common local runners.
PRESETS = {
    "Ollama": "http://localhost:11434/v1",
    "LM Studio": "http://localhost:1234/v1",
    "KoboldCpp": "http://localhost:5001/v1",
    "llama.cpp server": "http://localhost:8080/v1",
    "text-generation-webui": "http://localhost:5000/v1",
}

DEFAULTS = {
    "base_url": PRESETS["Ollama"],
    "model": "",
    "api_key": "",
    "temperature": 0.9,
}

# label -> approximate word target. Caption panels hold a paragraph or
# three; longer stories are better suited to the DA description.
LENGTHS = {
    "Short (~60 words)": 60,
    "Medium (~120 words)": 120,
    "Long (~200 words)": 200,
    "Very long (~350 words)": 350,
}

SYSTEM_PROMPT = (
    "You are a fiction writer for an adult caption-art community that makes "
    "TG (gender transformation) captions: an image paired with a short story "
    "panel. Turn the user's prompt into a vivid, immersive story told in the "
    "point of view and tense the prompt asks for (default: the main "
    "character's first-person POV). Keep the user's key beats, names and any "
    "quoted lines, ending on the line or twist the prompt gives. Explicit "
    "adult content is allowed when the prompt calls for it. All characters "
    "are adults. Aim for about {words} words. Output only the story text — "
    "no title, no preamble, no notes or disclaimers, no markdown."
)


class AIWriterError(Exception):
    """A user-presentable failure (unreachable server, bad model, ...)."""


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            settings.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({k: settings.get(k, v) for k, v in DEFAULTS.items()}, f, indent=2)


def _headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def list_models(base_url: str, api_key: str = "", timeout: float = 5.0) -> list:
    """Model ids the server reports at GET /models."""
    try:
        resp = requests.get(_url(base_url, "/models"), headers=_headers(api_key),
                            timeout=timeout)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.ConnectionError:
        raise AIWriterError(_unreachable_msg(base_url)) from None
    except (requests.RequestException, ValueError) as e:
        raise AIWriterError(f"Couldn't list models from {base_url}: {e}") from None
    return sorted(m.get("id", "") for m in data if m.get("id"))


def _unreachable_msg(base_url: str) -> str:
    return (f"Couldn't reach a model server at {base_url}.\n\n"
            "Start your local LLM app (e.g. Ollama or LM Studio) and load a "
            "model, then check the address under ⚙ Settings → AI Writer.")


def build_messages(prompt: str, words: int) -> list:
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(words=words)},
        {"role": "user", "content": prompt.strip()},
    ]


class _ThinkFilter:
    """Drop <think>...</think> reasoning blocks (Qwen3, DeepSeek-R1 and
    similar emit these) from a stream of text chunks whose tag boundaries
    can fall anywhere."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out = []
        while True:
            tag = self.CLOSE if self._inside else self.OPEN
            idx = self._buf.find(tag)
            if idx == -1:
                # Hold back a possible partial tag at the end of the buffer.
                keep = 0
                for n in range(len(tag) - 1, 0, -1):
                    if self._buf.endswith(tag[:n]):
                        keep = n
                        break
                ready, self._buf = self._buf[:len(self._buf) - keep], self._buf[len(self._buf) - keep:]
                if not self._inside:
                    out.append(ready)
                break
            if not self._inside:
                out.append(self._buf[:idx])
            self._buf = self._buf[idx + len(tag):]
            self._inside = not self._inside
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return "" if self._inside else rest


def stream_story(prompt: str, words: int, settings: dict,
                 cancel: Optional[threading.Event] = None,
                 timeout: float = 300.0) -> Iterator[str]:
    """Yield the story text as it streams from POST /chat/completions."""
    if not settings.get("model"):
        raise AIWriterError("No model selected — pick one under ⚙ Settings → AI Writer.")
    body = {
        "model": settings["model"],
        "messages": build_messages(prompt, words),
        "temperature": float(settings.get("temperature", 0.9)),
        "max_tokens": int(words * 2.5) + 200,
        "stream": True,
    }
    try:
        resp = requests.post(_url(settings["base_url"], "/chat/completions"),
                             headers=_headers(settings.get("api_key", "")),
                             json=body, stream=True, timeout=(10, timeout))
    except requests.ConnectionError:
        raise AIWriterError(_unreachable_msg(settings["base_url"])) from None
    except requests.RequestException as e:
        raise AIWriterError(f"Request failed: {e}") from None

    with resp:
        if resp.status_code != 200:
            detail = resp.text[:300].strip()
            raise AIWriterError(f"The model server returned {resp.status_code}: {detail}")
        think = _ThinkFilter()
        leading = True
        for raw in resp.iter_lines(decode_unicode=False):
            if cancel is not None and cancel.is_set():
                return
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if "error" in event:
                err = event["error"]
                raise AIWriterError(str(err.get("message", err) if isinstance(err, dict) else err))
            for choice in event.get("choices", []):
                text = (choice.get("delta") or {}).get("content") or ""
                text = think.feed(text)
                if leading:
                    text = text.lstrip()
                    leading = not text
                if text:
                    yield text
        tail = think.flush()
        if leading:
            tail = tail.lstrip()
        if tail:
            yield tail
