"""Make an animation from still pictures — pure PIL, no Tk.

Three kinds (File → Create Animation…):
- one picture brought to life: a slow zoom, a pan, a pulse or a shake;
- a transition from one picture to another (crossfade, wipe, slide, …),
  e.g. a "before" picture turning into the "after";
- a storyboard of those moves one after another, usually planned by the
  AI writer from a description (see render_storyboard).
(Real AI-generated motion comes from ComfyUI instead — comfy_client.py.)

Each returns (frames, durations_ms); the app saves them as a lossless
animated WebP and opens that like any GIF, so captions, text boxes and the
GIF export all work on it unchanged.
"""

import math
import os
import random
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# Generated animations are kept at most this big; captions are added on top
# and the export scales up from here when it needs to.
MAX_SIDE = 1080

# label -> effect key
STILL_EFFECTS = {
    "Slow zoom in": "zoom_in",
    "Slow zoom out": "zoom_out",
    "Pan across": "pan",
    "Pulse": "pulse",
    "Shake": "shake",
}

TRANSITIONS = {
    "Crossfade": "crossfade",
    "Fade through white": "white",
    "Fade through black": "black",
    "Wipe": "wipe",
    "Slide": "slide",
    "Pixel dissolve": "dissolve",
    "Circle reveal": "circle",
    "Zoom through": "zoom",
    "Smoke bomb": "smoke",
}

# Transitions with a color of their own (the smoke).
COLORED = {"smoke"}
DEFAULT_SMOKE = "#ff5fb0"

# Effects that end where they started, so they loop without a jump.
_LOOPING = {"pulse", "shake"}


def prepare(img: Image.Image, max_side: int = MAX_SIDE) -> Image.Image:
    """An RGB copy no bigger than max_side (transparency on white)."""
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    else:
        img = img.convert("RGB")
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def fit_cover(img: Image.Image, size: tuple) -> Image.Image:
    """Scale to fill `size` exactly, cropping the overflow evenly."""
    w, h = size
    scale = max(w / img.width, h / img.height)
    rw, rh = max(w, round(img.width * scale)), max(h, round(img.height * scale))
    resized = img.resize((rw, rh), Image.LANCZOS)
    left, top = (rw - w) // 2, (rh - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _ease(t: float) -> float:
    """Smooth start and stop (smoothstep)."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _view(img: Image.Image, zoom: float, cx: float, cy: float) -> Image.Image:
    """The picture zoomed by `zoom` around (cx, cy) as fractions, kept
    inside the picture, at the picture's own size."""
    w, h = img.size
    cw, ch = w / zoom, h / zoom
    x0 = min(max(cx * w - cw / 2, 0), w - cw)
    y0 = min(max(cy * h - ch / 2, 0), h - ch)
    return img.resize((w, h), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch))


def _frame_times(seconds: float, fps: float) -> tuple:
    n = max(2, round(seconds * fps))
    return n, [round(1000 / fps)] * n


def still_frames(img: Image.Image, effect: str, seconds: float = 3.0, fps: float = 15,
                 strength: float = 0.5, focus: tuple = (0.5, 0.5),
                 bounce: bool = True, max_side: int = MAX_SIDE) -> tuple:
    """Animate one picture. `strength` 0-1 scales how far it zooms or moves;
    `focus` is where a zoom heads (e.g. a face). With `bounce`, effects that
    don't loop on their own play forwards then back so the GIF loops smoothly."""
    img = prepare(img, max_side)
    n, durations = _frame_times(seconds, fps)
    amount = 0.05 + 0.3 * max(0.0, min(1.0, strength))     # 5%..35% zoom
    fx, fy = focus
    rnd = random.Random(7)
    frames = []
    for i in range(n):
        t = i / (n - 1)
        if effect in ("zoom_in", "zoom_out"):
            k = _ease(t if effect == "zoom_in" else 1 - t)
            zoom = 1 + amount * k
            # Head from the center toward the focus as it zooms in.
            frames.append(_view(img, zoom, 0.5 + (fx - 0.5) * k, 0.5 + (fy - 0.5) * k))
        elif effect == "pan":
            k = _ease(t)
            zoom = 1 + amount
            if img.width >= img.height:
                frames.append(_view(img, zoom, k, 0.5))
            else:
                frames.append(_view(img, zoom, 0.5, k))
        elif effect == "pulse":
            # Two beats per loop, like a heartbeat, back to the start.
            k = 0.5 * (1 - math.cos(2 * math.pi * t * 2))
            frames.append(_view(img, 1 + amount * 0.4 * k, fx, fy))
        elif effect == "shake":
            if i in (0, n - 1):
                frames.append(_view(img, 1 + amount * 0.25, 0.5, 0.5))
            else:
                j = amount * 0.25 / 2
                frames.append(_view(img, 1 + amount * 0.25, 0.5 + rnd.uniform(-j, j),
                                    0.5 + rnd.uniform(-j, j)))
        else:
            raise ValueError(f"unknown effect {effect!r}")
    if bounce and effect not in _LOOPING:
        # Forwards then back; the turnaround frames aren't doubled, and a
        # short pause at each end lets it breathe.
        durations[0] = durations[-1] = 400
        frames = frames + frames[-2:0:-1]
        durations = durations + durations[-2:0:-1]
    elif effect in _LOOPING:
        frames, durations = frames[:-1], durations[:-1]   # last == first
    return frames, durations


