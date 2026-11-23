"""Comic strip builder: several pictures laid out as comic panels (a strip,
a grid or a comic-page template, wide or tall), each cropped as you like,
with an optional narration caption and speech bubble, plus a title.

Pure Pillow (no Tk) so it can run off the UI thread and be tested. A GIF or
animated WebP panel keeps playing: the strip then animates, every panel
looping on its own timing (see render). The app hands in a font loader so
captions use the same fonts as everywhere else.
"""

import math
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence

# Panel shape (width / height); None = the first picture's own shape.
SHAPES = {
    "Square": 1.0,
    "Portrait 3:4": 3 / 4,
    "Tall 9:16": 9 / 16,
    "Landscape 4:3": 4 / 3,
    "Wide 16:9": 16 / 9,
    "Like the first picture": None,
}
# Where a panel's narration caption goes.
CAPTION_PLACES = ("Inside, top", "Inside, bottom", "Below the picture")
BUBBLE_PLACES = ("Auto", "Top left", "Top right", "Bottom left", "Bottom right")

DEFAULTS = {
    "layout": "Strip",
    "vertical": False,
    "columns": 2,
    "shape": "Portrait 3:4",
    "panel_width": 480,
    "gutter": 14,
    "border": 4,
    "border_color": "#111111",
    "page_bg": "#ffffff",
    "caption_bg": "#fff3b0",       # classic yellow narration box
    "caption_fg": "#111111",
    "caption_place": "Inside, top",
    "bubble_bg": "#ffffff",
    "bubble_fg": "#111111",
    "title": "",
    "title_color": "#111111",
    "text_scale": 1.0,
}

MAX_FRAMES = 300            # per animated strip
MAX_SECONDS = 15.0
MIN_DELAY = 40              # ms — browsers slow faster GIF frames down anyway
MAX_PANEL_FRAMES = 240


FontLoader = Callable[[int, bool], ImageFont.FreeTypeFont]


def _default_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (("arialbd.ttf", "Arial Bold.ttf") if bold else ("arial.ttf", "Arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_panel_frames(path: str, max_side: int = 1600) -> list:
    """[(RGB image, delay ms)] for a picture; several for a GIF / animated
    WebP (capped at MAX_PANEL_FRAMES), one for a still."""
    out = []
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        for i, frame in enumerate(ImageSequence.Iterator(im)):
            if i >= MAX_PANEL_FRAMES:
                break
            frame.load()
            delay = frame.info.get("duration", im.info.get("duration"))
            delay = int(delay) if delay else 100
            img = frame.convert("RGBA")
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, img).convert("RGB")
            if max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.LANCZOS)
            out.append((img, max(MIN_DELAY if n > 1 else 0, delay)))
    if len(out) == 1:
        out = [(out[0][0], 0)]
    return out


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

