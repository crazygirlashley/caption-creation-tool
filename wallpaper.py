"""App background image/GIF ("wallpaper") rendering — pure PIL, no Tk.

The app shows it behind the Editor's cards and the Caption Builder's
preview, either covering the area ("fit": scaled to fill, center-cropped)
or repeated as a pattern ("tile"), optionally dimmed toward the theme's
background color so text on top stays readable. Animated GIFs/WebPs keep
their frames and timing (capped — see MAX_FRAMES).
"""

import os
import shutil
from typing import Optional

from PIL import Image, ImageSequence

import app_paths

MODES = ("fit", "tile", "off")
# Frames kept from an animated background: a full-window frame costs a few
# MB as a Tk image, so long animations are thinned out (timing preserved).
MAX_FRAMES = 40
STORE_DIR = os.path.join(app_paths.BASE_DIR, "backgrounds")


class Wallpaper:
    def __init__(self, path: str) -> None:
        self.path = path
        frames, durations = [], []
        with Image.open(path) as img:
            for frm in ImageSequence.Iterator(img):
                frames.append(frm.convert("RGB"))
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
               dim_color: tuple = (0, 0, 0)) -> Image.Image:
        """Frame `index` laid out over an area of `size` (w, h)."""
        w, h = max(1, int(size[0])), max(1, int(size[1]))
        frame = self.frames[index % len(self.frames)]
        if mode == "tile":
            out = Image.new("RGB", (w, h))
            fw, fh = frame.size
            for y in range(0, h, fh):
                for x in range(0, w, fw):
                    out.paste(frame, (x, y))
        else:  # fit = cover the area, keep aspect, crop the overflow
            scale = max(w / frame.width, h / frame.height)
            rw, rh = max(1, round(frame.width * scale)), max(1, round(frame.height * scale))
            resized = frame.resize((rw, rh), Image.BILINEAR if self.animated else Image.LANCZOS)
            left, top = (rw - w) // 2, (rh - h) // 2
            out = resized.crop((left, top, left + w, top + h))
        if dim > 0:
            out = Image.blend(out, Image.new("RGB", out.size, dim_color), min(0.95, dim))
        return out


def store_copy(path: str) -> str:
    """Copy a chosen background into the app's own backgrounds/ folder so
    it keeps working if the original is moved or deleted. Returns the copy."""
    os.makedirs(STORE_DIR, exist_ok=True)
    dest = os.path.join(STORE_DIR, os.path.basename(path))
    if os.path.abspath(dest) != os.path.abspath(path):
        shutil.copyfile(path, dest)
    return dest


def load(path: Optional[str]) -> Optional[Wallpaper]:
    if not path or not os.path.isfile(path):
        return None
    try:
        return Wallpaper(path)
    except (OSError, ValueError):
        return None
