"""Caption story writer backed by a local / self-hosted LLM.

Talks to any server exposing the OpenAI-compatible chat API — Ollama,
LM Studio, KoboldCpp, llama.cpp's llama-server, text-generation-webui — so
the user picks whatever open model they run themselves; nothing is sent to
a hosted service unless they point the URL at one. Pure (no Tk), so the
CaptionApp dialog and the tests share it.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Iterator, Optional

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
    # A model that can see pictures (e.g. Moondream, LLaVA), used only to
    # describe the caption's picture; "" = don't look at the picture.
    "vision_model": "",
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


def build_messages(prompt: str, words: int, picture: str = "") -> list:
    """`picture` is a description of the caption's image, if there is one,
    so the story can match who and what is in it."""
    user = prompt.strip()
    if picture.strip():
        user = (f"The caption's picture shows: {picture.strip()}\n\n"
                "Write the story to fit that picture — the person in it is the one "
                f"the story is about, as they look by the end.\n\n{user}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(words=words)},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Looking at the picture: a vision model describes it, and the description
# goes into the writer's prompt. The writing model itself needn't see, so any
# uncensored text model still does the writing.
# ---------------------------------------------------------------------------

DESCRIBE_PROMPT = (
    "Describe this picture for a fiction writer, in 3 to 5 plain sentences. "
    "Cover the person or people (apparent gender presentation, hair, makeup, "
    "body, clothing or lingerie, pose, facial expression), what they seem to be "
    "doing, and the setting and mood. This is an adult art community and the "
    "people shown are adults; describe nudity or sexual content matter-of-factly "
    "if present. Don't guess names or ages, and don't add opinions or warnings."
)

# Vision models offered for one-click download through Ollama:
# (tag, label, download GB, RAM GB needed)
VISION_MODELS = [
    ("moondream", "Moondream 2 — small and quick, short descriptions", 1.7, 4),
    ("llava:7b", "LLaVA 7B — more detail", 4.7, 10),
    ("minicpm-v", "MiniCPM-V 8B — the most detail, needs a strong PC", 5.5, 12),
]

_DESCRIBE_MAX_SIDE = 768


def image_data_url(img) -> str:
    """A PIL image as a JPEG data: URL, shrunk to what vision models use."""
    import base64
    import io
    im = img.convert("RGB")
    im.thumbnail((_DESCRIBE_MAX_SIDE, _DESCRIBE_MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def describe_image(img, settings: dict, cancel: Optional[threading.Event] = None) -> str:
    """A short description of a PIL image from settings["vision_model"]."""
    model = (settings.get("vision_model") or "").strip()
    if not model:
        raise AIWriterError("No picture model is set — choose one under "
                            "⚙ Settings → AI Writer → Picture model.")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": DESCRIBE_PROMPT},
        {"type": "image_url", "image_url": {"url": image_data_url(img)}},
    ]}]
    try:
        text = "".join(_stream_chat(messages, 300, dict(settings, model=model), cancel,
                                    timeout=240, temperature=0.2))
    except AIWriterError as e:
        if "image" in str(e).lower() or "vision" in str(e).lower() or "400" in str(e):
            raise AIWriterError(f"{model} couldn't read the picture — it may not be a model "
                                f"that can see images.\n\n{e}") from None
        raise
    return " ".join(text.split())


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


EDIT_SYSTEM_PROMPT = (
    "You are an editor for an adult caption-art community that makes TG "
    "(gender transformation) captions: an image paired with a short story "
    "panel. Revise the text the user sends as instructed: {instruction} "
    "Unless the instruction says otherwise, keep the point of view, tense, "
    "names, any quoted dialogue, emoji and the ending line. Explicit adult "
    "content is allowed. All characters are adults. Output only the revised "
    "text — no title, preamble, notes, quotation marks around it, or markdown."
)

# key -> (menu label, instruction; {words} is the target length)
EDIT_ACTIONS = {
    "smooth": ("Smooth & polish",
               "Improve the flow, rhythm and word choice and fix awkward phrasing, "
               "keeping the length about the same (about {words} words)."),
    "shorten": ("Shorten to fit",
                "Condense it to about {words} words, keeping the key story beats, the "
                "strongest details and the final line."),
    "expand": ("Add more detail",
               "Expand it to about {words} words with richer sensory, physical and "
               "emotional detail, without changing what happens."),
    "grammar": ("Fix spelling & grammar",
                "Fix only spelling, grammar and punctuation. Change as little as "
                "possible and keep the wording, style and length."),
}


def edit_instruction(action: str, words: int, custom: str = "") -> str:
    if action == "custom":
        return custom.strip().rstrip(".") + "."
    return EDIT_ACTIONS[action][1].format(words=words)


def stream_edit(text: str, instruction: str, words: int, settings: dict,
                cancel: Optional[threading.Event] = None) -> Iterator[str]:
    """Yield a revised version of text as it streams."""
    messages = [
        {"role": "system", "content": EDIT_SYSTEM_PROMPT.format(instruction=instruction)},
        {"role": "user", "content": text.strip()},
    ]
    # Grammar fixes should stay faithful, so they run cooler.
    temperature = 0.3 if "Fix only spelling" in instruction else None
    yield from _stream_chat(messages, int(max(words, len(text.split())) * 2.5) + 200,
                            settings, cancel, temperature=temperature)


# ---------------------------------------------------------------------------
# Planning an animation: a description ("zoom into her face, then purple
# smoke reveals the after picture") becomes a storyboard of the steps
# animate.py can play. The step list (`guide`) comes from animate.PLAN_GUIDE.
# ---------------------------------------------------------------------------

PLAN_SYSTEM_PROMPT = (
    "You direct short looping animations made from one or two still pictures for an "
    "adult caption-art community. Turn the user's description into a storyboard using "
    "ONLY these steps:\n\n{guide}\n"
    "Pictures available: {pictures}.\n"
    "Rules: start with a hold step; keep the whole animation under 15 seconds; use the "
    "user's colors and timings when given; when they don't say, choose what fits the "
    "mood (a gentle zoom for something slow and dreamy, shakes and flashes for something "
    "sudden). Output only a JSON array of step objects — no explanation, no markdown."
)

PLAN_EXAMPLE_USER = ("hold on her for a second, slowly zoom into her face, then a purple "
                     "smoke bomb reveals the after picture")
PLAN_EXAMPLE_REPLY = (
    '[{"type": "hold", "picture": "first", "seconds": 1}, '
    '{"type": "zoom", "to": 1.6, "focus": "face", "seconds": 2.5}, '
    '{"type": "transition", "effect": "smoke", "to": "second", "color": "#b05cc9", '
    '"seconds": 1.8}, {"type": "hold", "picture": "second", "seconds": 1.5}]'
)


def parse_plan(text: str):
    """The JSON storyboard in a model's reply, tolerating code fences and
    chatter around it. Raises AIWriterError if there isn't one."""
    s = text.strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = s.find(opener), s.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except ValueError:
                continue
    raise AIWriterError("The AI didn't reply with a plan it could follow. Try again, or "
                        "describe it a little differently.")