# Page templates, drawn wide (reading left to right): (columns, rows,
# [(x, y, w, h) in grid units], units per normal panel across, down). The
# Vertical switch flips a layout on its diagonal, so the same template
# makes a tall page read top to bottom.
TEMPLATES = {
    "Big + 2": (3, 2, [(0, 0, 2, 2), (2, 0, 1, 1), (2, 1, 1, 1)], 1, 1),
    "2 over 1": (2, 2, [(0, 0, 1, 1), (1, 0, 1, 1), (0, 1, 2, 1)], 1, 1),
    "1 over 2": (2, 2, [(0, 0, 2, 1), (0, 1, 1, 1), (1, 1, 1, 1)], 1, 1),
    "Big + 3": (3, 3, [(0, 0, 2, 3), (2, 0, 1, 1), (2, 1, 1, 1), (2, 2, 1, 1)], 1, 1),
    "Splash + 3": (3, 2, [(0, 0, 3, 1), (0, 1, 1, 1), (1, 1, 1, 1), (2, 1, 1, 1)], 1, 1),
    "3 + splash": (3, 2, [(0, 0, 1, 1), (1, 0, 1, 1), (2, 0, 1, 1), (0, 1, 3, 1)], 1, 1),
    "Big + 4": (4, 2, [(0, 0, 2, 2), (2, 0, 1, 1), (3, 0, 1, 1), (2, 1, 1, 1),
                       (3, 1, 1, 1)], 1, 1),
    "2 over 3": (6, 2, [(0, 0, 3, 1), (3, 0, 3, 1), (0, 1, 2, 1), (2, 1, 2, 1),
                        (4, 1, 2, 1)], 2, 1),
    "Wide middle": (2, 3, [(0, 0, 1, 1), (1, 0, 1, 1), (0, 1, 2, 1), (0, 2, 1, 1),
                           (1, 2, 1, 1)], 1, 1),
    "Splash + 4": (4, 2, [(0, 0, 4, 1), (0, 1, 1, 1), (1, 1, 1, 1), (2, 1, 1, 1),
                          (3, 1, 1, 1)], 1, 1),
    "Manga page": (2, 4, [(0, 0, 2, 1), (0, 1, 1, 1), (1, 1, 1, 1), (0, 2, 1, 2),
                          (1, 2, 1, 1), (1, 3, 1, 1)], 1, 1),
}
# Always offered, for any number of panels.
GENERIC_LAYOUTS = ("Strip", "Grid")
LAYOUTS = GENERIC_LAYOUTS + tuple(TEMPLATES)


def layouts_for(n: int) -> list:
    """The layouts that fit n panels: Strip and Grid, plus every template
    with exactly n panels."""
    return list(GENERIC_LAYOUTS) + [k for k, t in TEMPLATES.items() if len(t[2]) == n]


def slots(n: int, layout: str, columns: int = 2, vertical: bool = False) -> tuple:
    """(grid columns, grid rows, [(x, y, w, h)], kx, ky) for n panels. A
    template with a different panel count falls back to Strip."""
    n = max(1, n)
    layout = {"Row": "Strip", "Column": "Strip"}.get(layout, layout)
    if layout == "Grid":
        cols = max(1, min(n, int(columns)))
        rows = math.ceil(n / cols)
        # Half-panel units, so a short last row can sit centered.
        rects = []
        for i in range(n):
            r, c = divmod(i, cols)
            in_row = min(cols, n - r * cols)
            offset = cols - in_row
            rects.append((offset + 2 * c, r, 2, 1))
        gc, gr, kx, ky = 2 * cols, rows, 2, 1
    elif layout in TEMPLATES and len(TEMPLATES[layout][2]) == n:
        gc, gr, rects, kx, ky = TEMPLATES[layout]
    else:
        gc, gr, rects, kx, ky = n, 1, [(i, 0, 1, 1) for i in range(n)], 1, 1
    if vertical:
        gc, gr, kx, ky = gr, gc, ky, kx
        rects = [(y, x, h, w) for x, y, w, h in rects]
    return gc, gr, list(rects), kx, ky


def grid_shape(n: int, layout: str, columns: int = 2, vertical: bool = False) -> tuple:
    """(panels across, panels down) — roughly, for sizing a preview."""
    gc, gr, _rects, kx, ky = slots(n, layout, columns, vertical)
    return max(1, round(gc / kx)), max(1, round(gr / ky))


def panel_size(opts: dict, first_size: Optional[tuple]) -> tuple:
    w = max(80, int(opts["panel_width"]))
    ratio = SHAPES.get(opts["shape"], 1.0)
    if ratio is None:
        ratio = (first_size[0] / first_size[1]) if first_size and first_size[1] else 1.0
    ratio = max(0.3, min(3.5, ratio))
    return w, max(60, int(round(w / ratio)))


def _place(place: str) -> str:
    """Caption place, accepting the older "Below the panel"."""
    return "Below the picture" if place == "Below the panel" else place


