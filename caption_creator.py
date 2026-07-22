#!/usr/bin/env python3
"""Caption Creator — add styled caption panels beside images and GIFs."""

import functools
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
import traceback
from typing import Optional
import webbrowser

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence, ImageTk

import da_client

# ---------------------------------------------------------------------------
# Crash / hang logging
# ---------------------------------------------------------------------------

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "caption_creator_crash.log")

_handler = logging.handlers.RotatingFileHandler(
    _LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
log = logging.getLogger("caption_creator")
log.setLevel(logging.DEBUG)
log.addHandler(_handler)

# da_client logs the actual DeviantArt response body on API errors (e.g. why a
# submit got a 400), but that's only ever useful if it's captured somewhere
# persistent — attach the same rotating file handler here so it lands in
# caption_creator_crash.log regardless of whether the DA Log window is open.
_da_logger = logging.getLogger("da_client")
_da_logger.setLevel(logging.DEBUG)
_da_logger.addHandler(_handler)


def _excepthook(exc_type, exc_value, exc_tb):
    log.critical("CRASH — unhandled exception\n%s",
                 "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip())
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _excepthook


def _tk_excepthook(exc, val, tb):
    log.error("TKINTER CALLBACK ERROR\n%s",
              "".join(traceback.format_exception(exc, val, tb)).rstrip())


class _DALogHandler(logging.Handler):
    """Routes da_client log records to a Tkinter Text widget on the main thread."""

    _TAGS = {
        logging.ERROR:   "err",
        logging.WARNING: "warn",
        logging.INFO:    "info",
        logging.DEBUG:   "dbg",
    }

    def __init__(self, text_widget: "tk.Text") -> None:
        super().__init__()
        self._text = text_widget
        self.setFormatter(logging.Formatter(
            "%(asctime)s  [%(levelname)s]  %(message)s", datefmt="%H:%M:%S"
        ))

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        tag = self._TAGS.get(record.levelno, "dbg")
        def _append(m=msg, t=tag):
            try:
                self._text.config(state="normal")
                self._text.insert("end", m, t)
                self._text.see("end")
                self._text.config(state="disabled")
            except tk.TclError:
                pass
        self._text.after(0, _append)


class _Watchdog(threading.Thread):
    """Daemon thread: logs a warning if the Tk main thread stops processing for TIMEOUT seconds."""
    TIMEOUT = 8.0
    INTERVAL = 1.5

    def __init__(self):
        super().__init__(daemon=True, name="watchdog")
        self._last_pong = time.monotonic()
        self._stop_evt = threading.Event()
        self._hung = False

    def pong(self):
        self._last_pong = time.monotonic()
        if self._hung:
            gap = time.monotonic() - self._last_pong
            log.info("UI_RESPONSIVE — recovered after approx %.1fs", gap)
            self._hung = False

    def run(self):
        log.debug("Watchdog started (timeout=%.1fs)", self.TIMEOUT)
        while not self._stop_evt.wait(self.INTERVAL):
            gap = time.monotonic() - self._last_pong
            if gap >= self.TIMEOUT and not self._hung:
                log.warning("UI_HANG — main thread unresponsive for %.1fs", gap)
                self._hung = True

    def stop(self):
        self._stop_evt.set()

# ---------------------------------------------------------------------------
# Font helpers (Windows)
# ---------------------------------------------------------------------------

_FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts"),
]
_FONT_DIR = _FONT_DIRS[0]

_FONT_FILES = {
    "Arial": "arial.ttf",
    "Arial Bold": "arialbd.ttf",
    "Comic Sans MS": "comic.ttf",
    "Courier New": "cour.ttf",
    "Georgia": "georgia.ttf",
    "Impact": "impact.ttf",
    "Tahoma": "tahoma.ttf",
    "Times New Roman": "times.ttf",
    "Trebuchet MS": "trebuc.ttf",
    "Verdana": "verdana.ttf",
}

_FONT_BOLD_FILES = {
    "Arial":          "arialbd.ttf",
    "Georgia":        "georgiab.ttf",
    "Tahoma":         "tahomabd.ttf",
    "Times New Roman":"timesbd.ttf",
    "Trebuchet MS":   "trebucbd.ttf",
    "Verdana":        "verdanab.ttf",
}

_PILL_PRESETS = {
    "Pink":   ("#ffc0cb", "#dd2bbc"),   # (bg, stroke)
    "Blue":   ("#acdeff", "#1d61d1"),
    "Purple": ("#ae63f4", "#810381"),
}

_AARDVARK_NAME = "Aardvark Cafe"
_AARDVARK_DAFONT = "https://www.dafont.com/aardvark-cafe.font"

_FORMATS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formats")


def _load_formats() -> dict:
    """Scan formats/ dir and return {name: dict} for every valid format JSON."""
    result = {}
    if not os.path.isdir(_FORMATS_DIR):
        return result
    for fname in sorted(os.listdir(_FORMATS_DIR)):
        if not fname.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(_FORMATS_DIR, fname), encoding="utf-8") as fh:
                data = json.load(fh)
            name = data.get("name") or os.path.splitext(fname)[0]
            result[name] = data
        except Exception:
            log.warning("FORMAT_LOAD_ERROR  %s", fname)
    return result


@functools.lru_cache(maxsize=None)
def _find_font_file(stem: str) -> Optional[str]:
    """Return path to a font file whose name contains `stem` (case-insensitive).
    Cached — this walks every file in the Windows Fonts dir, which is slow enough
    to cause visible preview lag if repeated on every render/keystroke."""
    needle = stem.lower().replace(" ", "").replace("_", "")
    for font_dir in _FONT_DIRS:
        if not os.path.isdir(font_dir):
            continue
        for fname in os.listdir(font_dir):
            if fname.lower().endswith((".ttf", ".otf")):
                candidate = fname.lower().replace(" ", "").replace("_", "").replace("-", "")
                if needle in candidate:
                    return os.path.join(font_dir, fname)
    return None


def _check_aardvark() -> Optional[str]:
    # Font file ships as AARDC___.TTF (truncated 8.3 name)
    return _find_font_file("aardc")


@functools.lru_cache(maxsize=256)
def _pil_font(family: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Special case for Aardvark Cafe
    if family == _AARDVARK_NAME:
        path = _check_aardvark()
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    # Try bold variant first if requested
    fname = None
    if bold and family in _FONT_BOLD_FILES:
        fname = _FONT_BOLD_FILES[family]
    if fname is None:
        fname = _FONT_FILES.get(family, "")

    for font_dir in _FONT_DIRS:
        candidate = os.path.join(font_dir, fname)
        if os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                pass
    # Fuzzy fallback
    path = _find_font_file(family)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _font_list() -> list:
    fonts = list(_FONT_FILES.keys())
    if _check_aardvark():
        fonts.insert(0, _AARDVARK_NAME)
    return fonts


# ---------------------------------------------------------------------------
# Image compositing
# ---------------------------------------------------------------------------

_MIN_OUTPUT_W = 1280
_MIN_OUTPUT_H = 720

# Raw RGBA frame-data threshold above which we warn before loading a GIF/video.
# The app holds source frames AND a separately-rendered cache simultaneously
# (plus temporary RGB copies during save/upload), so actual peak usage runs
# several times higher than this raw estimate — kept conservative accordingly.
_LARGE_MEDIA_WARN_BYTES = 400 * 1024 * 1024


def _human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def _pad_to_macro_block(arr: np.ndarray, block: int = 16) -> np.ndarray:
    """Pad a HxWx3 array on the bottom/right (edge-replicated) so both
    dimensions are multiples of `block`. Caption widths/heights are
    arbitrary user-chosen integers, essentially never a multiple of 16, so
    without this imageio/ffmpeg silently stretches every MP4 frame to the
    nearest multiple — a real (if small) content distortion, not just a
    cosmetic warning."""
    h, w = arr.shape[:2]
    pad_h = (-h) % block
    pad_w = (-w) % block
    if pad_h == 0 and pad_w == 0:
        return arr
    return np.pad(arr, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")


def _hex_to_rgb(color: str) -> tuple:
    h = color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont, max_px: int,
                draw: ImageDraw.ImageDraw) -> list:
    lines = []
    for paragraph in (text.splitlines() or [""]):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            w = draw.textbbox((0, 0), trial, font=font)[2]
            if w <= max_px:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)
    return lines


