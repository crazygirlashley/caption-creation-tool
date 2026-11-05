"""AI-generated animation through ComfyUI — pure, no Tk.

Real image-to-video models (Wan, LTX-Video, …) need a graphics card with a
lot of memory, so Caption Creator doesn't run them itself: it sends the
picture and a description of the motion to a ComfyUI server — on this PC, on
another PC at home, or a rented cloud GPU — and loads the clip it makes.

The workflow is the user's own, exported from ComfyUI in API format
(Workflow → Export (API)), so any model and any settings work. This module
puts the picture(s) and the description into it:
- placeholders, if the workflow has them: %prompt%, %negative%, %image%,
  %image2% (anywhere in a text field);
- otherwise found automatically: LoadImage nodes get the pictures, and the
  text node that feeds a sampler's "positive" input gets the description.
Seeds are randomized so each run differs.
"""

import io
import json
import os
import random
import re
import tempfile
import threading
import time
import uuid
from typing import Callable, Optional

import requests
from PIL import Image, ImageSequence

import content_rules

DEFAULT_URL = "http://127.0.0.1:8188"

_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".avi")
_ANIM_EXTS = (".gif", ".webp", ".png", ".apng")

ProgressFn = Callable[[str], None]


class ComfyError(Exception):
    """A user-presentable failure."""


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _unreachable(base: str) -> str:
    return (f"Couldn't reach ComfyUI at {base}.\n\nStart ComfyUI (on this PC or the one with "
            "the graphics card) and check the address. To use it from another PC, start "
            "ComfyUI with --listen so it accepts connections from your network.")