def cover_crop(img: Image.Image, size: tuple, focus: tuple = (0.5, 0.5),
               zoom: float = 1.0) -> Image.Image:
    """Scale img to fill size, keeping the focus point (fractions of the
    picture) as central as the edges allow."""
    tw, th = size
    iw, ih = img.size
    scale = max(tw / iw, th / ih) * max(1.0, zoom)
    cw, ch = tw / scale, th / scale
    fx, fy = focus
    left = min(max(0.0, fx * iw - cw / 2), iw - cw)
    top = min(max(0.0, fy * ih - ch / 2), ih - ch)
    return img.resize((tw, th), Image.LANCZOS, box=(left, top, left + cw, top + ch))


def _map_point(pt: tuple, img_size: tuple, size: tuple, focus: tuple,
               zoom: float = 1.0) -> tuple:
    """Where a point of the source picture lands in its cover-cropped panel."""
    tw, th = size
    iw, ih = img_size
    scale = max(tw / iw, th / ih) * max(1.0, zoom)
    cw, ch = tw / scale, th / scale
    left = min(max(0.0, focus[0] * iw - cw / 2), iw - cw)
    top = min(max(0.0, focus[1] * ih - ch / 2), ih - ch)
    return (pt[0] - left) * scale, (pt[1] - top) * scale


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def wrap(text: str, font, max_w: float) -> list:
    """Word-wrapped lines (long words are broken) that fit max_w."""
    lines = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            trial = f"{line} {word}".strip()
            if font.getlength(trial) <= max_w:
                line = trial
                continue
            if line:
                lines.append(line)
            while font.getlength(word) > max_w and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and font.getlength(word[:cut]) > max_w:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _line_h(font) -> int:
    a, d = font.getmetrics()
    return int((a + d) * 1.08)


def _fit_text(text: str, fonts: FontLoader, max_w: float, max_h: float,
              start: int, bold: bool = False, smallest: int = 9) -> tuple:
    """(font, lines) for the largest size from start down that fits."""
    size = max(smallest, int(start))
    while True:
        font = fonts(size, bold)
        lines = wrap(text, font, max_w)
        if size <= smallest or len(lines) * _line_h(font) <= max_h:
            return font, lines
        size = max(smallest, int(size * 0.9))


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list, font, x: float, y: float,
                w: float, fill: str, align: str = "center") -> None:
    lh = _line_h(font)
    for i, line in enumerate(lines):
        lw = font.getlength(line)
        lx = x + (w - lw) / 2 if align == "center" else x
        draw.text((lx, y + i * lh), line, font=font, fill=fill)


