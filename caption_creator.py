#!/usr/bin/env python3
"""Caption Creator — add styled caption panels beside images and GIFs."""

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


def _find_font_file(stem: str) -> Optional[str]:
    """Return path to a font file whose name contains `stem` (case-insensitive)."""
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
                   stroke_width: int = 0) -> int:
    """Binary-search for the largest font size where wrapped text fits in area_w×area_h."""
    if not text.strip():
        return max_size
    tmp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lo, hi, best = 6, max_size, 6
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _pil_font(font_family, mid)
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

    out.paste(frame, (0, 0))
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
        self._is_gif: bool = False
        self._anim_id: Optional[str] = None
        self._anim_idx: int = 0
        self._tk_img = None

        # Background and text (shared globally)
        self._page_bg_color = "#f5f5f5"
        self._fc_color = "#111111"
        self._stroke_color = "#000000"
        available_fonts = _font_list()
        self._font_var = tk.StringVar(value=available_fonts[0] if available_fonts else "Arial")
        self._size_var = tk.IntVar(value=72)
        self._width_var = tk.IntVar(value=320)
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
        ttk.Button(bar, text="Open Image / GIF…", command=self._open).pack(side="left", padx=4)
        ttk.Button(bar, text="Save…", command=self._save).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=4)
        ttk.Button(bar, text="DA Login", command=self._da_login).pack(side="left", padx=2)
        ttk.Button(bar, text="Send to DA…", command=self._da_send).pack(side="left", padx=2)
        ttk.Button(bar, text="DA Settings", command=self._da_settings).pack(side="left", padx=2)
        self._da_status_label = ttk.Label(bar, text="DA: not logged in", foreground="#999")
        self._da_status_label.pack(side="left", padx=(6, 2))
        self._status = ttk.Label(bar, text="No file loaded", foreground="#888")
        self._status.pack(side="left", padx=12)

        # ---- Right control panel (fixed width, packed before preview) ----
        right = tk.Frame(self.root, width=320, bg=self.root.cget("bg"))
        right.pack(side="right", fill="y", padx=(0, 4), pady=(0, 4))
        right.pack_propagate(False)

        # ---- Preview canvas fills remaining left space ----
        pf = ttk.LabelFrame(self.root, text="Preview", padding=4)
        pf.pack(side="left", fill="both", expand=True, padx=(4, 2), pady=(0, 4))
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
                  self._footer_font_var, self._footer_size_var):
            v.trace_add("write", lambda *_: self._safe_refresh())
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
        r += 1

        ttk.Label(f, text="Page BG Color:").grid(row=r, column=0, sticky="w", pady=4)
        self._bg_btn = tk.Button(f, bg=self._page_bg_color, width=5, relief="groove",
                                 command=self._pick_bg)
        self._bg_btn.grid(row=r, column=1, sticky="w", padx=6)
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
        self._fc_btn = tk.Button(f, bg=self._fc_color, width=5, relief="groove",
                                 command=self._pick_fc)
        self._fc_btn.grid(row=r, column=1, sticky="w", padx=6)
        r += 1

        self._cap_size_label = ttk.Label(f, text="Caption Width:")
        self._cap_size_label.grid(row=r, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=80, to=1200, textvariable=self._width_var,
                    width=7).grid(row=r, column=1, sticky="w", padx=6)
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
        self._stroke_btn = tk.Button(f, bg=self._stroke_color, width=5, relief="groove",
                                     command=self._pick_stroke)
        self._stroke_btn.grid(row=r, column=1, sticky="w", padx=6)

    def _build_xchange_tab(self, f: ttk.Frame) -> None:
        f.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(f, text="Title Text:").grid(row=r, column=0, sticky="nw", pady=4)
        self._header_text_box = tk.Text(f, width=18, height=3, wrap="word",
                                        relief="solid", bd=1, padx=3, pady=3)
        self._header_text_box.grid(row=r, column=1, sticky="ew", padx=6, pady=(2, 8))
        self._header_text_box.insert("1.0", "")
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
                        command=self._safe_refresh).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 8))
        r += 1

        ttk.Label(f, text="Footer Text:").grid(row=r, column=0, sticky="nw", pady=4)
        self._footer_text_box = tk.Text(f, width=18, height=3, wrap="word",
                                        relief="solid", bd=1, padx=3, pady=3)
        self._footer_text_box.grid(row=r, column=1, sticky="ew", padx=6, pady=(2, 8))
        self._footer_text_box.insert("1.0", "")
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

    def _open(self) -> None:
        path = filedialog.askopenfilename(
            title="Open Image or GIF",
            filetypes=[
                ("Images & GIFs", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
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

        img = Image.open(path)
        is_anim = getattr(img, "is_animated", False)
        self._is_gif = is_anim or path.lower().endswith(".gif")

        if self._is_gif:
            for frm in ImageSequence.Iterator(img):
                self._frames.append(frm.copy().convert("RGBA"))
                self._durations.append(frm.info.get("duration", 100))
        else:
            self._frames.append(img.convert("RGBA"))
            self._durations.append(0)

        name = os.path.basename(path)
        kind = f"GIF ({len(self._frames)} frames)" if self._is_gif else "Image"
        w, h = self._frames[0].size
        self._status.config(text=f"{name}  •  {kind}  •  {w}×{h}px")
        log.info("FILE_OPEN  %s  %s  %dx%d", name, kind, w, h)

        self._anim_idx = 0
        self._safe_refresh(debounce_ms=0)

    def _save(self) -> None:
        if not self._cache:
            messagebox.showwarning("Nothing to save", "Open an image or GIF first.")
            return

        # If a GIF background build is still in progress or was never completed,
        # cancel it and do a blocking full render now before saving.
        if self._is_gif and not self._cache_complete:
            self._build_cancel.set()
            if self._refresh_job:
                self.root.after_cancel(self._refresh_job)
                self._refresh_job = None
            txt = self._status.cget("text").replace(" [Rendering…]", "")
            self._status.config(text=txt + " [Rendering for save…]")
            self.root.update_idletasks()
            try:
                self._refresh()
            except Exception:
                log.exception("SAVE_RENDER_ERROR")
                messagebox.showerror("Render Error", "Failed to render all frames for saving.")
                return
            finally:
                t = self._status.cget("text")
                self._status.config(text=t.replace(" [Rendering for save…]", ""))

        if self._is_gif:
            path = filedialog.asksaveasfilename(
                defaultextension=".gif",
                filetypes=[("Animated GIF", "*.gif")],
            )
            if not path:
                return
            rgb = [f.convert("RGB") for f in self._cache]
            rgb[0].save(path, save_all=True, append_images=rgb[1:],
                        loop=0, duration=self._durations, optimize=False)
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
        if "cap_panel_size" in data:
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
        if not self._footer_text_box.get("1.0", "end-1c").strip():
            self._footer_text_box.delete("1.0", "end")
            self._footer_text_box.insert("1.0", data.get("footer_text", ""))

        # Pill presets
        if data.get("show_pill_presets", False):
            self._pill_frame.pack(fill="x", padx=8, pady=(0, 4), before=self._nb)
        else:
            self._pill_frame.pack_forget()

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
        if self._is_gif:
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
            wm_h = int(footer_sz * 2.0)
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
                                      fw, width, pad, stroke_w)
            else:
                fh = self._frames[0].size[1]
                size = _fit_font_size(text, self._font_var.get(), max_size,
                                      width, fh, pad, stroke_w)
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
        self._cache_complete = not self._is_gif
        self._anim_idx = 0
        self._redraw()

    def _rebuild_all_async(self) -> None:
        """Build all GIF frames in a background thread; post results back to main thread."""
        self._refresh_job = None
        if not self._frames or not self._is_gif:
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
        self._start_anim()

    def _refresh(self) -> None:
        """Full synchronous rebuild — used by _save when the cache is incomplete."""
        if not self._frames:
            return
        self._stop_anim()
        params = self._collect_render_params()
        if params is None:
            return
        t0 = time.perf_counter()
        try:
            self._cache = [build_composite(f, **params) for f in self._frames]
            self._cache_complete = True
        except Exception:
            log.exception("RENDER_ERROR  frames=%d", len(self._frames))
            raise
        elapsed = time.perf_counter() - t0
        if elapsed > 1.0:
            log.warning("SLOW_RENDER  frames=%d  total=%.2fs  per_frame=%.0fms",
                        len(self._frames), elapsed, elapsed / len(self._frames) * 1000)
        self._anim_idx = 0
        if self._is_gif:
            self._start_anim()
        else:
            self._redraw()

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
        """Refresh the DA login status label based on cached token state."""
        if da_client.da_has_cached_token():
            self._da_status_label.config(text="DA: logged in", foreground="#4a4")
        else:
            self._da_status_label.config(text="DA: not logged in", foreground="#999")

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
                 'Set the OAuth2 redirect URI to "http://127.0.0.1" when registering.',
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

    def _da_send(self) -> None:
        """Export the current output and save it as a private draft on DeviantArt."""
        if not self._cache:
            messagebox.showwarning("Nothing to send", "Open an image or GIF first.")
            return
        if getattr(self, "_da_in_progress", False):
            return
        self._da_in_progress = True

        # Ensure full GIF cache before uploading
        if self._is_gif and not self._cache_complete:
            self._build_cancel.set()
            if self._refresh_job:
                self.root.after_cancel(self._refresh_job)
                self._refresh_job = None
            old_txt = self._status.cget("text").replace(" [Rendering…]", "")
            self._status.config(text=old_txt + " [Rendering for upload…]")
            self.root.update_idletasks()
            try:
                self._refresh()
            except Exception:
                log.exception("DA_SEND_RENDER_ERROR")
                self._da_in_progress = False
                messagebox.showerror("Render Error", "Failed to render all frames.")
                return
            finally:
                self._status.config(
                    text=self._status.cget("text").replace(" [Rendering for upload…]", ""))

        # Ensure client_id is configured
        client_id = da_client.load_client_id()
        if not client_id:
            client_id = self._da_settings()
            if not client_id:
                self._da_in_progress = False
                return

        # Derive a title from the first non-empty line of caption text
        raw_text = self._text_box.get("1.0", "end-1c").strip()
        title = next((ln.strip() for ln in raw_text.splitlines() if ln.strip()),
                     "Caption Creator Upload")

        # Write temp file
        import tempfile
        suffix = ".gif" if self._is_gif else ".png"
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp_path = tmp.name
            tmp.close()
            if self._is_gif:
                rgb = [f.convert("RGB") for f in self._cache]
                rgb[0].save(tmp_path, save_all=True, append_images=rgb[1:],
                            loop=0, duration=self._durations, optimize=False)
            else:
                self._cache[0].save(tmp_path)
        except Exception:
            log.exception("DA_SEND_EXPORT_ERROR")
            self._da_in_progress = False
            messagebox.showerror("Export Error", "Failed to write temp file for upload.")
            return

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
                stackid = da_client.da_stash_submit(token, tmp_path, title)
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

    def _redraw(self) -> None:
        if not self._cache:
            return
        img = self._cache[self._anim_idx]
        cw = max(self._canvas.winfo_width(), 10)
        ch = max(self._canvas.winfo_height(), 10)
        scale = min(cw / img.width, ch / img.height, 1.0)
        disp = img.resize((max(1, int(img.width * scale)),
                           max(1, int(img.height * scale))), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(disp)
        self._canvas.delete("all")
        self._canvas.create_image(cw // 2, ch // 2, anchor="center", image=self._tk_img)

    def _start_anim(self) -> None:
        self._anim_step()

    def _anim_step(self) -> None:
        if not self._cache or not self._is_gif:
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