def plan_animation(description: str, guide: str, has_second: bool, settings: dict,
                   cancel: Optional[threading.Event] = None):
    """Ask the writing model for a storyboard; returns the parsed JSON
    (normalize it with animate.normalize_plan before playing it)."""
    pictures = ('"first" and "second" (the second is usually the "after")' if has_second
                else 'only "first" (transitions go back to the same picture)')
    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT.format(guide=guide,
                                                                pictures=pictures)},
        {"role": "user", "content": PLAN_EXAMPLE_USER},
        {"role": "assistant", "content": PLAN_EXAMPLE_REPLY},
        {"role": "user", "content": description.strip()},
    ]
    text = "".join(_stream_chat(messages, 900, settings, cancel, timeout=240,
                                temperature=0.3))
    return parse_plan(text)


def stream_story(prompt: str, words: int, settings: dict,
                 cancel: Optional[threading.Event] = None,
                 timeout: float = 300.0, picture: str = "") -> Iterator[str]:
    """Yield the story text as it streams from POST /chat/completions."""
    yield from _stream_chat(build_messages(prompt, words, picture), int(words * 2.5) + 200,
                            settings, cancel, timeout=timeout)


def _stream_chat(messages: list, max_tokens: int, settings: dict,
                 cancel: Optional[threading.Event] = None, timeout: float = 300.0,
                 temperature: Optional[float] = None) -> Iterator[str]:
    """Stream the assistant's reply text from POST /chat/completions,
    with reasoning (<think>) blocks and leading whitespace removed."""
    if not settings.get("model"):
        raise AIWriterError("No model selected — pick one under ⚙ Settings → AI Writer.")
    body = {
        "model": settings["model"],
        "messages": messages,
        "temperature": (temperature if temperature is not None
                        else float(settings.get("temperature", 0.9))),
        "max_tokens": max_tokens,
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


# ---------------------------------------------------------------------------
# One-click local setup (Ollama)
#
# For users with no local LLM yet: download the official Ollama installer,
# verify its Authenticode signature, install it per-user, start its server,
# and pull a model — all after the user agrees in the setup dialog in
# caption_creator.py.
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://localhost:11434"
# The installer comes from Ollama's official GitHub release, whose API
# publishes a SHA-256 for every asset — the download must match it exactly.
OLLAMA_RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
OLLAMA_INSTALLER_ASSET = "OllamaSetup.exe"
OLLAMA_INSTALL_GB = 5.0  # installer download plus the installed runtime, roughly

# Models offered by the setup dialog — uncensored / lightly aligned models
# that will write explicit fiction. (tag, label, download GB, RAM GB needed)
SETUP_MODELS = [
    ("hermes3:3b", "Hermes 3 3B — light & fast, plainer prose", 2.0, 6),
    ("hermes3:8b", "Hermes 3 8B — best writing for most PCs", 4.7, 10),
    ("dolphin-mistral", "Dolphin Mistral 7B — uncensored, alternative voice", 4.1, 10),
    ("huihui_ai/mistral-small-abliterated:24b",
     "Mistral Small 24B abliterated — richest prose, needs a strong PC", 14.0, 24),
]

ProgressFn = Callable[[str, Optional[float]], None]  # (status text, fraction 0-1 or None)


class SetupCancelled(Exception):
    pass


def total_ram_gb() -> float:
    if sys.platform == "win32":
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        stat = _MemStatus()
        stat.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullTotalPhys / 1e9
    return 0.0


def recommended_model(ram_gb: float, models: Optional[list] = None) -> str:
    """Largest offered model this much RAM runs comfortably."""
    models = models or SETUP_MODELS
    fitting = [tag for tag, _label, _gb, need in models if ram_gb >= need]
    return fitting[-1] if fitting else models[0][0]


def find_ollama_exe() -> Optional[str]:
    """Path to ollama.exe if installed (default per-user location or PATH)."""
    candidates = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama",
                               "ollama.exe")]
    on_path = shutil.which("ollama")
    if on_path:
        candidates.insert(0, on_path)
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def ollama_running(base: str = OLLAMA_URL, timeout: float = 2.0) -> bool:
    try:
        return requests.get(base + "/api/version", timeout=timeout).status_code == 200
    except requests.RequestException:
        return False