def caption_block_height(text: str, fonts: FontLoader, width: int, base: int) -> int:
    """Height of a "Below the picture" caption band for this panel width."""
    if not text.strip():
        return 0
    pad = max(4, base // 2)
    font, lines = _fit_text(text, fonts, width - 2 * pad, 10_000, base)
    return len(lines) * _line_h(font) + 2 * pad


def draw_caption_box(img: Image.Image, text: str, fonts: FontLoader, where: str,
                     opts: dict, base: int) -> int:
    """A narration box across the top or bottom of the panel; returns how
    far it reaches in from that edge."""
    if not text.strip():
        return 0
    draw = ImageDraw.Draw(img)
    w, h = img.size
    pad = max(4, base // 2)
    inset = max(3, opts["border"])
    box_w = w - 2 * inset
    font, lines = _fit_text(text, fonts, box_w - 2 * pad, h * 0.45 - 2 * pad, base)
    box_h = len(lines) * _line_h(font) + 2 * pad
    y0 = inset if where == "top" else h - inset - box_h
    draw.rectangle((inset, y0, inset + box_w, y0 + box_h), fill=opts["caption_bg"],
                   outline=opts["border_color"], width=max(1, opts["border"] // 2 + 1))
    _draw_lines(draw, lines, font, inset + pad, y0 + pad, box_w - 2 * pad, opts["caption_fg"],
                align="left")
    return inset + box_h


def _overlap(a: tuple, b: tuple) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def draw_bubble(img: Image.Image, text: str, fonts: FontLoader, place: str,
                face: Optional[tuple], opts: dict, base: int,
                avoid_top: int = 0, avoid_bottom: int = 0) -> None:
    """A speech bubble in a corner, its tail pointing at the face's chin
    when the face box (panel pixels) is known, else toward the middle of
    the panel. "Auto" picks the corner that covers the face least, top
    corners first."""
    if not text.strip():
        return
    w, h = img.size
    max_w = w * 0.62
    font, lines = _fit_text(text, fonts, max_w * 0.80, h * 0.36, int(base * 1.05), bold=True)
    tw = max(font.getlength(line) for line in lines)
    th = len(lines) * _line_h(font)
    bw, bh = tw / 0.80 + base * 0.6, th / 0.74 + base * 0.4
    margin = max(6, opts["border"] + 4)

    def _spot(corner: str) -> tuple:
        x = margin if corner.endswith("left") else w - margin - bw
        y = margin + avoid_top if corner.startswith("Top") else h - margin - avoid_bottom - bh
        return max(margin, x), max(margin, y)

    corner = place if place in BUBBLE_PLACES[1:] else "Top left"
    if place == "Auto" and face is not None:
        # Covering a little of the face beats moving to the bottom.
        lower = 0.3 * (face[2] - face[0]) * (face[3] - face[1])
        corner = min(BUBBLE_PLACES[1:], key=lambda c: _overlap(
            (*_spot(c), _spot(c)[0] + bw, _spot(c)[1] + bh), face)
            + (lower if c.startswith("Bottom") else 0))
    x0, y0 = _spot(corner)
    cx, cy = x0 + bw / 2, y0 + bh / 2

    if face is not None:
        tx, ty = (face[0] + face[2]) / 2, face[3]
    else:
        tx, ty = w / 2, h / 2
    # Tail: from inside the bubble toward the target, ending a short way
    # past the bubble's edge (and short of the target).
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    # Distance from the center to the ellipse's edge in this direction.
    edge = 1 / math.hypot(ux / (bw / 2), uy / (bh / 2))
    length = min(max(0.0, dist - edge) * 0.8, max(bh * 0.55, base * 1.4))
    length = max(length, base * 0.8)
    tip = (cx + ux * (edge + length), cy + uy * (edge + length))
    half = min(bw, bh) * 0.14
    root = (cx + ux * edge * 0.6, cy + uy * edge * 0.6)
    tail = [(root[0] - uy * half, root[1] + ux * half), tip,
            (root[0] + uy * half, root[1] - ux * half)]

    stroke = max(2, int(base * 0.12))
    box = (x0, y0, x0 + bw, y0 + bh)
    # One shape (bubble + tail) as a mask, grown for the outline, so the
    # tail joins the bubble without a line across it.
    pad = stroke + 2
    ox = int(min(box[0], *(p[0] for p in tail))) - pad
    oy = int(min(box[1], *(p[1] for p in tail))) - pad
    mw = int(max(box[2], *(p[0] for p in tail))) + pad - ox
    mh = int(max(box[3], *(p[1] for p in tail))) + pad - oy
    mask = Image.new("L", (mw, mh), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy), fill=255)
    md.polygon([(x - ox, y - oy) for x, y in tail], fill=255)
    ring = mask.filter(ImageFilter.MaxFilter(2 * stroke + 1))
    img.paste(opts["bubble_fg"], (ox, oy), ring)
    img.paste(opts["bubble_bg"], (ox, oy), mask)
    _draw_lines(ImageDraw.Draw(img), lines, font, cx - tw / 2, cy - th / 2, tw,
                opts["bubble_fg"])


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _text_base(opts: dict, size: tuple) -> int:
    return max(10, int(min(size) / 13 * float(opts.get("text_scale", 1.0))))


def crop_view(crop: Optional[tuple], img_size: tuple, size: tuple) -> tuple:
    """(focus, zoom) for cover_crop that shows the crop box (fractions of
    the picture) in a panel of this size."""
    if not crop:
        return (0.5, 0.5), 1.0
    x0, y0, x1, y1 = crop
    iw, ih = img_size
    cw, ch = max(1.0, (x1 - x0) * iw), max(1.0, (y1 - y0) * ih)
    tw, th = size
    base = max(tw / iw, th / ih)
    want = max(tw / cw, th / ch)
    return ((x0 + x1) / 2, (y0 + y1) / 2), max(1.0, want / base)


def _panel_text(panel: dict, key: str) -> str:
    """A panel's caption or bubble text, or "" when its switch is off."""
    if not panel.get(f"{key}_on", True):
        return ""
    return panel.get(key, "") or ""


def render_panel(img: Image.Image, panel: dict, size: tuple, opts: dict,
                 fonts: FontLoader) -> Image.Image:
    """One panel at its slot size (inside the border): the picture cropped
    to fit, its caption (inside, or in a band below the picture) and its
    speech bubble."""
    # One text size for the whole page (render sets it from a normal panel).
    base = opts.get("_base") or _text_base(opts, size)
    place = _place(panel.get("caption_place") or opts["caption_place"])
    caption = _panel_text(panel, "caption")
    band = 0
    if caption.strip() and place == "Below the picture":
        band = min(int(size[1] * 0.5), caption_block_height(caption, fonts, size[0], base))
    pic_size = (size[0], max(20, size[1] - band))
    if panel.get("crop"):
        focus, zoom = crop_view(panel["crop"], img.size, pic_size)
    else:
        focus = panel.get("focus") or (0.5, 0.5)
        zoom = float(panel.get("zoom", 1.0) or 1.0)
    pic = cover_crop(img, pic_size, focus, zoom)
    avoid_top = avoid_bottom = 0
    if caption.strip() and place.startswith("Inside"):
        where = "top" if place.endswith("top") else "bottom"
        used = draw_caption_box(pic, caption, fonts, where, opts, base)
        # The bubble sits clear of the caption box.
        if where == "top":
            avoid_top = used
        else:
            avoid_bottom = used
    face = None
    if panel.get("face") is not None:
        fx0, fy0, fx1, fy1 = panel["face"]
        ax, ay = _map_point((fx0, fy0), img.size, pic_size, focus, zoom)
        bx, by = _map_point((fx1, fy1), img.size, pic_size, focus, zoom)
        chin = ((ax + bx) / 2, by)
        if 0 <= chin[0] <= pic_size[0] and 0 <= chin[1] <= pic_size[1]:
            face = (ax, ay, bx, by)
    draw_bubble(pic, _panel_text(panel, "bubble"), fonts, panel.get("bubble_place", "Auto"),
                face, opts, base, avoid_top, avoid_bottom)
    if not band:
        return pic
    cell = Image.new("RGB", size, opts["page_bg"])
    cell.paste(pic, (0, 0))
    draw = ImageDraw.Draw(cell)
    border = max(1, int(opts["border"]))
    draw.rectangle((0, pic_size[1], size[0], pic_size[1] + border - 1),
                   fill=opts["border_color"])
    pad = max(4, base // 2)
    font, lines = _fit_text(caption, fonts, size[0] - 2 * pad, band - 2 * pad, base)
    text_h = len(lines) * _line_h(font)
    _draw_lines(draw, lines, font, pad, pic_size[1] + (band - text_h) / 2, size[0] - 2 * pad,
                opts.get("below_fg", opts["title_color"]))
    return cell


def layout_geometry(panels: list, opts: dict, fonts: FontLoader,
                    first_size: Optional[tuple]) -> dict:
    """Page size and each panel's box: {"size", "boxes": [(x, y, w, h)]
    (outer, border included), "title_font", "title_lines", "base"}."""
    gc, gr, rects, kx, ky = slots(len(panels), opts["layout"], opts["columns"],
                                  opts.get("vertical", False))
    cw, ch = panel_size(opts, first_size)
    g, b = int(opts["gutter"]), int(opts["border"])
    base = _text_base(opts, (cw, ch))
    # One grid unit's pitch: a normal panel is kx units wide, ky tall.
    unit_w = (cw + 2 * b + g) / kx
    unit_h = (ch + 2 * b + g) / ky
    page_w = int(round(gc * unit_w + g))
    title = opts.get("title", "").strip()
    title_font, title_lines, title_h = None, [], 0
    if title:
        title_font, title_lines = _fit_text(title, fonts, page_w - 2 * g, ch * 0.5,
                                            int(base * 1.8), bold=True)
        title_h = len(title_lines) * _line_h(title_font) + g
    page_h = int(round(title_h + gr * unit_h + g))
    boxes = []
    for x, y, w, h in rects:
        bx = int(round(g + x * unit_w))
        by = int(round(title_h + g + y * unit_h))
        boxes.append((bx, by, int(round(w * unit_w - g)), int(round(h * unit_h - g))))
    return dict(size=(page_w, page_h), boxes=boxes, base=base, title_font=title_font,
                title_lines=title_lines, title_h=title_h)


def _timeline(sequences: list) -> tuple:
    """(times in ms, step) for the output frames: the shortest panel delay
    (at least MIN_DELAY), long enough for the longest panel's loop, within
    MAX_SECONDS and MAX_FRAMES."""
    animated = [s for s in sequences if len(s) > 1]
    if not animated:
        return [0], 0
    step = max(MIN_DELAY, min(d for s in animated for _, d in s))
    total = min(MAX_SECONDS * 1000, max(sum(d for _, d in s) for s in animated))
    step = max(step, int(math.ceil(total / MAX_FRAMES)))
    n = max(1, int(round(total / step)))
    return [i * step for i in range(n)], step


def _index_at(seq: list, t: float) -> int:
    if len(seq) == 1:
        return 0
    total = sum(d for _, d in seq)
    t = t % total
    for i, (_, d) in enumerate(seq):
        if t < d:
            return i
        t -= d
    return len(seq) - 1


def render(panels: list, sequences: list, opts: Optional[dict] = None,
           fonts: Optional[FontLoader] = None,
           progress: Optional[Callable[[str], None]] = None) -> tuple:
    """The comic: (frames, durations in ms). One frame (duration 0) when
    every panel is a still.

    panels: [{"caption", "caption_on", "caption_place", "bubble", "bubble_on",
    "bubble_place", "crop": (x0, y0, x1, y1) fractions, "focus": (fx, fy),
    "zoom", "face": (x0, y0, x1, y1) in source pixels}]
    sequences: for each panel, load_panel_frames()'s [(image, delay)].
    """
    if not panels or len(panels) != len(sequences):
        raise ValueError("need one picture for every panel")
    opts = dict(DEFAULTS, **(opts or {}))
    fonts = fonts or _default_font
    first_size = sequences[0][0][0].size
    geo = layout_geometry(panels, opts, fonts, first_size)
    opts["_base"] = geo["base"]
    b = int(opts["border"])

    page = Image.new("RGB", geo["size"], opts["page_bg"])
    draw = ImageDraw.Draw(page)
    if geo["title_lines"]:
        _draw_lines(draw, geo["title_lines"], geo["title_font"], opts["gutter"],
                    opts["gutter"] * 0.6, geo["size"][0] - 2 * opts["gutter"],
                    opts["title_color"])
    for x, y, w, h in geo["boxes"]:
        if b:
            draw.rectangle((x, y, x + w - 1, y + h - 1), fill=opts["border_color"])

    # Each panel's frames, rendered once.
    cells = []
    for i, (panel, seq, (_x, _y, w, h)) in enumerate(zip(panels, sequences, geo["boxes"])):
        if progress:
            progress(f"Drawing panel {i + 1} of {len(panels)}…")
        inner = (max(20, w - 2 * b), max(20, h - 2 * b))
        cells.append([render_panel(img, panel, inner, opts, fonts) for img, _ in seq])

    times, step = _timeline(sequences)
    frames, durs, last_key = [], [], None
    for t in times:
        key = tuple(_index_at(seq, t) for seq in sequences)
        if key == last_key:
            durs[-1] += step
            continue
        frame = page.copy()
        for (x, y, _w, _h), cell_frames, k in zip(geo["boxes"], cells, key):
            frame.paste(cell_frames[k], (x + b, y + b))
        frames.append(frame)
        durs.append(step)
        last_key = key
    return frames, durs
