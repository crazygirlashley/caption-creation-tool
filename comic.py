"""Comic strip builder: several pictures laid out as comic panels, each with
an optional narration caption and speech bubble, plus an optional title.

Pure Pillow (no Tk) so it can run off the UI thread and be tested. A GIF or
animated WebP panel keeps playing: the strip then animates, every panel
looping on its own timing (see render). The app hands in a font loader so
captions use the same fonts as everywhere else.
"""

import math
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence

# Panel arrangement.
LAYOUTS = ("Row", "Column", "Grid")
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
CAPTION_PLACES = ("Inside, top", "Inside, bottom", "Below the panel")
BUBBLE_PLACES = ("Auto", "Top left", "Top right", "Bottom left", "Bottom right")

DEFAULTS = {
    "layout": "Row",
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

def grid_shape(n: int, layout: str, columns: int = 2) -> tuple:
    """(columns, rows) for n panels."""
    n = max(1, n)
    if layout == "Column":
        return 1, n
    if layout == "Grid":
        cols = max(1, min(n, int(columns)))
        return cols, math.ceil(n / cols)
    return n, 1


def panel_size(opts: dict, first_size: Optional[tuple]) -> tuple:
    w = max(80, int(opts["panel_width"]))
    ratio = SHAPES.get(opts["shape"], 1.0)
    if ratio is None:
        ratio = (first_size[0] / first_size[1]) if first_size and first_size[1] else 1.0
    ratio = max(0.3, min(3.5, ratio))
    return w, max(60, int(round(w / ratio)))


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
    """Height of a "Below the panel" caption for this panel width."""
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


def render_panel(img: Image.Image, panel: dict, size: tuple, opts: dict,
                 fonts: FontLoader) -> Image.Image:
    """One panel picture: cropped to the panel, with its inside caption and
    speech bubble."""
    focus = panel.get("focus") or (0.5, 0.5)
    zoom = float(panel.get("zoom", 1.0) or 1.0)
    cell = cover_crop(img, size, focus, zoom)
    base = _text_base(opts, size)
    place = panel.get("caption_place") or opts["caption_place"]
    caption = panel.get("caption", "")
    avoid_top = avoid_bottom = 0
    if caption.strip() and place.startswith("Inside"):
        where = "top" if place.endswith("top") else "bottom"
        used = draw_caption_box(cell, caption, fonts, where, opts, base)
        # The bubble sits clear of the caption box.
        if where == "top":
            avoid_top = used
        else:
            avoid_bottom = used
    face = None
    if panel.get("face") is not None:
        fx0, fy0, fx1, fy1 = panel["face"]
        ax, ay = _map_point((fx0, fy0), img.size, size, focus, zoom)
        bx, by = _map_point((fx1, fy1), img.size, size, focus, zoom)
        chin = ((ax + bx) / 2, by)
        if 0 <= chin[0] <= size[0] and 0 <= chin[1] <= size[1]:
            face = (ax, ay, bx, by)
    draw_bubble(cell, panel.get("bubble", ""), fonts, panel.get("bubble_place", "Auto"),
                face, opts, base, avoid_top, avoid_bottom)
    return cell


def layout_geometry(panels: list, opts: dict, fonts: FontLoader,
                    first_size: Optional[tuple]) -> dict:
    """Sizes and positions: {"size", "cell", "below", "title", "title_h",
    "spots": [(x, y)]}."""
    cols, rows = grid_shape(len(panels), opts["layout"], opts["columns"])
    cw, ch = panel_size(opts, first_size)
    g, b = int(opts["gutter"]), int(opts["border"])
    base = _text_base(opts, (cw, ch))
    below = 0
    if opts["caption_place"] == "Below the panel" or any(
            p.get("caption_place") == "Below the panel" for p in panels):
        below = max((caption_block_height(p.get("caption", ""), fonts, cw, base)
                     for p in panels
                     if (p.get("caption_place") or opts["caption_place"]) == "Below the panel"),
                    default=0)
    title = opts.get("title", "").strip()
    page_w = cols * (cw + 2 * b) + (cols + 1) * g
    title_font, title_lines, title_h = None, [], 0
    if title:
        title_font, title_lines = _fit_text(title, fonts, page_w - 2 * g, ch * 0.5,
                                            int(base * 1.8), bold=True)
        title_h = len(title_lines) * _line_h(title_font) + g
    cell_h = ch + 2 * b + below
    page_h = title_h + rows * cell_h + (rows + 1) * g
    spots = []
    for i in range(len(panels)):
        r, c = divmod(i, cols)
        spots.append((g + c * (cw + 2 * b + g), title_h + g + r * (cell_h + g)))
    # Center a short last row of a grid.
    last = len(panels) % cols
    if opts["layout"] == "Grid" and last and rows > 1:
        shift = (cols - last) * (cw + 2 * b + g) // 2
        spots[-last:] = [(x + shift, y) for x, y in spots[-last:]]
    return dict(size=(page_w, page_h), cell=(cw, ch), below=below, base=base,
                title_font=title_font, title_lines=title_lines, title_h=title_h, spots=spots)


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
    """The strip: (frames, durations in ms). One frame (duration 0) when
    every panel is a still.

    panels: [{"caption", "caption_place", "bubble", "bubble_place",
    "focus": (fx, fy), "zoom", "face": (x0, y0, x1, y1) in source pixels}]
    sequences: for each panel, load_panel_frames()'s [(image, delay)].
    """
    if not panels or len(panels) != len(sequences):
        raise ValueError("need one picture for every panel")
    opts = dict(DEFAULTS, **(opts or {}))
    fonts = fonts or _default_font
    first_size = sequences[0][0][0].size
    geo = layout_geometry(panels, opts, fonts, first_size)
    cw, ch = geo["cell"]
    b = int(opts["border"])

    page = Image.new("RGB", geo["size"], opts["page_bg"])
    draw = ImageDraw.Draw(page)
    if geo["title_lines"]:
        _draw_lines(draw, geo["title_lines"], geo["title_font"], opts["gutter"],
                    opts["gutter"] * 0.6, geo["size"][0] - 2 * opts["gutter"],
                    opts["title_color"])
    for i, (panel, (x, y)) in enumerate(zip(panels, geo["spots"])):
        if b:
            draw.rectangle((x, y, x + cw + 2 * b - 1, y + ch + 2 * b - 1),
                           fill=opts["border_color"])
        place = panel.get("caption_place") or opts["caption_place"]
        if geo["below"] and place == "Below the panel" and panel.get("caption", "").strip():
            pad = max(4, geo["base"] // 2)
            font, lines = _fit_text(panel["caption"], fonts, cw - 2 * pad, 10_000, geo["base"])
            _draw_lines(draw, lines, font, x + b + pad, y + ch + 2 * b + pad, cw - 2 * pad,
                        opts.get("below_fg", opts["title_color"]))

    # Each panel's frames, rendered once.
    cells = []
    for i, (panel, seq) in enumerate(zip(panels, sequences)):
        if progress:
            progress(f"Drawing panel {i + 1} of {len(panels)}…")
        p = dict(panel)
        if (p.get("caption_place") or opts["caption_place"]) == "Below the panel":
            p["caption"] = ""
        cells.append([render_panel(img, p, (cw, ch), opts, fonts) for img, _ in seq])

    times, step = _timeline(sequences)
    frames, durs, last_key = [], [], None
    for t in times:
        key = tuple(_index_at(seq, t) for seq in sequences)
        if key == last_key:
            durs[-1] += step
            continue
        frame = page.copy()
        for (x, y), cell_frames, k in zip(geo["spots"], cells, key):
            frame.paste(cell_frames[k], (x + b, y + b))
        frames.append(frame)
        durs.append(step)
        last_key = key
    return frames, durs