def check(base: str, timeout: float = 5.0) -> dict:
    """ComfyUI's /system_stats (its version and graphics cards); raises
    ComfyError if it can't be reached."""
    try:
        resp = requests.get(_url(base, "/system_stats"), timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        raise ComfyError(_unreachable(base)) from None
    except (requests.RequestException, ValueError) as e:
        raise ComfyError(f"{base} answered, but not like ComfyUI: {e}") from None


def describe_server(stats: dict) -> str:
    """e.g. "ComfyUI 0.3.40 · NVIDIA GeForce RTX 4090 (24 GB)"."""
    version = (stats.get("system") or {}).get("comfyui_version", "")
    cards = []
    for dev in stats.get("devices") or []:
        # "cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync" -> the card's name
        name = re.sub(r"^\w+:\d+\s+", "", str(dev.get("name", "")))
        name = name.split(" : ", 1)[0].strip() or dev.get("type", "")
        vram = dev.get("vram_total")
        cards.append(f"{name} ({vram / 2**30:.0f} GB)" if vram else name)
    return " · ".join(p for p in (f"ComfyUI {version}".strip(), ", ".join(cards)) if p)


def load_workflow(path: str) -> dict:
    """An API-format workflow ({node id: {"class_type", "inputs"}})."""
    try:
        with open(path, encoding="utf-8") as f:
            wf = json.load(f)
    except (OSError, ValueError) as e:
        raise ComfyError(f"Couldn't read the workflow: {e}") from None
    if isinstance(wf, dict) and "nodes" in wf and "links" in wf:
        raise ComfyError("This is a ComfyUI workflow in its normal (editor) format. Open it "
                         "in ComfyUI and use Workflow → Export (API), then choose that file.")
    if not isinstance(wf, dict) or not wf or not all(
            isinstance(n, dict) and "class_type" in n for n in wf.values()):
        raise ComfyError("That file isn't a ComfyUI API workflow. In ComfyUI use "
                         "Workflow → Export (API).")
    return wf


def _is_link(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _text_node_for(wf: dict, which: str) -> Optional[str]:
    """The text-encode node feeding any node's `which` ("positive" or
    "negative") input, followed back through conditioning nodes."""
    for node in wf.values():
        link = node.get("inputs", {}).get(which)
        seen = set()
        while _is_link(link) and link[0] in wf and link[0] not in seen:
            seen.add(link[0])
            src = wf[link[0]]
            inputs = src.get("inputs", {})
            if isinstance(inputs.get("text"), str) or isinstance(inputs.get("prompt"), str):
                return link[0]
            # Through a conditioning node (e.g. an image-to-video node that
            # takes and passes on "positive"), or any single conditioning input.
            link = inputs.get(which) or inputs.get("conditioning") or next(
                (v for v in inputs.values() if _is_link(v)), None)
    return None


def _sorted_ids(ids) -> list:
    return sorted(ids, key=lambda i: (len(str(i)), str(i)))


def fill_workflow(wf: dict, images: list, prompt: str, negative: str = "",
                  size: Optional[tuple] = None, seed: Optional[int] = None,
                  safety: str = "") -> tuple:
    """A copy of `wf` with the uploaded image name(s), the description and a
    new seed filled in; with `size` (w, h), width/height inputs are set to
    that shape at the workflow's own pixel count. `safety` terms are added
    to the negative prompt (see content_rules.video_negative). Returns
    (workflow, notes) where notes says what was filled (shown to the user)."""
    wf = json.loads(json.dumps(wf))
    notes = []
    neg_text = ", ".join(t for t in (negative, safety) if t)
    placeholders = {"%prompt%": prompt, "%negative%": neg_text,
                    "%image%": images[0] if images else "",
                    "%image2%": images[1] if len(images) > 1 else (images[0] if images else "")}
    used = set()
    for node in wf.values():
        for key, v in node.get("inputs", {}).items():
            if isinstance(v, str) and "%" in v:
                for ph, value in placeholders.items():
                    if ph in v:
                        v = v.replace(ph, value)
                        used.add(ph)
                node["inputs"][key] = v
    if "%image%" not in used:
        loaders = _sorted_ids(i for i, n in wf.items() if n["class_type"] == "LoadImage")
        if not loaders:
            raise ComfyError("The workflow has no Load Image node to put your picture in. Use "
                             "an image-to-video workflow (one that starts from a picture).")
        for n, node_id in enumerate(loaders):
            wf[node_id]["inputs"]["image"] = images[min(n, len(images) - 1)]
        notes.append(f"picture → Load Image ({len(loaders)} found)")
    else:
        notes.append("picture → %image%")
    if "%prompt%" not in used:
        node_id = _text_node_for(wf, "positive")
        if node_id is None:
            texts = [i for i, n in wf.items() if "TextEncode" in n["class_type"]
                     and isinstance(n.get("inputs", {}).get("text"), str)]
            node_id = texts[0] if len(texts) == 1 else None
        if node_id is None:
            raise ComfyError("Couldn't find where the description goes. In ComfyUI, type "
                             "%prompt% in the positive prompt box, then export (API) again.")
        key = "text" if isinstance(wf[node_id]["inputs"].get("text"), str) else "prompt"
        wf[node_id]["inputs"][key] = prompt
        notes.append(f"description → {wf[node_id]['class_type']} (node {node_id})")
    else:
        notes.append("description → %prompt%")
    if safety and "%negative%" not in used:
        node_id = _text_node_for(wf, "negative")
        if node_id is not None:
            key = "text" if isinstance(wf[node_id]["inputs"].get("text"), str) else "prompt"
            old = wf[node_id]["inputs"][key].strip().rstrip(",")
            wf[node_id]["inputs"][key] = f"{old}, {safety}" if old else safety
            notes.append("safety terms → negative prompt")
        else:
            notes.append("no negative prompt found for the safety terms")
    elif safety:
        notes.append("safety terms → %negative%")
    rnd = random.Random(seed)
    for node in wf.values():
        for key in ("seed", "noise_seed"):
            if isinstance(node.get("inputs", {}).get(key), int):
                node["inputs"][key] = rnd.randrange(2**48)
    if size:
        w0, h0 = size
        for node in wf.values():
            inp = node.get("inputs", {})
            if node["class_type"] != "LoadImage" and isinstance(inp.get("width"), int) \
                    and isinstance(inp.get("height"), int):
                area = inp["width"] * inp["height"]
                ratio = w0 / h0
                w = max(256, round((area * ratio) ** 0.5 / 32) * 32)
                h = max(256, round((area / ratio) ** 0.5 / 32) * 32)
                inp["width"], inp["height"] = w, h
                notes.append(f"size → {w}×{h} (your picture's shape)")
    return wf, notes


def workflow_fps(wf: dict, default: float = 16.0) -> float:
    for node in wf.values():
        for key in ("fps", "frame_rate"):
            v = node.get("inputs", {}).get(key)
            if isinstance(v, (int, float)) and 1 <= v <= 120:
                return float(v)
    return default


def upload_image(base: str, img: Image.Image, name: str) -> str:
    """Upload a PIL image to ComfyUI's input folder; returns its name there."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    try:
        resp = requests.post(_url(base, "/upload/image"),
                             files={"image": (name, buf.getvalue(), "image/png")},
                             data={"overwrite": "true", "type": "input"}, timeout=60)
        resp.raise_for_status()
        info = resp.json()
    except requests.ConnectionError:
        raise ComfyError(_unreachable(base)) from None
    except (requests.RequestException, ValueError) as e:
        raise ComfyError(f"Couldn't send the picture to ComfyUI: {e}") from None
    sub = info.get("subfolder") or ""
    return f"{sub}/{info['name']}" if sub else info["name"]


def queue(base: str, wf: dict, client_id: str) -> str:
    try:
        resp = requests.post(_url(base, "/prompt"),
                             json={"prompt": wf, "client_id": client_id}, timeout=30)
    except requests.ConnectionError:
        raise ComfyError(_unreachable(base)) from None
    except requests.RequestException as e:
        raise ComfyError(f"Couldn't start the job: {e}") from None
    if resp.status_code != 200:
        # ComfyUI explains what's wrong with the workflow (a missing model
        # file, a node that isn't installed, …).
        try:
            data = resp.json()
            err = data.get("error", {})
            msg = err.get("message", "") if isinstance(err, dict) else str(err)
            details = []
            for node_id, ne in (data.get("node_errors") or {}).items():
                for e in ne.get("errors", []):
                    details.append(f"{ne.get('class_type', node_id)}: "
                                   f"{e.get('details') or e.get('message')}")
            text = msg + ("\n" + "\n".join(details[:6]) if details else "")
        except ValueError:
            text = resp.text[:400]
        raise ComfyError(f"ComfyUI couldn't run the workflow:\n{text}")
    return resp.json()["prompt_id"]


def interrupt(base: str) -> None:
    try:
        requests.post(_url(base, "/interrupt"), timeout=10)
    except requests.RequestException:
        pass


def wait(base: str, prompt_id: str, progress: ProgressFn,
         cancel: Optional[threading.Event] = None, timeout: float = 3600,
         poll: float = 1.5) -> dict:
    """Wait for the job; returns its outputs ({node id: {"images": […], …}})."""
    start = time.time()
    while True:
        if cancel is not None and cancel.is_set():
            interrupt(base)
            raise ComfyError("cancelled")
        if time.time() - start > timeout:
            raise ComfyError("ComfyUI didn't finish within an hour.")
        try:
            resp = requests.get(_url(base, f"/history/{prompt_id}"), timeout=15)
            resp.raise_for_status()
            hist = resp.json().get(prompt_id)
        except (requests.RequestException, ValueError):
            hist = None
        if hist:
            status = hist.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m[1].get("exception_message", "") for m in status.get("messages", [])
                        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error"]
                raise ComfyError("ComfyUI stopped with an error:\n"
                                 + (msgs[0] if msgs else "see the ComfyUI window"))
            if status.get("completed", True) and hist.get("outputs"):
                return hist["outputs"]
        elapsed = int(time.time() - start)
        ahead = _queue_position(base, prompt_id)
        progress(f"Waiting in ComfyUI's queue ({ahead} ahead)… {elapsed}s" if ahead else
                 f"Generating… {elapsed // 60}:{elapsed % 60:02d} (this can take several "
                 "minutes)")
        time.sleep(poll)


def _queue_position(base: str, prompt_id: str) -> int:
    try:
        q = requests.get(_url(base, "/queue"), timeout=10).json()
    except (requests.RequestException, ValueError):
        return 0
    pending = [item[1] for item in q.get("queue_pending", []) if len(item) > 1]
    return pending.index(prompt_id) + len(q.get("queue_running", [])) \
        if prompt_id in pending else 0


def output_files(outputs: dict) -> list:
    """[{"filename", "subfolder", "type"}] of everything the job saved."""
    files = []
    for node_out in outputs.values():
        for key in ("gifs", "videos", "images", "animated"):
            for item in node_out.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    files.append(item)
    # Preview nodes leave "temp" files (often the input picture); the result
    # is what was saved, when there is any.
    saved = [f for f in files if f.get("type", "output") == "output"]
    return saved or files


def download(base: str, item: dict) -> bytes:
    try:
        resp = requests.get(_url(base, "/view"), params={
            "filename": item["filename"], "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output")}, timeout=300)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        raise ComfyError(f"Couldn't download {item['filename']} from ComfyUI: {e}") from None


def to_frames(files: list, fps: float) -> tuple:
    """(frames, durations_ms) from what the job made: one video or animated
    image, or a run of still frames. `files` is [(filename, bytes)]."""
    def ext(name: str) -> str:
        return os.path.splitext(name)[1].lower()

    videos = [f for f in files if ext(f[0]) in _VIDEO_EXTS]
    if videos:
        import imageio.v2 as imageio
        name, data = videos[-1]
        tmp = tempfile.NamedTemporaryFile(suffix=ext(name), delete=False)
        try:
            tmp.write(data)
            tmp.close()
            reader = imageio.get_reader(tmp.name, "ffmpeg")
            try:
                vfps = reader.get_meta_data().get("fps") or fps
                frames = [Image.fromarray(fr).convert("RGB") for fr in reader]
            finally:
                reader.close()
        finally:
            os.unlink(tmp.name)
        if not frames:
            raise ComfyError("The video ComfyUI made has no frames.")
        return frames, [round(1000 / vfps)] * len(frames)
    for name, data in reversed(files):
        if ext(name) in _ANIM_EXTS:
            with Image.open(io.BytesIO(data)) as im:
                if getattr(im, "n_frames", 1) > 1:
                    frames, durs = [], []
                    for fr in ImageSequence.Iterator(im):
                        fr.load()
                        durs.append(fr.info.get("duration") or round(1000 / fps))
                        frames.append(fr.convert("RGB"))
                    return frames, durs
    stills = [f for f in files if ext(f[0]) in (".png", ".jpg", ".jpeg", ".webp")]
    if len(stills) < 2:
        raise ComfyError("ComfyUI finished but didn't make an animation. Make sure the "
                         "workflow ends in a node that saves a video, an animated WebP/GIF, "
                         "or the frames.")
    frames = [Image.open(io.BytesIO(d)).convert("RGB") for _n, d in stills]
    return frames, [round(1000 / fps)] * len(frames)


def generate(base: str, workflow_path: str, pictures: list, prompt: str,
             progress: ProgressFn, cancel: Optional[threading.Event] = None,
             match_shape: bool = True, allow_explicit: bool = False,
             minor_subject: Optional[bool] = None) -> tuple:
    """The whole job: returns (frames, durations_ms, notes).
    minor_subject is the picture model's age check: True (someone may be
    under 18), False (adults) or None (not checked). A sexual description is
    refused for a possible minor; explicit content is only let through for
    checked adults with `allow_explicit`, and otherwise steered away from in
    the negative prompt (content_rules)."""
    why = content_rules.blocked_reason(prompt, minor_context=bool(minor_subject))
    if why:
        raise ComfyError(content_rules.message(why))
    safety = content_rules.video_negative(allow_explicit and minor_subject is False,
                                          minor_subject=bool(minor_subject))
    wf = load_workflow(workflow_path)
    check(base)
    progress("Sending the picture to ComfyUI…")
    tag = uuid.uuid4().hex[:8]
    names = [upload_image(base, p, f"caption_creator_{tag}_{i + 1}.png")
             for i, p in enumerate(pictures)]
    filled, notes = fill_workflow(wf, names, prompt,
                                  size=pictures[0].size if match_shape else None,
                                  safety=safety)
    prompt_id = queue(base, filled, uuid.uuid4().hex)
    progress("Queued in ComfyUI…")
    outputs = wait(base, prompt_id, progress, cancel)
    progress("Downloading the result…")
    files = [(item["filename"], download(base, item)) for item in output_files(outputs)]
    frames, durations = to_frames(files, workflow_fps(filled))
    return frames, durations, notes
