"""Make an animation from still pictures — pure PIL, no Tk.

Two kinds (File → Create Animation…):
- one picture brought to life: a slow zoom, a pan, a pulse or a shake;
- a transition from one picture to another (crossfade, wipe, slide, …),
  e.g. a "before" picture turning into the "after".

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


def save_webp(path: str, frames: list, durations: list) -> None:
    """Lossless animated WebP: no quality is lost before the caption and the
    GIF export's own color reduction."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    frames[0].save(path, "WEBP", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, lossless=True, quality=0, method=0)