def _fit_font_size(text: str, font_family: str, max_size: int,
                   area_w: int, area_h: int, padding: int,
                   stroke_width: int = 0, bold: bool = False) -> int:
    """Binary-search for the largest font size where wrapped text fits in area_w×area_h."""
    if not text.strip():
        return max_size
    tmp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lo, hi, best = 6, max_size, 6
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _pil_font(font_family, mid, bold)
        lines = _wrap_lines(text, font, area_w - padding * 2 - stroke_width * 2, tmp_draw)
        total_h = len(lines) * int(mid * 1.3) + padding * 2
        if total_h <= area_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _text_x(draw: ImageDraw.ImageDraw, line: str, font: ImageFont.FreeTypeFont,
             x: int, w: int, padding: int, stroke_width: int, align: str) -> int:
    """Compute the left edge for a line of text based on alignment."""
    inner_w = w - padding * 2
    line_w = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)[2]
    if align == "left":
        return x + padding
    if align == "right":
        return x + w - padding - line_w
    return x + padding + max(0, (inner_w - line_w) // 2)  # center


def _draw_text_block(draw: ImageDraw.ImageDraw,
                      x: int, y: int, w: int, h: int,
                      text: str, font_family: str, font_size: int,
                      font_color: str, padding: int,
                      stroke_width: int = 0, stroke_color: str = "#000000",
                      bold: bool = False,
                      shadow_draw: Optional[ImageDraw.ImageDraw] = None,
                      shadow_offset: tuple = (2, 3),
                      align: str = "center") -> None:
    font = _pil_font(font_family, font_size, bold)
    lines = _wrap_lines(text, font, w - padding * 2, draw)
    line_h = int(font_size * 1.3)
    block_h = len(lines) * line_h
    ty_start = y + max(padding, (h - block_h) // 2)
    fc = _hex_to_rgb(font_color)
    sc = _hex_to_rgb(stroke_color) if stroke_width > 0 else None
    ox, oy = shadow_offset
    if shadow_draw is not None:
        ty = ty_start
        for line in lines:
            tx = _text_x(draw, line, font, x, w, padding, stroke_width, align)
            shadow_draw.text((tx + ox, ty + oy), line, font=font,
                             fill=(0, 0, 0, 180), stroke_width=stroke_width,
                             stroke_fill=(0, 0, 0, 180))
            ty += line_h
    ty = ty_start
    for line in lines:
        tx = _text_x(draw, line, font, x, w, padding, stroke_width, align)
        draw.text((tx, ty), line, font=font, fill=fc,
                  stroke_width=stroke_width, stroke_fill=sc)
        ty += line_h


def _draw_text_overlay(draw: ImageDraw.ImageDraw,
                        pos: tuple, text: str, font: ImageFont.FreeTypeFont,
                        fill: str, stroke_width: int = 0, stroke_color: str = "#000000",
                        shadow_draw: Optional[ImageDraw.ImageDraw] = None,
                        shadow_offset: tuple = (2, 3)) -> None:
    """Draw a single text string at pos, optionally writing shadow to shadow_draw at offset."""
    fc = _hex_to_rgb(fill)
    sc = _hex_to_rgb(stroke_color) if stroke_width > 0 else None
    ox, oy = shadow_offset
    if shadow_draw is not None:
        shadow_draw.text((pos[0] + ox, pos[1] + oy), text, font=font,
                         fill=(0, 0, 0, 180), stroke_width=stroke_width,
                         stroke_fill=(0, 0, 0, 180))
    draw.text(pos, text, font=font, fill=fc, stroke_width=stroke_width, stroke_fill=sc)


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                    color: str, ax: int, ay: int, aw: int, ah: int,
                    padding: int = 0) -> None:
    if not text.strip():
        return
    inner_w = aw - padding * 2
    lines = _wrap_lines(text, font, inner_w, draw)
    line_h = draw.textbbox((0, 0), "Ag", font=font)[3] + 2
    block_h = len(lines) * line_h
    ty = ay + max(padding, (ah - block_h) // 2)
    fc = _hex_to_rgb(color)
    for line in lines:
        tw = draw.textbbox((0, 0), line, font=font)[2]
        tx = ax + padding + max(0, (inner_w - tw) // 2)
        draw.text((tx, ty), line, font=font, fill=fc)
        ty += line_h


def build_composite(
    frame: Image.Image,
    text: str,
    cap_width: int,
    page_bg: str,
    font_family: str,
    font_size: int,
    font_color: str,
    padding: int,
    stroke_width: int = 0,
    stroke_color: str = "#000000",
    bold: bool = False,
    shadow: bool = False,
    align: str = "center",
    fmt: str = "Standard",
    layout: str = "horizontal",
    header_text: str = "",
    header_font: str = "Arial Bold",
    header_size: int = 28,
    footer_enabled: bool = False,
    footer_text: str = "Fast Acting, Gender Swapping Pill",
    footer_font: str = "Arial",
    footer_size: int = 16,
    watermark_path: str = "",
    watermark_height: int = 60,
    output_size_pct: float = 0.0,
) -> Image.Image:
    frame = frame.convert("RGBA")
    fw, fh = frame.size

    # Panel geometry — cap_width acts as height in vertical layout
    if layout == "vertical":
        panel_x, panel_y, panel_w, panel_h = 0, fh, fw, cap_width
        total_w, total_h = fw, fh + cap_width
    else:
        panel_x, panel_y, panel_w, panel_h = fw, 0, cap_width, fh
        total_w, total_h = fw + cap_width, fh

    out = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 255))
    draw = ImageDraw.Draw(out)

    out.paste(frame, (0, 0), frame)
    out.paste(Image.new("RGBA", (panel_w, panel_h), _hex_to_rgb(page_bg) + (255,)),
              (panel_x, panel_y))

    # When shadow is on, accumulate all text into two layers then composite once.
    # This cuts GaussianBlur calls from N-per-text-element down to one per frame.
    if shadow:
        txt_img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        txt_draw = ImageDraw.Draw(txt_img)
        shd_img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        shd_draw_ctx: Optional[ImageDraw.ImageDraw] = ImageDraw.Draw(shd_img)
    else:
        txt_img = None
        txt_draw = draw
        shd_draw_ctx = None

    _draw_text_block(txt_draw, panel_x, panel_y, panel_w, panel_h,
                     text, font_family, font_size, font_color, padding,
                     stroke_width, stroke_color, bold, shd_draw_ctx,
                     align=align)

    # Watermark — bottom-left of image; track right edge for footer placement
    wm_right = 8
    if watermark_path and os.path.exists(watermark_path):
        try:
            wm = Image.open(watermark_path).convert("RGBA")
            scale = watermark_height / wm.height
            wm = wm.resize((max(1, int(wm.width * scale)), watermark_height), Image.LANCZOS)
            wy = fh - watermark_height - 8
            if wy >= 0:
                out.alpha_composite(wm, (8, wy))
                wm_right = 8 + wm.width + 6
        except Exception:
            pass

    # Footer text — overlaid on bottom-left of image, to the right of watermark
    if footer_enabled and footer_text.strip():
        fnt = _pil_font(footer_font, footer_size)
        avail_w = fw - wm_right - 8
        if avail_w > 20:
            lines = _wrap_lines(footer_text.strip(), fnt, avail_w, txt_draw)
            line_h = txt_draw.textbbox((0, 0), "Ag", font=fnt)[3] + 2
            block_h = len(lines) * line_h
            fx = wm_right
            fy = fh - block_h - 8
            if fy >= 0:
                ty = fy
                for line in lines:
                    _draw_text_overlay(txt_draw, (fx, ty), line, fnt, font_color,
                                       stroke_width, stroke_color, shd_draw_ctx)
                    ty += line_h

    # Title text overlaid at top-left corner of image
    if header_text.strip():
        fnt = _pil_font(header_font, header_size)
        _draw_text_overlay(txt_draw, (8, 8), header_text.strip(), fnt,
                           font_color, stroke_width, stroke_color, shd_draw_ctx)

    # Composite shadow (single blur) then actual text on top
    if shadow and txt_img is not None and shd_img is not None:
        out.alpha_composite(shd_img.filter(ImageFilter.GaussianBlur(3)))
        out.alpha_composite(txt_img)

    # Enforce a minimum final output size — upscale (never downscale by
    # default) preserving aspect ratio, so small sources don't distort when
    # stretched to fit both axes. output_size_pct (0-100) then slides between
    # that same 1280x720-touching scale (0%) and today's default — natural
    # size, or the 1280x720 floor if natural is smaller (100%). For sources
    # already bigger than the floor, this lets 0% genuinely downscale (for a
    # smaller file) without stretching, since both ends are uniform scales of
    # the same natural aspect ratio; for sources smaller than the floor, the
    # two ends coincide (there's no room to shrink below the hard floor).
    floor_scale = max(_MIN_OUTPUT_W / total_w, _MIN_OUTPUT_H / total_h)
    default_scale = max(1.0, floor_scale)
    pct = max(0.0, min(100.0, output_size_pct)) / 100.0
    scale = floor_scale + (default_scale - floor_scale) * pct
    new_w = max(_MIN_OUTPUT_W, round(total_w * scale))
    new_h = max(_MIN_OUTPUT_H, round(total_h * scale))
    if (new_w, new_h) != (total_w, total_h):
        out = out.resize((new_w, new_h), Image.LANCZOS)

    return out


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class CaptionApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Caption Creator")
        self.root.minsize(800, 500)

        log.info("APP_START  python=%s  platform=%s", sys.version.split()[0], sys.platform)
        root.report_callback_exception = _tk_excepthook
        self._watchdog = _Watchdog()
        self._watchdog.start()
        self._ping_watchdog()

        self._frames: list = []
        self._durations: list = []
        self._cache: list = []
        self._is_anim: bool = False
        self._is_video: bool = False
        self._video_fps: Optional[float] = None
        # When set, self._frames holds only a single placeholder frame standing
        # in for a large GIF/video whose full decode was deferred until an
        # actual Save/Send-to-DA (see _materialize_deferred_source).
        self._deferred_path: Optional[str] = None
        self._deferred_kind: Optional[str] = None
        self._deferred_total_frames: Optional[int] = None
        self._source_path: Optional[str] = None
        self._single_frame_var = tk.BooleanVar(value=False)
        self._anim_id: Optional[str] = None
        self._anim_idx: int = 0
        self._tk_img = None
        self._preview_pil_img: Optional[Image.Image] = None
        self._color_pick_target: Optional[str] = None

        # Background and text (shared globally)
        self._page_bg_color = "#f5f5f5"
        self._fc_color = "#111111"
        self._stroke_color = "#000000"
        available_fonts = _font_list()
        self._font_var = tk.StringVar(value=available_fonts[0] if available_fonts else "Arial")
        self._size_var = tk.IntVar(value=72)
        self._width_var = tk.IntVar(value=320)
        self._dynamic_width_var = tk.BooleanVar(value=True)
        # 100% = today's default output size (natural size, or the enforced
        # 1280x720 floor if natural is smaller); 0% shrinks it — aspect ratio
        # preserved, no stretching — down to that same 1280x720 floor, which
        # is only a real downscale when the natural size exceeds it. See
        # build_composite()'s output_size_pct handling.
        self._output_override_var = tk.BooleanVar(value=False)
        self._output_pct_var = tk.IntVar(value=100)
        self._pad_var = tk.IntVar(value=20)
        self._stroke_width_var = tk.IntVar(value=0)
        self._auto_size_var = tk.BooleanVar(value=True)
        self._bold_var = tk.BooleanVar(value=False)
        self._align_var = tk.StringVar(value="center")

        # Formats (loaded from formats/ dir)
        self._formats: dict = _load_formats()
        default_fmt = "Standard" if "Standard" in self._formats else (
            next(iter(self._formats), "Standard"))
        self._format_var = tk.StringVar(value=default_fmt)

        # Header
        self._header_font_var = tk.StringVar(value="Arial")
        self._header_size_var = tk.IntVar(value=28)

        # Footer overlay
        self._footer_enabled = tk.BooleanVar(value=False)
        self._footer_font_var = tk.StringVar(value="Arial")
        self._footer_size_var = tk.IntVar(value=16)

        # Watermark
        self._watermark_path = ""
        # Only used when the footer is disabled — with the footer on,
        # watermark height stays tied to 2x footer size as before.
        self._watermark_height_var = tk.IntVar(value=120)

        # Warn if Aardvark Cafe not installed (deferred to after window shows)
        self._warn_aardvark = not bool(_check_aardvark())

        # Async render state
        self._refresh_job: Optional[str] = None
        self._build_cancel = threading.Event()
        self._cache_complete: bool = True

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._auto_detect_watermark()
        self.root.after(100, self._da_update_status)
        if self._warn_aardvark:
            self.root.after(500, self._prompt_aardvark)

    def _ping_watchdog(self) -> None:
        self._watchdog.pong()
        self.root.after(1000, self._ping_watchdog)

    def _on_close(self) -> None:
        log.info("APP_CLOSE")
        self._build_cancel.set()
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._watchdog.stop()
        self.root.destroy()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ---- Toolbar (top) ----
        bar = ttk.Frame(self.root, padding=(6, 5))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="Open Image / GIF / MP4…", command=self._open).pack(side="left", padx=4)
        ttk.Button(bar, text="Save…", command=self._save).pack(side="left", padx=4)
        # Only shown for animated sources (GIF/MP4) — hidden for static images.
        self._single_frame_check = ttk.Checkbutton(
            bar, text="Single-Frame Preview", variable=self._single_frame_var,
            command=self._on_single_frame_toggle)
        self._toolbar_sep = ttk.Separator(bar, orient="vertical")
        self._toolbar_sep.pack(side="left", fill="y", padx=6, pady=4)
        # DA Login is hidden once _da_update_status confirms a cached login token;
        # Send to DA and Log Out are only shown once that same check passes.
        self._da_login_btn = ttk.Button(bar, text="DA Login", command=self._da_login)
        self._da_login_btn.pack(side="left", padx=2)
        self._da_send_btn = ttk.Button(bar, text="Send to DA…", command=self._da_send)
        self._da_logout_btn = ttk.Button(bar, text="Log Out", command=self._da_logout)
        self._da_settings_btn = ttk.Button(bar, text="DA Settings", command=self._da_settings)
        self._da_settings_btn.pack(side="left", padx=2)

        # ---- Status bar (bottom) — DA login state and current file info ----
        status_bar = ttk.Frame(self.root, padding=(6, 3))
        status_bar.pack(side="bottom", fill="x")
        self._da_status_label = ttk.Label(status_bar, text="DA: not logged in", foreground="#999")
        self._da_status_label.pack(side="left", padx=(0, 2))
        self._status = ttk.Label(status_bar, text="No file loaded", foreground="#888")
        self._status.pack(side="left", padx=12)

        # ---- Right control panel (fixed width, packed before preview) ----
        right = tk.Frame(self.root, width=320, bg=self.root.cget("bg"))
        right.pack(side="right", fill="y", padx=(0, 4), pady=(0, 4))
        right.pack_propagate(False)

        # ---- Preview canvas fills remaining left space ----
        pf = ttk.LabelFrame(self.root, text="Preview", padding=4)
        pf.pack(side="left", fill="both", expand=True, padx=(4, 2), pady=(0, 4))
        self._preview_frame = pf

        ttk.Checkbutton(pf, text="Output Size Override", variable=self._output_override_var,
                        command=self._on_output_override_toggle).pack(side="top", anchor="w")
        self._output_pct_row = ttk.Frame(pf)
        self._output_pct_row.pack(side="top", fill="x", pady=(0, 4))
        ttk.Scale(self._output_pct_row, from_=0, to=100, orient="horizontal",
                  variable=self._output_pct_var).pack(side="left", fill="x", expand=True)
        self._output_pct_label = ttk.Label(self._output_pct_row, text="100%", width=5)
        self._output_pct_label.pack(side="left", padx=(6, 0))
        self._output_pct_row.pack_forget()  # hidden until the toggle above is checked

        self._canvas = tk.Canvas(pf, bg="#2b2b2b", highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda _: self._redraw())

        # Format selector + Reload + Apply
        fmt_bar = tk.Frame(right, bg=right.cget("bg"))
        fmt_bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(fmt_bar, text="Format:").pack(side="left")
        self._fmt_combo = ttk.Combobox(fmt_bar, textvariable=self._format_var,
                                       values=list(self._formats.keys()),
                                       width=12, state="readonly")
        self._fmt_combo.pack(side="left", padx=6)
        ttk.Button(fmt_bar, text="Apply",
                   command=lambda: self._safe_refresh(debounce_ms=0)).pack(side="right")
        ttk.Button(fmt_bar, text="↺", width=2,
                   command=self._reload_formats).pack(side="right", padx=(0, 2))

        # Pill preset bar — shown only for X-Change
        self._pill_frame = tk.Frame(right, bg=right.cget("bg"))
        ttk.Label(self._pill_frame, text="Pill:").pack(side="left", padx=(8, 4))
        for name, (bg, fc) in _PILL_PRESETS.items():
            tk.Button(self._pill_frame, text=name, bg=bg, fg=fc, relief="raised", width=7,
                      command=lambda b=bg, fc2=fc: self._apply_preset(b, fc2)).pack(
                side="left", padx=2)
        # hidden by default (Standard format); shown in _on_format_change

        # Notebook tabs
        self._nb = nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True, padx=4, pady=4)

        # Tab 1: Caption
        cap_tab = ttk.Frame(nb, padding=10)
        nb.add(cap_tab, text="Caption")
        self._build_caption_tab(cap_tab)

        # Tab 2: X-Change Header
        xc_tab = ttk.Frame(nb, padding=10)
        nb.add(xc_tab, text="Header")
        self._build_xchange_tab(xc_tab)

        # Tab 3: Footer
        ftr_tab = ttk.Frame(nb, padding=10)
        nb.add(ftr_tab, text="Footer")
        self._build_footer_tab(ftr_tab)

        # Tab 4: Watermark
        wm_tab = ttk.Frame(nb, padding=10)
        nb.add(wm_tab, text="Watermark")
        self._build_watermark_tab(wm_tab)

        # Traces
        for v in (self._font_var, self._size_var, self._width_var, self._pad_var,
                  self._stroke_width_var, self._auto_size_var, self._bold_var,
                  self._align_var, self._header_font_var, self._header_size_var,
                  self._footer_font_var, self._footer_size_var,
                  self._watermark_height_var, self._output_pct_var):
            v.trace_add("write", lambda *_: self._safe_refresh())
        self._output_pct_var.trace_add(
            "write", lambda *_: self._output_pct_label.config(text=f"{self._output_pct_var.get()}%"))
        self._format_var.trace_add("write", lambda *_: self._on_format_change())

    def _row(self, parent, r, label, widget, pady=4):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=pady)
        widget.grid(row=r, column=1, sticky="ew", padx=(6, 0), pady=pady)

    def _build_caption_tab(self, f: ttk.Frame) -> None:
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Caption Text:").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self._text_box = tk.Text(f, width=22, height=8, wrap="word",
                                 relief="solid", bd=1, padx=4, pady=4)
        self._text_box.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        self._text_box.insert("1.0", "Your caption here")
        self._text_box.bind("<KeyRelease>", lambda _: self._safe_refresh())
        r += 1

        ttk.Label(f, text="Page BG Color:").grid(row=r, column=0, sticky="w", pady=4)
        bg_row = ttk.Frame(f)
        bg_row.grid(row=r, column=1, sticky="w", padx=6)
        self._bg_btn = tk.Button(bg_row, bg=self._page_bg_color, width=5, relief="groove",
                                 command=self._pick_bg)
        self._bg_btn.pack(side="left")
        ttk.Button(bg_row, text="Pick", width=5,
                   command=lambda: self._start_color_pick("bg")).pack(side="left", padx=(4, 0))
        r += 1

        ttk.Label(f, text="Font:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self._font_var, values=_font_list(),
                     width=16, state="readonly").grid(row=r, column=1, sticky="ew", padx=6)
        r += 1

        auto_row = ttk.Frame(f)
        auto_row.grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(auto_row, text="Auto-fit", variable=self._auto_size_var,
                        command=self._safe_refresh).pack(side="left")
        ttk.Checkbutton(auto_row, text="Bold", variable=self._bold_var,
                        command=self._safe_refresh).pack(side="left", padx=(12, 0))
        r += 1

        ttk.Label(f, text="Alignment:").grid(row=r, column=0, sticky="w", pady=4)
        align_row = ttk.Frame(f)
        align_row.grid(row=r, column=1, sticky="w", padx=6)
        for label, value in (("←", "left"), ("↔", "center"), ("→", "right")):
            ttk.Radiobutton(align_row, text=label, variable=self._align_var,
                            value=value).pack(side="left", padx=2)
        r += 1

        ttk.Label(f, text="Max Font Size:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=8, to=200, textvariable=self._size_var,
                    width=7).grid(row=r, column=1, sticky="w", padx=6)
        r += 1

        ttk.Label(f, text="Text Color:").grid(row=r, column=0, sticky="w", pady=4)
        fc_row = ttk.Frame(f)
        fc_row.grid(row=r, column=1, sticky="w", padx=6)
        self._fc_btn = tk.Button(fc_row, bg=self._fc_color, width=5, relief="groove",
                                 command=self._pick_fc)
        self._fc_btn.pack(side="left")
        ttk.Button(fc_row, text="Pick", width=5,
                   command=lambda: self._start_color_pick("fc")).pack(side="left", padx=(4, 0))
        r += 1

        self._cap_size_label = ttk.Label(f, text="Caption Width:")
        self._cap_size_label.grid(row=r, column=0, sticky="w", pady=4)
        self._width_spin = ttk.Spinbox(f, from_=80, to=1200, textvariable=self._width_var,
                                       width=7)
        self._width_spin.grid(row=r, column=1, sticky="w", padx=6)
        self._width_spin.config(state="disabled" if self._dynamic_width_var.get() else "normal")
        r += 1

        ttk.Checkbutton(f, text="Dynamic Width (1.25× image width)",
                        variable=self._dynamic_width_var,
                        command=self._on_dynamic_width_toggle).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 4))
        r += 1

        ttk.Label(f, text="Padding:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=0, to=120, textvariable=self._pad_var,
                    width=7).grid(row=r, column=1, sticky="w", padx=6)
        r += 1

        ttk.Label(f, text="Stroke Width:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=0, to=20, textvariable=self._stroke_width_var,
                    width=7).grid(row=r, column=1, sticky="w", padx=6)
        r += 1

        ttk.Label(f, text="Stroke Color:").grid(row=r, column=0, sticky="w", pady=4)
        stroke_row = ttk.Frame(f)
        stroke_row.grid(row=r, column=1, sticky="w", padx=6)
        self._stroke_btn = tk.Button(stroke_row, bg=self._stroke_color, width=5, relief="groove",
                                     command=self._pick_stroke)
        self._stroke_btn.pack(side="left")
        ttk.Button(stroke_row, text="Pick", width=5,
                   command=lambda: self._start_color_pick("stroke")).pack(side="left", padx=(4, 0))

    def _build_xchange_tab(self, f: ttk.Frame) -> None:
        f.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(f, text="Title Text:").grid(row=r, column=0, sticky="nw", pady=4)
        self._header_text_box = tk.Text(f, width=18, height=3, wrap="word",
                                        relief="solid", bd=1, padx=3, pady=3)
        self._header_text_box.grid(row=r, column=1, sticky="ew", padx=6, pady=(2, 8))
        self._header_text_box.insert("1.0", "")
        self._header_text_box.bind("<KeyRelease>", lambda _: self._safe_refresh())
        r += 1

        ttk.Label(f, text="Header Font:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self._header_font_var, values=_font_list(),
                     width=16, state="readonly").grid(row=r, column=1, sticky="ew", padx=6)
        r += 1

        ttk.Label(f, text="Header Size:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=8, to=144, textvariable=self._header_size_var,
                    width=7).grid(row=r, column=1, sticky="w", padx=6)
        r += 1


    def _build_footer_tab(self, f: ttk.Frame) -> None:
        f.columnconfigure(1, weight=1)
        r = 0

        ttk.Checkbutton(f, text="Enable Footer", variable=self._footer_enabled,
                        command=self._on_footer_toggle).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 8))
        r += 1

        ttk.Label(f, text="Footer Text:").grid(row=r, column=0, sticky="nw", pady=4)
        self._footer_text_box = tk.Text(f, width=18, height=3, wrap="word",
                                        relief="solid", bd=1, padx=3, pady=3)
        self._footer_text_box.grid(row=r, column=1, sticky="ew", padx=6, pady=(2, 8))
        self._footer_text_box.insert("1.0", "")
        self._footer_text_box.bind("<KeyRelease>", lambda _: self._safe_refresh())
        r += 1

        ttk.Label(f, text="Footer Font:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self._footer_font_var, values=_font_list(),
                     width=16, state="readonly").grid(row=r, column=1, sticky="ew", padx=6)
        r += 1

        ttk.Label(f, text="Footer Size:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=8, to=72, textvariable=self._footer_size_var,
                    width=7).grid(row=r, column=1, sticky="w", padx=6)
        r += 1


    def _build_watermark_tab(self, f: ttk.Frame) -> None:
        f.columnconfigure(1, weight=1)
        r = 0

        ttk.Button(f, text="Load Watermark Image…",
                   command=self._load_watermark).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 6))
        r += 1

        self._wm_label = ttk.Label(f, text="None", foreground="#888", wraplength=220)
        self._wm_label.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 6))
        r += 1

        ttk.Button(f, text="Clear", command=self._clear_watermark).grid(
            row=r, column=0, sticky="w", pady=(0, 10))
        r += 1

        # Only relevant when the footer is off — with the footer on, watermark
        # height stays tied to 2x footer size. Visibility kept in sync by
        # _update_watermark_height_visibility() (called from _on_footer_toggle
        # and _on_format_change).
        self._wm_height_label = ttk.Label(f, text="Watermark Height (px):")
        self._wm_height_label.grid(row=r, column=0, sticky="w", pady=(6, 4))
        self._wm_height_spin = ttk.Spinbox(f, from_=10, to=2000,
                                           textvariable=self._watermark_height_var, width=7)
        self._wm_height_spin.grid(row=r, column=1, sticky="w", padx=6, pady=(6, 4))
        r += 1


    # ------------------------------------------------------------------
    # Aardvark Cafe prompt
    # ------------------------------------------------------------------

    def _prompt_aardvark(self) -> None:
        answer = messagebox.askyesno(
            "Font Recommended",
            "The 'Aardvark Cafe' font is recommended for the X-Change header "
            "but is not installed.\n\nWould you like to open its download page on DaFont?",
        )
        if answer:
            webbrowser.open(_AARDVARK_DAFONT)

    # ------------------------------------------------------------------
    # Color pickers
    # ------------------------------------------------------------------

    def _pick_bg(self) -> None:
        r = colorchooser.askcolor(color=self._page_bg_color, title="Page Background Color")
        if r[1]:
            self._page_bg_color = r[1]; self._bg_btn.config(bg=r[1]); self._safe_refresh()

    def _pick_fc(self) -> None:
        r = colorchooser.askcolor(color=self._fc_color, title="Caption Text Color")
        if r[1]:
            self._fc_color = r[1]; self._fc_btn.config(bg=r[1]); self._safe_refresh()

    def _pick_stroke(self) -> None:
        r = colorchooser.askcolor(color=self._stroke_color, title="Stroke Color")
        if r[1]:
            self._stroke_color = r[1]; self._stroke_btn.config(bg=r[1]); self._safe_refresh()

    # ------------------------------------------------------------------
    # Eyedropper — sample a color directly from the preview image
    # ------------------------------------------------------------------

    def _start_color_pick(self, target: str) -> None:
        """Enter eyedropper mode: the next click on the preview samples a
        pixel color and applies it to `target` ('bg', 'fc', or 'stroke')."""
        if not self._cache:
            messagebox.showinfo("Nothing to Sample", "Open an image, GIF, or video first.")
            return
        self._color_pick_target = target
        self._canvas.config(cursor="crosshair")
        self._canvas.bind("<Button-1>", self._on_color_pick_click)
        self.root.bind("<Escape>", self._cancel_color_pick)

    def _cancel_color_pick(self, event=None) -> None:
        self._color_pick_target = None
        self._canvas.config(cursor="")
        self._canvas.unbind("<Button-1>")
        self.root.unbind("<Escape>")

    def _on_color_pick_click(self, event) -> None:
        target = self._color_pick_target
        img = self._preview_pil_img
        self._cancel_color_pick()
        if target is None or img is None:
            return
        cw = max(self._canvas.winfo_width(), 10)
        ch = max(self._canvas.winfo_height(), 10)
        left = (cw - img.width) // 2
        top = (ch - img.height) // 2
        px, py = event.x - left, event.y - top
        if not (0 <= px < img.width and 0 <= py < img.height):
            return  # clicked outside the image itself (canvas letterboxing)
        r, g, b = img.convert("RGB").getpixel((px, py))
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        if target == "bg":
            self._page_bg_color = hex_color
            self._bg_btn.config(bg=hex_color)
        elif target == "fc":
            self._fc_color = hex_color
            self._fc_btn.config(bg=hex_color)
        elif target == "stroke":
            self._stroke_color = hex_color
            self._stroke_btn.config(bg=hex_color)
        else:
            return
        self._safe_refresh()

    def _on_dynamic_width_toggle(self) -> None:
        self._width_spin.config(state="disabled" if self._dynamic_width_var.get() else "normal")
        self._apply_dynamic_width()

    def _on_output_override_toggle(self) -> None:
        if self._output_override_var.get():
            self._output_pct_row.pack(side="top", fill="x", pady=(0, 4), before=self._canvas)
        else:
            self._output_pct_row.pack_forget()
        self._safe_refresh()

    def _apply_dynamic_width(self) -> None:
        """When Dynamic Width is enabled, override cap width with 1.25x the image width."""
        if not self._dynamic_width_var.get() or not self._frames:
            return
        fw = self._frames[0].size[0]
        self._width_var.set(int(round(fw * 1.25)))

    def _on_footer_toggle(self) -> None:
        self._update_watermark_height_visibility()
        self._safe_refresh()

    def _update_watermark_height_visibility(self) -> None:
        """The manual watermark height control only matters when the footer
        is disabled — with the footer on, height stays tied to 2x footer size."""
        if self._footer_enabled.get():
            self._wm_height_label.grid_remove()
            self._wm_height_spin.grid_remove()
        else:
            self._wm_height_label.grid()
            self._wm_height_spin.grid()

    def _apply_preset(self, bg: str, stroke: str) -> None:
        self._page_bg_color = bg
        self._stroke_color = stroke
        self._bg_btn.config(bg=bg)
        self._stroke_btn.config(bg=stroke)
        self._safe_refresh(debounce_ms=0)

    # ------------------------------------------------------------------
    # Watermark
    # ------------------------------------------------------------------

    _WM_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
    _WM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watermark")

    def _auto_detect_watermark(self) -> None:
        """Load the watermark folder's sole image automatically; disable if 0 or 2+."""
        if not os.path.isdir(self._WM_DIR):
            return
        images = [f for f in os.listdir(self._WM_DIR)
                  if os.path.splitext(f)[1].lower() in self._WM_EXTS]
        if len(images) == 1:
            path = os.path.join(self._WM_DIR, images[0])
            self._watermark_path = path
            self._wm_label.config(text=images[0], foreground="#000")
            log.info("WATERMARK_AUTO  %s", images[0])
        else:
            log.info("WATERMARK_AUTO_SKIP  count=%d", len(images))

    def _load_watermark(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Watermark Image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
        )
        if path:
            self._watermark_path = path
            self._wm_label.config(text=os.path.basename(path), foreground="#000")
            self._safe_refresh(debounce_ms=0)

    def _clear_watermark(self) -> None:
        self._watermark_path = ""
        self._wm_label.config(text="None", foreground="#888")
        self._safe_refresh(debounce_ms=0)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _large_media_choice(self, frame_count: int, width: int, height: int) -> str:
        """For media whose raw frame data would use a lot of memory, ask how to
        proceed. Returns "full" (load every frame now), "placeholder" (load
        just one frame now, defer the rest to Save/Send-to-DA time), or
        "cancel". Returns "full" immediately, with no dialog, if the estimated
        size is below the warning threshold or can't be estimated."""
        if frame_count <= 0 or width <= 0 or height <= 0:
            return "full"
        raw_bytes = frame_count * width * height * 4
        if raw_bytes <= _LARGE_MEDIA_WARN_BYTES:
            return "full"

        dlg = tk.Toplevel(self.root)
        dlg.title("Large File")
        dlg.resizable(False, False)
        dlg.grab_set()

        msg = (
            f"This file has {frame_count} frames at {width}×{height}px — raw frame "
            f"data alone is about {_human_size(raw_bytes)}, and loading every frame "
            "now needs several times that to hold both the source and the "
            "captioned output in memory at once. On a machine with limited RAM "
            "this can hang or crash the app."
        )
        ttk.Label(dlg, text=msg, wraplength=360, justify="left",
                 padding=(16, 16, 16, 8)).pack()

        result: list = ["cancel"]

        def _choose(choice):
            result[0] = choice
            dlg.destroy()

        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        ttk.Button(btn_row, text="Use Single-Frame Preview",
                   command=lambda: _choose("placeholder")).pack(fill="x", pady=(0, 4))
        ttk.Label(btn_row, text="(shows one frame now; processes all frames when you "
                               "Save or Send to DA)",
                 foreground="#666", wraplength=340, justify="left",
                 font=("TkDefaultFont", 8)).pack(fill="x", pady=(0, 8))
        ttk.Button(btn_row, text="Load Full File Now",
                   command=lambda: _choose("full")).pack(fill="x", pady=(0, 4))
        ttk.Button(btn_row, text="Cancel",
                   command=lambda: _choose("cancel")).pack(fill="x")

        dlg.protocol("WM_DELETE_WINDOW", lambda: _choose("cancel"))
        self.root.wait_window(dlg)
        return result[0]

    def _load_gif_frames(self, path: str, force_full: bool = False) -> bool:
        """Decode a GIF/animated image into self._frames/_durations.
        Returns False (leaving self._frames empty) on cancellation. Sets
        self._deferred_path/_deferred_kind if the user picks the placeholder
        option instead of a full load."""
        img = Image.open(path)
        n_frames = getattr(img, "n_frames", 1)
        w, h = img.size

        choice = "full" if force_full else self._large_media_choice(n_frames, w, h)
        if choice == "cancel":
            return False

        if choice == "placeholder":
            first = next(iter(ImageSequence.Iterator(img)))
            self._frames.append(first.copy().convert("RGBA"))
            self._durations.append(first.info.get("duration", 100))
            self._deferred_path = path
            self._deferred_kind = "gif"
            self._deferred_total_frames = n_frames
            return True

        for frm in ImageSequence.Iterator(img):
            self._frames.append(frm.copy().convert("RGBA"))
            self._durations.append(frm.info.get("duration", 100))
        return True

    def _load_video_frames(self, path: str, force_full: bool = False) -> bool:
        """Decode an MP4 into self._frames/_durations via the imageio/ffmpeg backend.
        Returns False (leaving self._frames empty) on failure or user cancellation.
        Sets self._deferred_path/_deferred_kind if the user picks the
        placeholder option instead of a full load."""
        try:
            reader = imageio.get_reader(path, "ffmpeg")
            meta = reader.get_meta_data()
        except Exception:
            log.exception("VIDEO_OPEN_ERROR")
            messagebox.showerror("Open Error",
                                  "Failed to open video file — is the codec supported?")
            return False

        fps = meta.get("fps") or 24.0
        self._video_fps = fps
        duration_s = meta.get("duration")
        vid_w, vid_h = meta.get("size", (0, 0))
        choice = "full"
        if not force_full and duration_s:
            est_frames = int(duration_s * fps)
            choice = self._large_media_choice(est_frames, vid_w, vid_h)
            if choice == "cancel":
                reader.close()
                return False

        duration_ms = max(1, round(1000 / fps))
        try:
            if choice == "placeholder":
                first = reader.get_data(0)
                self._frames.append(Image.fromarray(first).convert("RGBA"))
                self._durations.append(duration_ms)
                self._deferred_path = path
                self._deferred_kind = "mp4"
                self._deferred_total_frames = est_frames
            else:
                for frame in reader:
                    self._frames.append(Image.fromarray(frame).convert("RGBA"))
                    self._durations.append(duration_ms)
        except Exception:
            log.exception("VIDEO_DECODE_ERROR")
            messagebox.showerror("Open Error", "Failed to decode video frames.")
            reader.close()
            return False
        reader.close()

        if not self._frames:
            messagebox.showerror("Open Error", "Video contained no readable frames.")
            return False
        return True

    def _materialize_deferred_source(self) -> bool:
        """If the current file is a single-frame placeholder standing in for a
        deferred large GIF/video, fully decode all its frames now. Returns
        True immediately if there's nothing deferred. Never re-prompts —
        the user already chose to defer this cost to save/send time."""
        if not self._deferred_path:
            return True
        path, kind = self._deferred_path, self._deferred_kind
        self._frames.clear()
        self._durations.clear()
        old_txt = self._status.cget("text")
        self._status.config(text=old_txt + " [Loading all frames…]")
        self.root.update_idletasks()
        try:
            if kind == "mp4":
                ok = self._load_video_frames(path, force_full=True)
            else:
                ok = self._load_gif_frames(path, force_full=True)
        except Exception:
            log.exception("MATERIALIZE_DEFERRED_ERROR")
            ok = False
        finally:
            self._status.config(
                text=self._status.cget("text").replace(" [Loading all frames…]", ""))
        if ok:
            self._deferred_path = None
            self._deferred_kind = None
            self._deferred_total_frames = None
            # Frames are all in memory now regardless of how we got here (an
            # explicit toggle-off or an automatic Save/Send-to-DA materialize)
            # — keep the checkbox in sync either way.
            self._single_frame_var.set(False)
        else:
            messagebox.showerror("Load Error", "Failed to load the full file for export.")
        return ok

    def _update_single_frame_visibility(self) -> None:
        """The Single-Frame Preview toggle only makes sense for animated sources.
        Uses winfo_manager() rather than winfo_ismapped() — the latter reflects
        actual on-screen mapping, which can read False even when packed (e.g.
        before the window is first drawn), causing a spurious re-pack/no-op."""
        packed = self._single_frame_check.winfo_manager() == "pack"
        if self._is_anim:
            if not packed:
                self._single_frame_check.pack(side="left", padx=4, before=self._toolbar_sep)
        else:
            if packed:
                self._single_frame_check.pack_forget()

    def _on_single_frame_toggle(self) -> None:
        """Manually collapse the loaded animation down to one preview frame
        (freeing memory while editing), or restore the full frame set."""
        if self._single_frame_var.get():
            if self._deferred_path or not self._source_path or len(self._frames) <= 1:
                return
            total = len(self._frames)
            first, first_duration = self._frames[0], self._durations[0]
            self._frames[:] = [first]
            self._durations[:] = [first_duration]
            self._deferred_path = self._source_path
            self._deferred_kind = "mp4" if self._is_video else "gif"
            self._deferred_total_frames = total
            self._cache_complete = False
            self._stop_anim()
            self._anim_idx = 0
            self._update_preview_label()
            log.info("SINGLE_FRAME_MODE_ON  total_frames=%d", total)
            self._safe_refresh(debounce_ms=0)
        else:
            if not self._deferred_path:
                return
            if self._materialize_deferred_source():
                log.info("SINGLE_FRAME_MODE_OFF  frames=%d", len(self._frames))
                self._anim_idx = 0
                self._safe_refresh(debounce_ms=0)
            else:
                self._single_frame_var.set(True)  # materialize failed — stay collapsed

    def _open(self) -> None:
        path = filedialog.askopenfilename(
            title="Open Image, GIF, or Video",
            filetypes=[
                ("Images, GIFs & Video", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.mp4"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        self._stop_anim()
        self._build_cancel.set()
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        self._build_cancel = threading.Event()
        self._frames.clear()
        self._durations.clear()
        self._cache.clear()
        self._update_preview_label()
        self._video_fps = None
        self._deferred_path = None
        self._deferred_kind = None
        self._deferred_total_frames = None

        ext = os.path.splitext(path)[1].lower()
        self._is_video = ext == ".mp4"

        if self._is_video:
            if not self._load_video_frames(path):
                self._is_video = False
                self._status.config(text="No file loaded")
                return
            # In placeholder mode self._frames has only 1 entry regardless of the
            # real total, so use the deferred total (if any) to decide animation.
            total_frames = self._deferred_total_frames if self._deferred_path else len(self._frames)
            self._is_anim = total_frames > 1
        else:
            img = Image.open(path)
            is_anim = getattr(img, "is_animated", False)
            self._is_anim = is_anim or ext == ".gif"

            if self._is_anim:
                if not self._load_gif_frames(path):
                    self._status.config(text="No file loaded")
                    return
            else:
                self._frames.append(img.convert("RGBA"))
                self._durations.append(0)

        name = os.path.basename(path)
        if self._deferred_path:
            total = self._deferred_total_frames or len(self._frames)
            kind_word = "MP4" if self._deferred_kind == "mp4" else "GIF"
            extra = f", {self._video_fps:.1f} fps" if self._video_fps else ""
            kind = f"{kind_word} (1 of {total} frames shown{extra} — preview only)"
        elif self._is_video:
            kind = f"MP4 ({len(self._frames)} frames, {self._video_fps:.1f} fps)"
        elif self._is_anim:
            kind = f"GIF ({len(self._frames)} frames)"
        else:
            kind = "Image"
        w, h = self._frames[0].size
        self._status.config(text=f"{name}  •  {kind}  •  {w}×{h}px")
        log.info("FILE_OPEN  %s  %s  %dx%d", name, kind, w, h)

        self._source_path = path
        self._single_frame_var.set(bool(self._deferred_path))
        self._update_single_frame_visibility()

        self._anim_idx = 0
        self._apply_dynamic_width()
        self._safe_refresh(debounce_ms=0)

    def _export_fps(self) -> float:
        """FPS to use when encoding an MP4 — the source video's fps if known,
        otherwise derived from the average GIF frame duration."""
        if self._video_fps:
            return self._video_fps
        if self._durations and any(self._durations):
            avg_ms = sum(self._durations) / len(self._durations)
            if avg_ms > 0:
                return 1000.0 / avg_ms
        return 10.0

    def _iter_export_frames(self):
        """Yield PIL frames of the current file, one at a time, for export.
        Streams directly from disk if the source is deferred — so exporting
        a huge GIF/video never needs more than a single frame in memory at
        once, regardless of its total length — or yields from the frames
        already loaded in self._frames otherwise."""
        if self._deferred_path:
            path, kind = self._deferred_path, self._deferred_kind
            if kind == "mp4":
                reader = imageio.get_reader(path, "ffmpeg")
                try:
                    for frame in reader:
                        yield Image.fromarray(frame).convert("RGBA")
                finally:
                    reader.close()
            else:
                img = Image.open(path)
                for frm in ImageSequence.Iterator(img):
                    yield frm.copy().convert("RGBA")
        else:
            yield from self._frames

    def _stream_export(self, output_path: str, as_mp4: bool) -> None:
        """Composite and write every frame of the current file directly to
        output_path, one frame at a time, instead of building the full
        source and composited frame lists first — this is what makes it
        possible to export files too large to ever fully fit in RAM at once.
        Updates self._status with progress as it goes; the caller is
        responsible for restoring the status text afterward."""
        params = self._collect_render_params()
        if params is None:
            raise RuntimeError("Invalid render parameters")

        total = self._deferred_total_frames if self._deferred_path else len(self._frames)
        fps = self._export_fps()

        if as_mp4:
            writer = imageio.get_writer(output_path, fps=fps, codec="libx264", quality=8)
        else:
            if self._deferred_path:
                # Streaming from disk — getting exact per-frame durations would
                # need a second full pass just to read timing metadata, so
                # large/deferred GIFs use one uniform duration derived from fps
                # instead. Already-loaded files keep their exact per-frame
                # durations below, since those are already in memory for free.
                duration = max(1, round(1000 / fps))
            else:
                duration = self._durations if self._durations else max(1, round(1000 / fps))
            writer = imageio.get_writer(output_path, mode="I", duration=duration, loop=0)

        base_status = self._status.cget("text")
        try:
            for i, frame in enumerate(self._iter_export_frames()):
                composited = build_composite(frame, **params)
                arr = np.array(composited.convert("RGB"))
                if as_mp4:
                    arr = _pad_to_macro_block(arr)
                writer.append_data(arr)
                if i % 20 == 0 or i == total - 1:
                    self._status.config(text=f"{base_status}  [Exporting frame {i + 1}/{total}…]")
                    self.root.update_idletasks()
        finally:
            writer.close()

    def _save(self) -> None:
        if not self._cache:
            messagebox.showwarning("Nothing to save", "Open an image, GIF, or video first.")
            return

        if self._is_anim:
            if self._is_video:
                default_ext = ".mp4"
                filetypes = [("MP4 Video", "*.mp4"), ("Animated GIF", "*.gif")]
            else:
                default_ext = ".gif"
                filetypes = [("Animated GIF", "*.gif"), ("MP4 Video", "*.mp4")]
            path = filedialog.asksaveasfilename(
                defaultextension=default_ext, filetypes=filetypes)
            if not path:
                return

            # Streams frame-by-frame (see _stream_export) instead of building the
            # full source and composited frame lists first, regardless of whether
            # the source is still deferred (a large-file placeholder) or fully loaded.
            self._build_cancel.set()
            if self._refresh_job:
                self.root.after_cancel(self._refresh_job)
                self._refresh_job = None
            base_status = self._status.cget("text").replace(" [Rendering…]", "")
            try:
                self._stream_export(path, as_mp4=path.lower().endswith(".mp4"))
            except Exception:
                log.exception("SAVE_RENDER_ERROR")
                # A failed streaming export leaves a truncated file at the
                # destination path rather than a separate temp file — remove it
                # so a partial/corrupt file isn't left where a good one was expected.
                try:
                    os.unlink(path)
                except OSError:
                    pass
                messagebox.showerror("Render Error", "Failed to render/export all frames for saving.")
                return
            finally:
                self._status.config(text=base_status)
        else:
            path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp")],
            )
            if not path:
                return
            out = self._cache[0]
            if path.lower().endswith((".jpg", ".jpeg")):
                out = out.convert("RGB")
            out.save(path)

        log.info("FILE_SAVE  %s", os.path.basename(path))
        messagebox.showinfo("Saved", f"Saved:\n{path}")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _reload_formats(self) -> None:
        """Re-scan formats/ dir and refresh the dropdown."""
        self._formats = _load_formats()
        self._fmt_combo.config(values=list(self._formats.keys()))
        if self._format_var.get() not in self._formats and self._formats:
            self._format_var.set(next(iter(self._formats)))
        self._on_format_change()

    def _on_format_change(self) -> None:
        fmt = self._format_var.get()
        data = self._formats.get(fmt, {})

        # Colors
        fc = data.get("font_color", "#111111")
        bg = data.get("page_bg_color", "#f5f5f5")
        sc = data.get("stroke_color", "#000000")
        self._fc_color = fc
        self._fc_btn.config(bg=fc)
        self._page_bg_color = bg
        self._bg_btn.config(bg=bg)
        self._stroke_color = sc
        self._stroke_btn.config(bg=sc)

        # Caption settings
        self._stroke_width_var.set(data.get("stroke_width", 0))
        self._font_var.set(data.get("font_family", "Arial"))
        self._bold_var.set(data.get("bold", False))
        self._pad_var.set(data.get("padding", 20))
        self._auto_size_var.set(data.get("auto_size", False))
        self._align_var.set(data.get("align", "center"))

        # Panel size and label
        self._dynamic_width_var.set(data.get("dynamic_width", False))
        self._width_spin.config(state="disabled" if self._dynamic_width_var.get() else "normal")
        if "cap_panel_size" in data and not self._dynamic_width_var.get():
            self._width_var.set(data["cap_panel_size"])
        if data.get("layout") == "vertical":
            self._cap_size_label.config(text="Caption Height:")
        else:
            self._cap_size_label.config(text="Caption Width:")

        # Header
        hfont = data.get("header_font", "Arial")
        if hfont == _AARDVARK_NAME and not _check_aardvark():
            hfont = "Arial Bold"
        self._header_font_var.set(hfont)
        self._header_size_var.set(data.get("header_size", 28))
        if not self._header_text_box.get("1.0", "end-1c").strip():
            self._header_text_box.delete("1.0", "end")
            self._header_text_box.insert("1.0", data.get("header_text", ""))

        # Footer
        ffont = data.get("footer_font", "Arial")
        if ffont == _AARDVARK_NAME and not _check_aardvark():
            ffont = "Arial Bold"
        self._footer_font_var.set(ffont)
        self._footer_size_var.set(data.get("footer_size", 16))
        self._footer_enabled.set(data.get("footer_enabled", False))
        self._update_watermark_height_visibility()
        if not self._footer_text_box.get("1.0", "end-1c").strip():
            self._footer_text_box.delete("1.0", "end")
            self._footer_text_box.insert("1.0", data.get("footer_text", ""))

        # Pill presets
        if data.get("show_pill_presets", False):
            self._pill_frame.pack(fill="x", padx=8, pady=(0, 4), before=self._nb)
        else:
            self._pill_frame.pack_forget()

        self._apply_dynamic_width()
        self._safe_refresh()

    def _safe_refresh(self, debounce_ms: int = 400) -> None:
        """Validate vars, show 1-frame preview immediately, schedule full GIF rebuild."""
        try:
            self._size_var.get()
            self._width_var.get()
            self._pad_var.get()
            self._stroke_width_var.get()
            self._header_size_var.get()
            self._footer_size_var.get()
        except tk.TclError:
            return

        if not self._frames:
            return

        # Cancel any pending scheduled rebuild
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None

        # Immediate 1-frame preview on the main thread (fast — no GIF loop)
        self._refresh_preview()

        # For GIFs, schedule the full rebuild after the debounce window
        if self._is_anim:
            self._refresh_job = self.root.after(debounce_ms, self._rebuild_all_async)

    def _collect_render_params(self) -> Optional[dict]:
        """Return all build_composite kwargs from current UI state, or None if invalid."""
        text = self._text_box.get("1.0", "end-1c")
        try:
            max_size = self._size_var.get()
            width = self._width_var.get()
            pad = self._pad_var.get()
            stroke_w = self._stroke_width_var.get()
            footer_sz = self._footer_size_var.get()
            if self._footer_enabled.get():
                wm_h = int(footer_sz * 2.0)
            else:
                wm_h = self._watermark_height_var.get()
        except tk.TclError:
            return None

        bold = self._bold_var.get()
        fmt = self._format_var.get()
        fmt_data = self._formats.get(fmt, {})
        shadow = fmt_data.get("shadow", False)
        header_enabled = fmt_data.get("header_enabled", False)
        layout = fmt_data.get("layout", "horizontal")

        if self._auto_size_var.get() and self._frames:
            if layout == "vertical":
                # Vertical: panel spans full image width; cap_width is the panel height
                fw = self._frames[0].size[0]
                size = _fit_font_size(text, self._font_var.get(), max_size,
                                      fw, width, pad, stroke_w, bold)
            else:
                fh = self._frames[0].size[1]
                size = _fit_font_size(text, self._font_var.get(), max_size,
                                      width, fh, pad, stroke_w, bold)
        else:
            size = max_size

        kwargs: dict = dict(
            stroke_width=stroke_w,
            stroke_color=self._stroke_color,
            bold=bold,
            shadow=shadow,
            align=self._align_var.get(),
            fmt=fmt,
            layout=layout,
        )

        if header_enabled:
            try:
                kwargs.update(
                    header_text=self._header_text_box.get("1.0", "end-1c"),
                    header_font=self._header_font_var.get(),
                    header_size=self._header_size_var.get(),
                )
            except tk.TclError:
                return None

        try:
            kwargs.update(
                footer_enabled=self._footer_enabled.get(),
                footer_text=self._footer_text_box.get("1.0", "end-1c"),
                footer_font=self._footer_font_var.get(),
                footer_size=footer_sz,
                watermark_path=self._watermark_path,
                watermark_height=wm_h,
                output_size_pct=(self._output_pct_var.get()
                                 if self._output_override_var.get() else 100),
            )
        except tk.TclError:
            return None

        return dict(
            text=text,
            cap_width=width,
            page_bg=self._page_bg_color,
            font_family=self._font_var.get(),
            font_size=size,
            font_color=self._fc_color,
            padding=pad,
            **kwargs,
        )

    def _refresh_preview(self) -> None:
        """Build the first frame only on the main thread for immediate visual feedback."""
        if not self._frames:
            return
        self._stop_anim()

        params = self._collect_render_params()
        if params is None:
            return

        t0 = time.perf_counter()
        try:
            first = build_composite(self._frames[0], **params)
        except Exception:
            log.exception("PREVIEW_ERROR")
            return
        elapsed = time.perf_counter() - t0
        if elapsed > 0.5:
            log.warning("SLOW_PREVIEW  elapsed=%.3fs", elapsed)

        self._cache = [first]
        self._cache_complete = not self._is_anim
        self._anim_idx = 0
        self._update_preview_label()
        self._redraw()

    def _rebuild_all_async(self) -> None:
        """Build all GIF frames in a background thread; post results back to main thread."""
        self._refresh_job = None
        if not self._frames or not self._is_anim:
            return
        if self._deferred_path:
            # Only a single placeholder frame is loaded — nothing meaningful to
            # rebuild yet. Rebuilding it would trivially "complete" and mark
            # _cache_complete True, which would wrongly skip materializing the
            # full source at save/send time. Full processing happens there instead.
            return

        params = self._collect_render_params()
        if params is None:
            return

        # Signal any running build to stop and create a fresh cancellation token
        self._build_cancel.set()
        self._build_cancel = threading.Event()
        cancel = self._build_cancel
        frames = self._frames[:]  # snapshot so the thread doesn't see file reloads

        txt = self._status.cget("text")
        if "[Rendering" not in txt:
            self._status.config(text=txt + " [Rendering…]")

        def _build() -> None:
            results = []
            for i, f in enumerate(frames):
                if cancel.is_set():
                    log.debug("GIF_BUILD_CANCELLED  frame=%d", i)
                    return
                try:
                    results.append(build_composite(f, **params))
                except Exception:
                    log.exception("FRAME_BUILD_ERROR  frame=%d", i)
                    return
            if not cancel.is_set():
                self.root.after(0, lambda r=results: self._on_rebuild_done(r))

        threading.Thread(target=_build, daemon=True).start()

    def _on_rebuild_done(self, results: list) -> None:
        """Called on the main thread when the background GIF build finishes."""
        self._cache = results
        self._cache_complete = True
        txt = self._status.cget("text")
        self._status.config(text=txt.replace(" [Rendering…]", ""))
        log.info("GIF_REBUILD_DONE  frames=%d", len(results))
        self._anim_idx = 0
        self._update_preview_label()
        self._start_anim()

    # ------------------------------------------------------------------
    # DeviantArt integration
    # ------------------------------------------------------------------

    def _da_show_log(self) -> None:
        """Open (or bring to front) the DeviantArt log window."""
        if hasattr(self, "_da_log_win") and self._da_log_win and self._da_log_win.winfo_exists():
            self._da_log_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("DeviantArt Log")
        win.geometry("620x300")
        win.resizable(True, True)
        self._da_log_win = win

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        txt = tk.Text(frame, state="disabled", wrap="word", font=("Consolas", 9),
                      bg="#1e1e1e", fg="#d4d4d4", relief="flat")
        sb = ttk.Scrollbar(frame, command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.tag_config("err",  foreground="#f44747")
        txt.tag_config("warn", foreground="#ffcc00")
        txt.tag_config("info", foreground="#9cdcfe")
        txt.tag_config("dbg",  foreground="#888888")
        self._da_log_text = txt

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=6, pady=4)

        def _clear():
            txt.config(state="normal")
            txt.delete("1.0", "end")
            txt.config(state="disabled")
        ttk.Button(btn_row, text="Clear Log", command=_clear).pack(side="right")

        # Attach a logging handler that routes da_client records to this window
        da_logger = logging.getLogger("da_client")
        da_logger.setLevel(logging.DEBUG)
        handler = _DALogHandler(txt)
        handler.setLevel(logging.DEBUG)
        self._da_log_handler = handler
        da_logger.addHandler(handler)

        def _on_close():
            da_logger.removeHandler(handler)
            self._da_log_handler = None
            self._da_log_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _da_update_status(self) -> None:
        """Refresh the DA login status label, and show/hide DA Login vs.
        Send-to-DA/Log-Out based on cached token state. Uses winfo_manager()
        rather than winfo_ismapped() — the latter reflects actual on-screen
        mapping, which can read False even when packed (e.g. before the
        window is first drawn), causing a spurious re-pack/no-op."""
        send_packed = self._da_send_btn.winfo_manager() == "pack"
        logout_packed = self._da_logout_btn.winfo_manager() == "pack"
        login_packed = self._da_login_btn.winfo_manager() == "pack"
        if da_client.da_has_cached_token():
            self._da_status_label.config(text="DA: logged in", foreground="#4a4")
            if not send_packed:
                self._da_send_btn.pack(side="left", padx=2, before=self._da_settings_btn)
            if not logout_packed:
                self._da_logout_btn.pack(side="left", padx=2, before=self._da_settings_btn)
            if login_packed:
                self._da_login_btn.pack_forget()
        else:
            self._da_status_label.config(text="DA: not logged in", foreground="#999")
            if not login_packed:
                self._da_login_btn.pack(side="left", padx=2,
                                        before=self._da_send_btn if send_packed else self._da_settings_btn)
            if send_packed:
                self._da_send_btn.pack_forget()
            if logout_packed:
                self._da_logout_btn.pack_forget()

    def _da_logout(self) -> None:
        """Log out of DeviantArt — deletes the cached token so the next
        upload requires re-authorization."""
        if not messagebox.askyesno(
            "Log Out",
            "Log out of DeviantArt?\n\nYou'll need to click DA Login and "
            "re-authorize before sending anything again."
        ):
            return
        da_client.da_logout()
        log.info("DA_LOGOUT")
        self._da_update_status()

    def _da_login(self) -> None:
        """Authenticate with DeviantArt (opens browser, shows log window)."""
        client_id = da_client.load_client_id()
        if not client_id:
            client_id = self._da_settings()
            if not client_id:
                return

        if getattr(self, "_da_login_in_progress", False):
            self._da_show_log()
            return

        # If a valid token already exists, ask before re-opening the browser
        if da_client.da_has_cached_token():
            if not messagebox.askyesno(
                "Already Logged In",
                "You are already logged in to DeviantArt.\nOpen the browser to re-authorize?"
            ):
                return

        self._da_login_in_progress = True
        self._da_show_log()

        def _open_browser(url):
            self.root.after(0, lambda u=url: webbrowser.open(u))

        def _do_login():
            try:
                da_client.da_authorize(client_id, open_browser=_open_browser)
                self.root.after(0, self._da_update_status)
            except Exception as exc:
                log.exception("DA_LOGIN_ERROR: %s", exc)
                self.root.after(0, self._da_update_status)
            finally:
                self._da_login_in_progress = False

        threading.Thread(target=_do_login, daemon=True).start()

    def _da_settings(self, *, focus_entry: bool = True) -> Optional[str]:
        """Open the DA settings dialog. Returns entered client_id or None."""
        dlg = tk.Toplevel(self.root)
        dlg.title("DeviantArt Settings")
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text="DeviantArt Client ID:", padding=(12, 12, 12, 4)).pack(anchor="w")
        entry = ttk.Entry(dlg, width=32)
        entry.pack(padx=12, pady=(0, 4))
        existing = da_client.load_client_id()
        if existing:
            entry.insert(0, existing)
        if focus_entry:
            entry.focus_set()

        ttk.Label(
            dlg,
            text="Register at deviantart.com/developers to get a Client ID.\n"
                 'Set the OAuth2 redirect URI to EXACTLY '
                 f'"http://127.0.0.1:{da_client.REDIRECT_PORT}" when registering '
                 '(DeviantArt requires an exact match, port included).',
            foreground="#555", padding=(12, 0, 12, 8), wraplength=280,
        ).pack(anchor="w")

        result: list = [None]

        def _save():
            cid = entry.get().strip()
            if cid:
                da_client.save_client_id(cid)
                result[0] = cid
            dlg.destroy()

        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_row, text="Save", command=_save).pack(side="right", padx=(4, 0))
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="right")

        dlg.bind("<Return>", lambda _: _save())
        self.root.wait_window(dlg)
        return result[0]

    def _da_send_prompt(self, default_title: str, default_description: str,
                        offer_mp4: bool = False) -> Optional[tuple]:
        """Ask for a title (required), description, and — if offer_mp4 — an
        upload format before uploading to DA.
        Returns (title, description, upload_format) where upload_format is
        "mp4" or "gif", or None if the user cancelled."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Send to DeviantArt")
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text="Title:", padding=(12, 12, 12, 2)).pack(anchor="w")
        title_entry = ttk.Entry(dlg, width=40)
        title_entry.insert(0, default_title)
        title_entry.pack(padx=12, pady=(0, 8), fill="x")
        title_entry.focus_set()
        title_entry.icursor("end")

        ttk.Label(dlg, text="Description:", padding=(12, 0, 12, 2)).pack(anchor="w")
        desc_box = tk.Text(dlg, width=40, height=6, wrap="word", relief="solid", bd=1,
                           padx=4, pady=4)
        desc_box.insert("1.0", default_description)
        desc_box.pack(padx=12, pady=(0, 10), fill="both", expand=True)

        fmt_var = tk.StringVar(value="mp4" if offer_mp4 else "gif")
        if offer_mp4:
            fmt_row = ttk.Frame(dlg)
            fmt_row.pack(fill="x", padx=12, pady=(0, 8))
            ttk.Label(fmt_row, text="Upload as:").pack(side="left")
            ttk.Radiobutton(fmt_row, text="MP4", variable=fmt_var,
                            value="mp4").pack(side="left", padx=(8, 4))
            ttk.Radiobutton(fmt_row, text="GIF", variable=fmt_var,
                            value="gif").pack(side="left")

        result: list = [None]

        def _ok():
            title = title_entry.get().strip()
            if not title:
                messagebox.showwarning("Title Required", "Please enter a title.", parent=dlg)
                return
            description = desc_box.get("1.0", "end-1c").strip()
            result[0] = (title, description, fmt_var.get())
            dlg.destroy()

        btn_row = ttk.Frame(dlg)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_row, text="Send", command=_ok).pack(side="right", padx=(4, 0))
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="right")

        # Bind Enter only on the single-line title field — binding it on the whole
        # dialog would hijack Enter inside the multi-line description box too.
        title_entry.bind("<Return>", lambda _: _ok())
        self.root.wait_window(dlg)
        return result[0]

    def _da_send(self) -> None:
        """Export the current output and save it as a private draft on DeviantArt."""
        if not self._cache:
            messagebox.showwarning("Nothing to send", "Open an image, GIF, or video first.")
            return
        if getattr(self, "_da_in_progress", False):
            return

        raw_text = self._text_box.get("1.0", "end-1c").strip()
        prompt = self._da_send_prompt("", raw_text, offer_mp4=self._is_video)
        if prompt is None:
            return
        title, description, upload_format = prompt

        self._da_in_progress = True

        # Ensure client_id is configured before doing the potentially long export below
        client_id = da_client.load_client_id()
        if not client_id:
            client_id = self._da_settings()
            if not client_id:
                self._da_in_progress = False
                return

        # Write temp file. Animated content streams frame-by-frame (see
        # _stream_export) instead of building the full source and composited
        # frame lists first, regardless of whether the source is still
        # deferred (a large-file placeholder) or fully loaded.
        import tempfile
        suffix = (".mp4" if upload_format == "mp4" else ".gif") if self._is_anim else ".png"
        self._build_cancel.set()
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        base_status = self._status.cget("text").replace(" [Rendering…]", "")
        tmp_path = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp_path = tmp.name
            tmp.close()
            if self._is_anim:
                self._stream_export(tmp_path, as_mp4=(upload_format == "mp4"))
            else:
                self._cache[0].save(tmp_path)
        except Exception:
            log.exception("DA_SEND_EXPORT_ERROR")
            self._da_in_progress = False
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            messagebox.showerror("Export Error", "Failed to render/export the file for upload.")
            return
        finally:
            self._status.config(text=base_status)

        # Upload runs in a background thread; token must already be cached (use DA Login first)
        self._status.config(text=self._status.cget("text") + " [Saving draft…]")

        def _upload():
            try:
                token = da_client.da_ensure_silent_token(client_id)
            except Exception as exc:
                def _on_no_token(e=exc):
                    self._da_in_progress = False
                    self._status.config(
                        text=self._status.cget("text").replace(" [Saving draft…]", ""))
                    messagebox.showerror(
                        "Not Logged In",
                        f"{e}\n\nClick 'DA Login' to authenticate first."
                    )
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                self.root.after(0, _on_no_token)
                return

            try:
                stackid = da_client.da_stash_submit(token, tmp_path, title,
                                                     artist_comments=description)
                self.root.after(0, lambda: self._da_upload_done(stackid, token, tmp_path))
            except Exception as exc:
                log.exception("DA_UPLOAD_ERROR")
                self.root.after(0, lambda e=exc: self._da_upload_failed(str(e), tmp_path))

        threading.Thread(target=_upload, daemon=True).start()

    def _da_upload_done(self, stackid: str, token: str, tmp_path: str) -> None:
        self._da_in_progress = False
        self._status.config(text=self._status.cget("text").replace(" [Saving draft…]", ""))
        log.info("DA_UPLOAD_DONE  stackid=%s", stackid)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

        dlg = tk.Toplevel(self.root)
        dlg.title("Saved as Draft")
        dlg.resizable(False, False)
        dlg.grab_set()
        ttk.Label(dlg, text="Saved as draft on DeviantArt.",
                  padding=(16, 16, 16, 8)).pack()

        pub_status = ttk.Label(dlg, text="", foreground="#555", padding=(16, 0, 16, 4))
        pub_status.pack()

        def _publish():
            pub_btn.config(state="disabled")
            pub_status.config(text="Publishing…")
            dlg.update_idletasks()

            def _do():
                try:
                    url = da_client.da_stash_publish(token, stackid)
                    self.root.after(0, lambda u=url: _publish_done(u))
                except Exception as exc:
                    self.root.after(0, lambda e=exc: _publish_fail(str(e)))

            threading.Thread(target=_do, daemon=True).start()

        def _publish_done(url: str):
            log.info("DA_PUBLISH_DONE  url=%s", url)
            pub_status.config(text=f"Published: {url}")
            pub_btn.pack_forget()
            webbrowser.open(url)

        def _publish_fail(msg: str):
            pub_status.config(text=f"Publish failed: {msg}", foreground="red")
            pub_btn.config(state="normal")

        btn_row = ttk.Frame(dlg)
        btn_row.pack(pady=(4, 16), padx=16)
        pub_btn = ttk.Button(btn_row, text="Publish to Gallery", command=_publish)
        pub_btn.pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Close", command=dlg.destroy).pack(side="left")

    def _da_upload_failed(self, msg: str, tmp_path: str) -> None:
        self._da_in_progress = False
        self._status.config(text=self._status.cget("text").replace(" [Saving draft…]", ""))
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        messagebox.showerror("Upload Failed", f"DeviantArt upload failed:\n{msg}")

    def _update_preview_label(self) -> None:
        """Show the actual final output size (post minimum-size upscale) next to
        the Preview panel label — the on-screen preview itself is fit-scaled
        smaller by _redraw(), so this is the only place the real size is visible."""
        if self._cache:
            w, h = self._cache[0].size
            self._preview_frame.config(text=f"Preview  (Output: {w}×{h}px)")
        else:
            self._preview_frame.config(text="Preview")

    def _redraw(self) -> None:
        if not self._cache:
            return
        img = self._cache[self._anim_idx]
        cw = max(self._canvas.winfo_width(), 10)
        ch = max(self._canvas.winfo_height(), 10)
        scale = min(cw / img.width, ch / img.height, 1.0)
        disp = img.resize((max(1, int(img.width * scale)),
                           max(1, int(img.height * scale))), Image.LANCZOS)
        self._preview_pil_img = disp  # kept for the color-eyedropper to sample from
        self._tk_img = ImageTk.PhotoImage(disp)
        self._canvas.delete("all")
        self._canvas.create_image(cw // 2, ch // 2, anchor="center", image=self._tk_img)

    def _start_anim(self) -> None:
        self._anim_step()

    def _anim_step(self) -> None:
        if not self._cache or not self._is_anim:
            return
        self._redraw()
        delay = max(20, self._durations[self._anim_idx])
        self._anim_idx = (self._anim_idx + 1) % len(self._cache)
        self._anim_id = self.root.after(delay, self._anim_step)

    def _stop_anim(self) -> None:
        if self._anim_id:
            self.root.after_cancel(self._anim_id)
            self._anim_id = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    root.geometry("1100x680")
    CaptionApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