def _mask_wipe(size: tuple, t: float, soft: int) -> Image.Image:
    """Left of the moving edge shows the new picture (255), with a soft
    2*soft px fade across the edge."""
    w, h = size
    edge = -soft + t * (w + 2 * soft)
    row = [round(255 * max(0.0, min(1.0, (edge + soft - x) / (2 * soft)))) for x in range(w)]
    line = Image.new("L", (w, 1))
    line.putdata(row)
    return line.resize((w, h), Image.NEAREST)


def _mask_circle(size: tuple, t: float, soft: int) -> Image.Image:
    w, h = size
    r = t * (math.hypot(w, h) / 2 + soft)
    mask = Image.new("L", size, 0)
    if r > 0:
        ImageDraw.Draw(mask).ellipse((w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(soft / 2)) if soft else mask


def _dissolve_noise(size: tuple, block: int = 6) -> Image.Image:
    """Fixed random blocks; each block appears when t passes its value."""
    w, h = size
    small = Image.new("L", (max(1, w // block), max(1, h // block)))
    rnd = random.Random(3)
    small.putdata([rnd.randrange(256) for _ in range(small.width * small.height)])
    return small.resize(size, Image.NEAREST)


def _fbm(w: int, h: int, seed: int, octaves: int = 5) -> np.ndarray:
    """Soft cloudy noise in 0..1 (several layers of smoothly enlarged
    random grids, big billows plus finer detail)."""
    rnd = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    total = 0.0
    for o in range(octaves):
        # Non-power-of-two cell counts and a random sub-cell offset per layer,
        # so the layers' grids don't line up into a visible pattern.
        cells = 2.3 * 2.07 ** o
        weight = 0.6 ** o
        gw = int(cells) + 3
        gh = int(cells * h / max(w, h)) + 3
        grid = (rnd.random((gh, gw)) * 255).astype(np.uint8)
        ox, oy = rnd.random(2)
        box = (ox, oy, ox + cells, oy + cells * h / max(w, h))
        layer = Image.fromarray(grid).resize((w, h), Image.BICUBIC, box=box)
        out += np.asarray(layer, np.float32) / 255 * weight
        total += weight
    out /= total
    lo, hi = np.percentile(out, (1, 99))
    return np.clip((out - lo) / max(1e-6, hi - lo), 0, 1).astype(np.float32)


class _Smoke:
    """Everything a smoke bomb needs, made once per transition: two cloud
    fields (one for the shape, one for light and shade, both taller than the
    picture so the smoke can drift up) and the distance from the blast."""

    def __init__(self, size: tuple, color: str) -> None:
        w, h = size
        self.w, self.h = w, h
        sw, sh = max(8, w // 2), max(8, h // 2)        # half size: soft anyway, 4x faster
        self.shape = Image.fromarray(_fbm(sw, int(sh * 1.4), seed=11, octaves=6), "F")
        self.shade = Image.fromarray(_fbm(sw, int(sh * 1.4), seed=29, octaves=5), "F")
        yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        # The bomb goes off a little below the middle.
        self.dist = np.hypot((xx - sw / 2) / (sw / 2), (yy - sh * 0.6) / (sh / 2)) / 1.25
        self.sw, self.sh = sw, sh
        c = color.lstrip("#")
        self.rgb = np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32)

    def _sample(self, field: Image.Image, zoom: float, drift: float) -> np.ndarray:
        """The cloud field around the blast, magnified by `zoom` (>= 1) —
        shrinking zoom over time makes the billows swell outward — and
        shifted up by `drift` rows as the smoke rises."""
        sw, sh = self.sw, self.sh
        cx = sw / 2
        cy = field.height - sh - drift + sh * 0.6
        box = (cx - sw / (2 * zoom), cy - 0.6 * sh / zoom,
               cx + sw / (2 * zoom), cy + 0.4 * sh / zoom)
        return np.asarray(field.resize((sw, sh), Image.BILINEAR, box=box))

    def frame(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        drift = t * 0.28 * self.sh                          # rising as it goes
        if t < 0.4:
            # Bursting out: fast at first, then slowly filling the corners,
            # the thick billows reaching furthest from the blast.
            x = t / 0.4
            p = 1 - (1 - x) ** 2.2
            zoom = 1.7 - 0.55 * p
            shape = self._sample(self.shape, zoom, drift)
            field = shape - self.dist * 0.9
            density = np.clip((field - (1.1 - p * 2.4)) / 0.35, 0, 1)
            base = a
        else:
            # Drifting away over the new picture, wispy parts first, slowly.
            q = (t - 0.4) / 0.6
            zoom = 1.15 - 0.15 * q
            shape = self._sample(self.shape, zoom, drift)
            density = np.clip((shape + 0.75 - q * 2.0) / 0.55, 0, 1)
            base = b
        shade = self._sample(self.shade, zoom, drift * 0.5)
        # Lit billows: the color, lighter on the bumps, darker in the folds.
        light = (shade - 0.5)[..., None]
        smoke = self.rgb * (0.82 + 0.45 * light)
        smoke = smoke + (255 - smoke) * np.clip(light * 0.6, 0, 1)
        d = Image.fromarray((density * 255).astype(np.uint8)).resize((self.w, self.h),
                                                                      Image.BICUBIC)
        s = Image.fromarray(np.clip(smoke, 0, 255).astype(np.uint8)).resize(
            (self.w, self.h), Image.BICUBIC)
        return Image.composite(s, base, d)


def blend(a: Image.Image, b: Image.Image, effect: str, t: float,
          noise: Optional[Image.Image] = None, smoke: Optional[_Smoke] = None,
          color: str = DEFAULT_SMOKE) -> Image.Image:
    """One frame of the transition from a to b (same size) at t in 0..1.
    `noise` / `smoke` are prepared once per transition (see
    transition_frames); `color` is the smoke's."""
    t = max(0.0, min(1.0, t))
    if effect == "smoke":
        return (smoke or _Smoke(a.size, color)).frame(a, b, t)
    w, h = a.size
    soft = max(4, w // 24)
    if effect == "crossfade":
        return Image.blend(a, b, _ease(t))
    if effect in ("white", "black"):
        mid = Image.new("RGB", a.size, (255, 255, 255) if effect == "white" else (0, 0, 0))
        k = _ease(t)
        return Image.blend(a, mid, k * 2) if k < 0.5 else Image.blend(mid, b, k * 2 - 1)
    if effect == "wipe":
        return Image.composite(b, a, _mask_wipe(a.size, _ease(t), soft))
    if effect == "slide":
        off = round(_ease(t) * w)
        out = Image.new("RGB", a.size)
        out.paste(a, (-off, 0))
        out.paste(b, (w - off, 0))
        return out
    if effect == "dissolve":
        noise = noise or _dissolve_noise(a.size)
        cut = round(t * 256)
        return Image.composite(b, a, noise.point(lambda v: 255 if v < cut else 0))
    if effect == "circle":
        return Image.composite(b, a, _mask_circle(a.size, _ease(t), soft))
    if effect == "zoom":
        k = _ease(t)
        za = _view(a, 1 + 0.35 * k, 0.5, 0.5)
        zb = _view(b, 1.35 - 0.35 * k, 0.5, 0.5)
        return Image.blend(za, zb, k)
    raise ValueError(f"unknown transition {effect!r}")


def transition_frames(a: Image.Image, b: Image.Image, effect: str, hold_a: float = 1.0,
                      seconds: float = 1.0, hold_b: float = 1.5, fps: float = 15,
                      loop_back: bool = True, color: str = DEFAULT_SMOKE,
                      max_side: int = MAX_SIDE) -> tuple:
    """Hold on a, change to b over `seconds`, hold on b — and with
    `loop_back`, change back to a so the GIF loops without a jump. Holds are
    single frames with a long delay, which keeps the file small. The second
    picture is cropped to the first one's shape. `color` is the smoke's, for
    the smoke bomb."""
    a = prepare(a, max_side)
    b = fit_cover(prepare(b, max(a.size) * 2), a.size)
    n = max(2, round(seconds * fps))
    step_ms = round(1000 / fps)
    noise = _dissolve_noise(a.size) if effect == "dissolve" else None
    smoke = _Smoke(a.size, color) if effect == "smoke" else None
    frames, durations = [a], [max(20, round(hold_a * 1000))]
    for i in range(1, n):
        frames.append(blend(a, b, effect, i / n, noise, smoke))
        durations.append(step_ms)
    frames.append(b)
    durations.append(max(20, round(hold_b * 1000)))
    if loop_back:
        for i in range(1, n):
            frames.append(blend(b, a, effect, i / n, noise, smoke))
            durations.append(step_ms)
    return frames, durations


# ---------------------------------------------------------------------------
# Storyboards: a list of steps played one after another — what the AI plans
# from a description ("hold, zoom into her face, purple smoke bomb to the
# after picture"). Each step carries on from where the last one ended (the
# same picture, zoom and spot), so the whole thing flows as one shot.
#
#   {"type": "hold",       "picture": "first"|"second", "seconds": 1}
#   {"type": "zoom",       "to": 1.4, "focus": "face"|"center"|"top"|…|[x, y], "seconds": 2}
#   {"type": "pan",        "direction": "left"|"right"|"up"|"down", "seconds": 2}
#   {"type": "shake",      "strength": 0-1, "seconds": 0.6}
#   {"type": "pulse",      "beats": 2, "strength": 0-1, "seconds": 1.2}
#   {"type": "flash",      "color": "#ffffff", "seconds": 0.4}
#   {"type": "transition", "effect": "smoke"|…, "to": "first"|"second",
#                          "color": "#b05cc9", "seconds": 1.5}
# ---------------------------------------------------------------------------

STEP_TYPES = ("hold", "zoom", "pan", "shake", "pulse", "flash", "transition")
MAX_STEPS = 14
MAX_SECONDS = 20.0

_STEP_ALIASES = {
    "show": "hold", "pause": "hold", "wait": "hold", "still": "hold", "stay": "hold",
    "zoom_in": "zoom", "zoom_out": "zoom", "zoomin": "zoom", "zoomout": "zoom", "push": "zoom",
    "move": "pan", "slide_view": "pan", "scroll": "pan",
    "tremble": "shake", "jitter": "shake", "quake": "shake",
    "heartbeat": "pulse", "throb": "pulse", "beat": "pulse",
    "change": "transition", "switch": "transition", "reveal": "transition",
    "transform": "transition", "fade": "transition", "cut": "transition",
    "blink": "flash", "strobe": "flash",
}

_FOCUS_WORDS = {
    "center": (0.5, 0.5), "centre": (0.5, 0.5), "middle": (0.5, 0.5),
    "top": (0.5, 0.25), "bottom": (0.5, 0.75), "left": (0.25, 0.5), "right": (0.75, 0.5),
    "chest": (0.5, 0.45), "body": (0.5, 0.55), "legs": (0.5, 0.78), "feet": (0.5, 0.9),
    "head": (0.5, 0.2),
}

_COLOR_WORDS = {
    "pink": "#ff5fb0", "hot pink": "#ff2d95", "purple": "#b05cc9", "violet": "#8a3cff",
    "lavender": "#c9a3ff", "lilac": "#c8a2c8", "red": "#e8322e", "blue": "#4f7dff",
    "light blue": "#8ecbff", "cyan": "#3fe0ff", "teal": "#1fb5a8", "green": "#3fcf6a",
    "yellow": "#ffd93d", "gold": "#e5b53a", "orange": "#ff8a2b", "white": "#ffffff",
    "black": "#202024", "grey": "#8c8c8c", "gray": "#8c8c8c", "silver": "#c8c8d0",
}

# For the AI: what it may use, in words it can follow.
PLAN_GUIDE = (
    "Steps (play in order; each continues from where the last ended):\n"
    '- {"type": "hold", "picture": "first" or "second", "seconds": 1} — show a picture '
    "without moving (use it first to choose the starting picture)\n"
    '- {"type": "zoom", "to": 1.0-2.5, "focus": "face"|"center"|"top"|"bottom"|"left"|'
    '"right"|"chest"|"legs", "seconds": 2} — zoom in (bigger number) or back out (1.0) '
    "toward a spot\n"
    '- {"type": "pan", "direction": "left"|"right"|"up"|"down", "seconds": 2} — slide '
    "the view across the picture\n"
    '- {"type": "shake", "strength": 0.1-1, "seconds": 0.6} — a jolt or tremble\n'
    '- {"type": "pulse", "beats": 1-4, "strength": 0.1-1, "seconds": 1.2} — a '
    "heartbeat-like throb\n"
    '- {"type": "flash", "color": "#ffffff", "seconds": 0.4} — a quick flash of color\n'
    '- {"type": "transition", "effect": EFFECT, "to": "first" or "second", '
    '"color": "#hex (smoke only)", "seconds": 1.5} — change to a picture (or to the same '
    "picture, e.g. a puff of smoke)\n"
    "EFFECT is one of: crossfade, white (fade through white), black (fade through "
    "black), wipe, slide, dissolve (pixels), circle (circle reveal), zoom (zoom through), "
    "smoke (a colored smoke bomb).\n"
)


def _num(v, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _color(v, default: str) -> str:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _COLOR_WORDS:
            return _COLOR_WORDS[s]
        h = s.lstrip("#")
        if len(h) == 3 and all(c in "0123456789abcdef" for c in h):
            h = "".join(c * 2 for c in h)
        if len(h) == 6 and all(c in "0123456789abcdef" for c in h):
            return "#" + h
        for word, hexv in _COLOR_WORDS.items():      # "light purple smoke"
            if word in s:
                return hexv
    return default


def _picture(v, has_second: bool, default: str = "first") -> str:
    s = str(v).strip().lower() if v is not None else ""
    if s in ("second", "2", "after", "b", "end", "last", "new", "other"):
        return "second" if has_second else "first"
    if s in ("first", "1", "before", "a", "start", "original"):
        return "first"
    return default if (default == "first" or has_second) else "first"


def _effect(v) -> str:
    s = str(v or "").strip().lower().replace("-", " ").replace("_", " ")
    if s in TRANSITIONS.values():
        return s
    for label, key in TRANSITIONS.items():
        if s == label.lower():
            return key
    for word, key in (("smoke", "smoke"), ("white", "white"), ("black", "black"),
                      ("wipe", "wipe"), ("slide", "slide"), ("pixel", "dissolve"),
                      ("dissolve", "dissolve"), ("circle", "circle"), ("iris", "circle"),
                      ("zoom", "zoom"), ("cross", "crossfade"), ("fade", "crossfade"),
                      ("blend", "crossfade")):
        if word in s:
            return key
    return "crossfade"


def normalize_plan(steps, has_second: bool) -> list:
    """Clean up a storyboard (typically from an AI): unknown steps dropped,
    names and values mapped and clamped, length capped. Always returns a
    playable list (at least one step)."""
    if isinstance(steps, dict):
        steps = steps.get("steps") or steps.get("plan") or [steps]
    out, total = [], 0.0
    current = "first"
    for raw in steps if isinstance(steps, list) else []:
        if not isinstance(raw, dict) or len(out) >= MAX_STEPS:
            continue
        kind = str(raw.get("type") or raw.get("step") or raw.get("action") or "").lower()
        kind = kind.strip().replace(" ", "_")
        kind = _STEP_ALIASES.get(kind, kind)
        if kind not in STEP_TYPES:
            continue
        step = {"type": kind}
        default_secs = {"hold": 1.0, "shake": 0.6, "flash": 0.4, "pulse": 1.2}.get(kind, 1.5)
        step["seconds"] = _num(raw.get("seconds", raw.get("duration")), default_secs, 0.1, 6)
        if total + step["seconds"] > MAX_SECONDS:
            break
        if kind == "hold":
            step["picture"] = current = _picture(raw.get("picture"), has_second, current)
        elif kind == "zoom":
            to = raw.get("to", raw.get("zoom", raw.get("amount")))
            if to is None:
                to = 1.0 if "out" in str(raw.get("type", "")).lower() else 1.4
            step["to"] = _num(to, 1.4, 1.0, 3.0)
            focus = raw.get("focus", "face")
            if isinstance(focus, (list, tuple)) and len(focus) == 2:
                step["focus"] = [_num(focus[0], 0.5, 0, 1), _num(focus[1], 0.5, 0, 1)]
            else:
                f = str(focus).strip().lower()
                step["focus"] = f if (f == "face" or f in _FOCUS_WORDS) else "face"
        elif kind == "pan":
            d = str(raw.get("direction", "right")).strip().lower()
            step["direction"] = d if d in ("left", "right", "up", "down") else "right"
        elif kind == "shake":
            step["strength"] = _num(raw.get("strength"), 0.5, 0.05, 1)
        elif kind == "pulse":
            step["beats"] = int(_num(raw.get("beats"), 2, 1, 6))
            step["strength"] = _num(raw.get("strength"), 0.5, 0.05, 1)
        elif kind == "flash":
            step["color"] = _color(raw.get("color"), "#ffffff")
        elif kind == "transition":
            step["effect"] = _effect(raw.get("effect", raw.get("style")))
            other = "second" if current == "first" else "first"
            step["to"] = current = _picture(raw.get("to", raw.get("picture")), has_second,
                                            other if has_second else "first")
            if step["effect"] in COLORED:
                step["color"] = _color(raw.get("color"), DEFAULT_SMOKE)
        out.append(step)
        total += step["seconds"]
    return out or [{"type": "hold", "picture": "first", "seconds": 1.0}]


def describe_step(step: dict) -> str:
    """One line a person can read, e.g. "Zoom in toward the face — 2.0s"."""
    k, secs = step["type"], f"{step['seconds']:.1f}s"
    if k == "hold":
        return f"Show the {step['picture']} picture — {secs}"
    if k == "zoom":
        f = step["focus"]
        where = ("the face" if f == "face" else f"the {f}" if isinstance(f, str)
                 else "a spot")
        verb = "Zoom back out" if step["to"] <= 1.01 else f"Zoom in to {step['to']:.1f}×"
        return f"{verb} toward {where} — {secs}"
    if k == "pan":
        return f"Pan {step['direction']} — {secs}"
    if k == "shake":
        return f"Shake ({round(step['strength'] * 100)}%) — {secs}"
    if k == "pulse":
        return f"Pulse ×{step['beats']} — {secs}"
    if k == "flash":
        return f"Flash {step['color']} — {secs}"
    label = next((lbl for lbl, key in TRANSITIONS.items() if key == step["effect"]),
                 step["effect"])
    extra = f" ({step['color']})" if "color" in step else ""
    return f"{label}{extra} to the {step['to']} picture — {secs}"


def render_storyboard(first: Image.Image, second: Optional[Image.Image], steps: list,
                      fps: float = 12, faces: Optional[dict] = None,
                      loop: bool = True, max_side: int = MAX_SIDE) -> tuple:
    """Play `steps` (see normalize_plan) into (frames, durations_ms).
    `faces` maps "first"/"second" to a face's (x, y) center, for zooms that
    head for the face. With `loop`, a short crossfade back to the opening
    shot is added when it ends somewhere else, so the GIF loops smoothly."""
    pics = {"first": prepare(first, max_side)}
    if second is not None:
        pics["second"] = fit_cover(prepare(second, max(pics["first"].size) * 2),
                                   pics["first"].size)
    faces = faces or {}
    step_ms = round(1000 / fps)
    frames: list = []
    durations: list = []
    # The camera: which picture, how far in, and where it's looking.
    cam = {"pic": "first", "zoom": 1.0, "cx": 0.5, "cy": 0.5}
    if steps and steps[0]["type"] == "hold":
        cam["pic"] = steps[0].get("picture", "first") if steps[0].get(
            "picture") in pics else "first"
    start = dict(cam)

    def view(c: dict) -> Image.Image:
        return _view(pics[c["pic"]], c["zoom"], c["cx"], c["cy"])

    def emit(img: Image.Image, ms: int) -> None:
        frames.append(img)
        durations.append(max(20, int(ms)))

    def n_frames(secs: float) -> int:
        return max(2, round(secs * fps))

    for step in steps:
        k = step["type"]
        if k == "hold":
            if step.get("picture") in pics:
                cam["pic"] = step["picture"]
            emit(view(cam), step["seconds"] * 1000)
        elif k == "zoom":
            f = step["focus"]
            if f == "face":
                tx, ty = faces.get(cam["pic"], (0.5, 0.4))
            elif isinstance(f, list):
                tx, ty = f
            else:
                tx, ty = _FOCUS_WORDS.get(f, (0.5, 0.5))
            z0, x0, y0 = cam["zoom"], cam["cx"], cam["cy"]
            z1 = step["to"]
            if z1 <= 1.01:
                tx, ty = 0.5, 0.5
            n = n_frames(step["seconds"])
            for i in range(1, n + 1):
                k2 = _ease(i / n)
                cam.update(zoom=z0 + (z1 - z0) * k2, cx=x0 + (tx - x0) * k2,
                           cy=y0 + (ty - y0) * k2)
                emit(view(cam), step_ms)
        elif k == "pan":
            cam["zoom"] = max(cam["zoom"], 1.25)
            axis = "cx" if step["direction"] in ("left", "right") else "cy"
            # "right" moves the view toward the right side of the picture,
            # from wherever it is now.
            end = 0.85 if step["direction"] in ("right", "down") else 0.15
            n = n_frames(step["seconds"])
            begin = cam[axis]
            for i in range(1, n + 1):
                cam[axis] = begin + (end - begin) * _ease(i / n)
                emit(view(cam), step_ms)
        elif k == "shake":
            rnd = random.Random(len(frames))
            n = n_frames(step["seconds"])
            zoom = max(cam["zoom"], 1.0 + 0.1 * step["strength"])
            j = 0.06 * step["strength"] / zoom
            for i in range(n):
                fade = 1 - i / n
                c = dict(cam, zoom=zoom, cx=cam["cx"] + rnd.uniform(-j, j) * fade,
                         cy=cam["cy"] + rnd.uniform(-j, j) * fade)
                emit(view(c), step_ms)
            emit(view(dict(cam, zoom=zoom)), step_ms)
            cam["zoom"] = zoom
        elif k == "pulse":
            n = n_frames(step["seconds"])
            for i in range(1, n + 1):
                beat = 0.5 * (1 - math.cos(2 * math.pi * step["beats"] * i / n))
                emit(view(dict(cam, zoom=cam["zoom"] * (1 + 0.12 * step["strength"] * beat))),
                     step_ms)
        elif k == "flash":
            base = view(cam)
            flat = Image.new("RGB", base.size, _rgb_hex(step["color"]))
            n = n_frames(step["seconds"])
            for i in range(1, n + 1):
                emit(Image.blend(base, flat, math.sin(math.pi * i / n) ** 2 * 0.9), step_ms)
        elif k == "transition":
            a = view(cam)
            target = step["to"] if step["to"] in pics else "first"
            b = pics[target]
            effect = step["effect"]
            noise = _dissolve_noise(a.size) if effect == "dissolve" else None
            smoke = _Smoke(a.size, step.get("color", DEFAULT_SMOKE)) if effect == "smoke" else None
            n = n_frames(step["seconds"])
            for i in range(1, n):
                emit(blend(a, b, effect, i / n, noise, smoke), step_ms)
            emit(b, step_ms)
            cam = {"pic": target, "zoom": 1.0, "cx": 0.5, "cy": 0.5}
    if not frames:
        emit(view(cam), 1000)
    if loop and (cam["pic"], round(cam["zoom"], 2)) != (start["pic"], round(start["zoom"], 2)):
        a, b = view(cam), view(start)
        n = n_frames(0.6)
        for i in range(1, n):
            emit(blend(a, b, "crossfade", i / n), step_ms)
    return frames, durations


def _rgb_hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def save_webp(path: str, frames: list, durations: list) -> None:
    """Lossless animated WebP: no quality is lost before the caption and the
    GIF export's own color reduction."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    frames[0].save(path, "WEBP", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, lossless=True, quality=0, method=0)