def ollama_models(base: str = OLLAMA_URL) -> list:
    try:
        resp = requests.get(base + "/api/tags", timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except (requests.RequestException, ValueError, KeyError):
        return []


def free_gb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return float("inf")


def download_file(url: str, dest: str, progress: ProgressFn,
                  cancel: Optional[threading.Event] = None,
                  label: str = "Downloading") -> None:
    with requests.get(url, stream=True, timeout=(15, 120)) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if cancel is not None and cancel.is_set():
                    raise SetupCancelled
                f.write(chunk)
                done += len(chunk)
                text = f"{label}… {done / 1e6:.0f} MB"
                if total:
                    text += f" of {total / 1e6:.0f} MB"
                progress(text, done / total if total else None)


def latest_ollama_installer() -> tuple:
    """(download url, sha256 hex, version tag) of the newest official
    Windows installer."""
    try:
        resp = requests.get(OLLAMA_RELEASE_API, timeout=20,
                            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        release = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise AIWriterError(f"Couldn't look up the latest Ollama release: {e}") from None
    for asset in release.get("assets", []):
        if asset.get("name") == OLLAMA_INSTALLER_ASSET:
            digest = asset.get("digest") or ""
            if not digest.startswith("sha256:"):
                raise AIWriterError("Ollama's release doesn't publish a checksum for its "
                                    "installer, so it can't be verified. Install Ollama "
                                    "yourself from https://ollama.com instead.")
            return asset["browser_download_url"], digest[7:].lower(), release.get("tag_name", "")
    raise AIWriterError("Couldn't find the Windows installer in Ollama's latest release.")


def verify_sha256(path: str, expected: str) -> None:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest().lower() != expected.lower():
        raise AIWriterError("The downloaded Ollama installer doesn't match the checksum "
                            "Ollama published, so it was not run (the download may be "
                            "corrupt or tampered with). Try again, or install Ollama "
                            "yourself from https://ollama.com.")


def signature_status(path: str) -> tuple:
    """(Authenticode status, signer subject) via PowerShell."""
    # The path goes in through an environment variable: -Command doesn't
    # populate $args, and splicing it into the script text would need
    # quoting that a crafted file name could break out of.
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath $env:CC_SIGNED_FILE; "
          "\"$($s.Status)|$($s.SignerCertificate.Subject)\"")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         capture_output=True, text=True, timeout=60,
                         env=dict(os.environ, CC_SIGNED_FILE=path),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    status, _, subject = out.stdout.strip().partition("|")
    return status, subject


def verify_signature(path: str, expected_signer: Optional[str] = None,
                     allow_unsigned: bool = False) -> str:
    """Refuse a file whose Windows code signature is invalid or tampered
    with — or, if expected_signer is given, signed by someone else.
    Returns the signer subject ("" if unsigned and allowed)."""
    if sys.platform != "win32":
        raise AIWriterError("Automatic install is only supported on Windows.")
    status, subject = signature_status(path)
    if status == "NotSigned" and allow_unsigned:
        return ""
    if status != "Valid" or (expected_signer and expected_signer.lower() not in subject.lower()):
        raise AIWriterError(
            "The downloaded Ollama installer failed its signature check "
            f"(status: {status or 'unknown'}, signer: {subject or 'none'}), so it was not run. "
            "You can install Ollama yourself from https://ollama.com instead.")
    return subject


def run_installer(path: str) -> None:
    """Run the Ollama installer silently (it installs per-user, no admin)."""
    proc = subprocess.run([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"])
    if proc.returncode != 0:
        raise AIWriterError(f"The Ollama installer exited with code {proc.returncode}.")


def start_ollama(exe: str, base: str = OLLAMA_URL, wait: float = 60.0) -> None:
    """Start `ollama serve` in the background (if not already up) and wait
    for it to answer."""
    if ollama_running(base):
        return
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0))
    deadline = time.time() + wait
    while time.time() < deadline:
        if ollama_running(base):
            return
        time.sleep(1)
    raise AIWriterError("Ollama is installed but its server did not start. "
                        "Launch Ollama from the Start menu, then press Refresh.")


def pull_model(name: str, progress: ProgressFn, cancel: Optional[threading.Event] = None,
               base: str = OLLAMA_URL) -> None:
    """Download a model through Ollama's /api/pull, reporting progress.
    Cancelling stops this download; Ollama resumes it on the next pull."""
    try:
        resp = requests.post(base + "/api/pull", json={"model": name, "stream": True},
                             stream=True, timeout=(15, 600))
    except requests.RequestException as e:
        raise AIWriterError(f"Couldn't ask Ollama to download {name}: {e}") from None
    with resp:
        if resp.status_code != 200:
            raise AIWriterError(f"Ollama couldn't download {name}: {resp.text[:300]}")
        for raw in resp.iter_lines():
            if cancel is not None and cancel.is_set():
                raise SetupCancelled
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if "error" in event:
                raise AIWriterError(f"Ollama couldn't download {name}: {event['error']}")
            total, done = event.get("total"), event.get("completed")
            status = event.get("status", "")
            if total and done is not None:
                progress(f"Downloading {name}… {done / 1e9:.1f} of {total / 1e9:.1f} GB",
                         done / total)
            elif status:
                progress(f"{name}: {status}", None)
            if status == "success":
                return
    raise AIWriterError(f"The download of {name} ended before it finished. Try again.")


def setup_ollama(model: str, progress: ProgressFn,
                 cancel: Optional[threading.Event] = None, role: str = "model") -> dict:
    """Install Ollama if needed, start it, pull `model`, then save and
    return AI writer settings using it as `role` ("model" for writing,
    "vision_model" for describing pictures)."""
    if not ollama_running():
        exe = find_ollama_exe()
        if exe is None:
            import tempfile
            tmp_dir = tempfile.mkdtemp(prefix="cc_ollama_")
            installer = os.path.join(tmp_dir, "OllamaSetup.exe")
            try:
                url, sha256, version = latest_ollama_installer()
                download_file(url, installer, progress, cancel,
                              label=f"Downloading Ollama {version}")
                progress("Verifying the installer…", None)
                verify_sha256(installer, sha256)
                # The checksum is the authority on authenticity; the
                # signature check additionally rejects a broken/tampered
                # signature. Ollama's signing name isn't documented, so
                # any valid publisher (or none) is accepted here.
                verify_signature(installer, allow_unsigned=True)
                progress("Installing Ollama (this takes a minute)…", None)
                run_installer(installer)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            exe = find_ollama_exe()
            if exe is None:
                raise AIWriterError("Ollama's installer finished, but ollama.exe was not found.")
        if not ollama_running():
            progress("Starting Ollama…", None)
            start_ollama(exe)
    installed = ollama_models()
    if model not in installed and f"{model}:latest" not in installed:
        pull_model(model, progress, cancel)
    settings = dict(load_settings(), base_url=PRESETS["Ollama"], api_key="")
    settings[role] = model
    save_settings(settings)
    progress("Ready.", 1.0)
    return settings
