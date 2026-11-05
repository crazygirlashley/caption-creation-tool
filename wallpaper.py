"""App background image/GIF ("wallpaper") rendering — pure PIL, no Tk.

The app shows it behind the Editor's cards and the Caption Builder's
preview, either covering the area ("fit": scaled to fill, center-cropped)
or repeated as a pattern ("tile"), optionally dimmed toward the theme's
background color so text on top stays readable. Animated GIFs/WebPs keep
their frames and timing (capped — see MAX_FRAMES). Transparent pixels
show the theme's background color.
"""

import os
import shutil
from typing import Optional

from PIL import Image, ImageSequence

import app_paths

# Frames kept from an animated background: a full-window frame costs a few
# MB as a Tk image, so long animations are thinned out (timing preserved).
MAX_FRAMES = 40
STORE_DIR = os.path.join(app_paths.BASE_DIR, "backgrounds")


class Wallpaper:
    def __init__(self, path: str, scale: float = 1.0) -> None:
        self.path = path
        frames, durations = [], []
        with Image.open(path) as img:
            for frm in ImageSequence.Iterator(img):
                frame = frm.convert("RGBA")
                if scale and scale != 1.0:
                    frame = frame.resize((max(1, round(frame.width * scale)),
                                          max(1, round(frame.height * scale))), Image.LANCZOS)
                frames.append(frame)
                durations.append(max(20, int(frm.info.get("duration", 100) or 100)))
        if not frames:
            raise ValueError("no frames")
        if len(frames) > MAX_FRAMES:
            step = len(frames) / MAX_FRAMES
            keep = [int(i * step) for i in range(MAX_FRAMES)]
            durations = [sum(durations[keep[i]:(keep[i + 1] if i + 1 < len(keep) else len(durations))])
                         for i in range(len(keep))]
            frames = [frames[i] for i in keep]
        self.frames = frames
        self.durations = durations

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def render(self, index: int, size: tuple, mode: str = "fit", dim: float = 0.0,
               dim_color: tuple = (0, 0, 0), gap: int = 0, stagger: bool = False) -> Image.Image:
        """Frame `index` laid out over an area of `size` (w, h), on
        `dim_color` wherever the image is transparent. Tiles can be spaced
        `gap` px apart, with every other row shifted half a tile (`stagger`)."""
        w, h = max(1, int(size[0])), max(1, int(size[1]))
        frame = self.frames[index % len(self.frames)]
        out = Image.new("RGBA", (w, h), tuple(dim_color) + (255,))
        if mode == "tile":
            step_x, step_y = frame.width + gap, frame.height + gap
            for row, y in enumerate(range(-step_y if stagger else 0, h, step_y)):
                shift = (step_x // 2) if stagger and row % 2 else 0
                for x in range(-shift, w, step_x):
                    # A masked paste blends transparency like alpha_composite
                    # but also accepts tiles hanging off the top/left edge.
                    out.paste(frame, (x, y), frame)
        else:  # fit = cover the area, keep aspect, crop the overflow
            scale = max(w / frame.width, h / frame.height)
            rw, rh = max(1, round(frame.width * scale)), max(1, round(frame.height * scale))
            resized = frame.resize((rw, rh), Image.BILINEAR if self.animated else Image.LANCZOS)
            left, top = (rw - w) // 2, (rh - h) // 2
            out.alpha_composite(resized.crop((left, top, left + w, top + h)))
        out = out.convert("RGB")
        if dim > 0:
            out = Image.blend(out, Image.new("RGB", out.size, tuple(dim_color)), min(0.95, dim))
        return out


def store_copy(path: str) -> str:
    """Copy a chosen background into the app's own backgrounds/ folder so
    it keeps working if the original is moved or deleted. Returns the copy."""
    os.makedirs(STORE_DIR, exist_ok=True)
    dest = os.path.join(STORE_DIR, os.path.basename(path))
    if os.path.abspath(dest) != os.path.abspath(path):
        shutil.copyfile(path, dest)
    return dest


def load(path: Optional[str], scale: float = 1.0) -> Optional[Wallpaper]:
    if not path or not os.path.isfile(path):
        return None
    try:
        return Wallpaper(path, scale)
    except (OSError, ValueError):
        return None
