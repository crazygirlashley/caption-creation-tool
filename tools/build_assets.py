"""Rebuild the built-in theme backgrounds in themes/ from their source art.

Usage:  python tools/build_assets.py [hypno] [pink_pill] [goth] [french_maid] [sissy]
(no names = all). Writes to themes/, plus a preview PNG of each into
tools/theme_art_src/previews/.

The source images are not in the repo (tools/theme_art_src/ is gitignored).
Where each one came from and its license is in themes/CREDITS.md; download
them into these paths to rebuild:

    theme_art_src/dl_hypno/MagicSpiral.gif     Hypno      (Wikimedia Commons)
    theme_art_src/dl_cand/pill3|5|6.png        Pink Pill  (NIH BioArt 633308/633318/633323)
    theme_art_src/dl_cand/maid4.jpg            Goth       (Cooper Hewitt sidewall)
    theme_art_src/dl_maid/bow_ill.png          French Maid (rawpixel bow)
    theme_art_src/dl_maid/heart2.png           French Maid (rawpixel heart)
    theme_art_src/dl_sissy/heart.gif           Sissy      (Sparkling pink heart.gif)

Bimbo_kisses.gif was made separately and has no build step here. After
changing a background, bump the theme JSON's "version" so existing installs
pick it up.
"""
import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageSequence

HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.join(HERE, "theme_art_src")
OUT = os.path.join(os.path.dirname(HERE), "themes")
PREV = os.path.join(S, "previews")


def gradient_map(gray: np.ndarray, stops) -> np.ndarray:
    """stops: [(pos 0..255, (r, g, b)), ...] -> RGB uint8."""
    xs = [p for p, _ in stops]
    out = np.zeros(gray.shape + (3,), np.float32)
    for ch in range(3):
        out[..., ch] = np.interp(gray, xs, [c[ch] for _, c in stops])
    return out.clip(0, 255).astype(np.uint8)


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _save_anim(frames, path, colors=128, duration=130):
    pal = frames[0].quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(path, save_all=True, append_images=q[1:], duration=duration, loop=0,
              optimize=True, disposal=1)


def _paste_wrapped(layer, p, x, y, W, H, dx0=0, dy0=0):
    """Paste `p` centred on (x, y), wrapping round the edges so the tile is seamless."""
    for dx in (-W, 0, W):
        for dy in (-H, 0, H):
            px, py = int(x + dx - p.width / 2) + dx0, int(y + dy - p.height / 2) + dy0
            if px > W or py > H or px + p.width < 0 or py + p.height < 0:
                continue
            layer.alpha_composite(p, (max(px, 0), max(py, 0)), (max(0, -px), max(0, -py)))


def _dots(W, H, bg, dot, step=46, r=5):
    base = Image.new("RGBA", (W, H), hexrgb(bg) + (255,))
    d = ImageDraw.Draw(base)
    for row in range(H // step):
        for col in range(W // step):
            cx = col * step + (step / 2 if row % 2 else 0) + step / 4
            cy = row * step + step / 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=hexrgb(dot) + (255,))
    return base


# ---------------------------------------------------------------- Hypno
def hypno():
    g = Image.open(f"{S}/dl_hypno/MagicSpiral.gif")
    frames = []
    size = 560
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - size / 2, yy - size / 2) / (size / 2)
    vign = np.clip(1.15 - r * 0.55, 0.35, 1.0)[..., None]
    for f in ImageSequence.Iterator(g):
        f = f.convert("RGB").crop((140, 0, 500, 360)).resize((size, size), Image.LANCZOS)
        a = np.asarray(f).astype(np.float32)
        lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        rgb = gradient_map(lum, [(0, (12, 0, 12)), (60, (70, 6, 50)), (110, (235, 40, 160)),
                                 (165, (255, 60, 175)), (215, (30, 205, 245)), (255, (120, 245, 255))])
        rgb = (rgb.astype(np.float32) * vign).clip(0, 255).astype(np.uint8)
        frames.append(Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(0.6)))
    _save_anim(frames, f"{OUT}/Hypno_spiral.gif", colors=64, duration=110)
    frames[0].save(f"{PREV}/hypno.png")


# ---------------------------------------------------------------- Pink Pill
def _hue_shift(im: Image.Image, deg: float, sat: float = 1.0) -> Image.Image:
    alpha = im.getchannel("A")
    hsv = np.asarray(im.convert("RGB").convert("HSV")).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] + deg / 360 * 255) % 255
    hsv[..., 1] = np.clip(hsv[..., 1] * sat, 0, 255)
    out = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGBA")
    out.putalpha(alpha)
    return out


def pink_pill():
    d = f"{S}/dl_cand"
    pink = Image.open(f"{d}/pill6.png").convert("RGBA")
    green = Image.open(f"{d}/pill3.png").convert("RGBA")
    orange = Image.open(f"{d}/pill5.png").convert("RGBA")
    variants = [
        pink,
        _hue_shift(orange, -62, 1.0),          # orange -> hot pink/magenta
        _hue_shift(green, 45, 0.85),            # green -> medical teal
        _hue_shift(pink, 0, 0.55),              # pastel pink
    ]
    variants = [v.crop(v.getbbox()) for v in variants]
    W = H = 440
    rnd = random.Random(7)
    pills = []
    # Poisson-ish scatter with wraparound spacing.
    tries = 0
    while len(pills) < 22 and tries < 5000:
        tries += 1
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        ok = all(min(abs(x - px), W - abs(x - px)) ** 2 + min(abs(y - py), H - abs(y - py)) ** 2 > 78 ** 2
                 for px, py, *_ in pills)
        if ok:
            pills.append((x, y, rnd.choice(range(len(variants))), rnd.uniform(0, 360),
                          rnd.uniform(52, 76), rnd.uniform(0, 2 * math.pi)))
    bg = hexrgb("#ffd6ea")
    frames = []
    N = 14
    for i in range(N):
        fr = Image.new("RGBA", (W, H), bg + (255,))
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for x, y, vi, ang, length, ph in pills:
            wob = 9 * math.sin(2 * math.pi * i / N + ph)
            v = variants[vi]
            sc = length / max(v.size)
            p = v.resize((max(1, int(v.width * sc)), max(1, int(v.height * sc))), Image.LANCZOS)
            p = p.rotate(ang + wob, resample=Image.BICUBIC, expand=True)
            sh = Image.new("RGBA", p.size, (190, 60, 120, 0))
            sh.putalpha(p.getchannel("A").point(lambda a: a * 0.22))
            _paste_wrapped(layer, p, x, y, W, H)
            _paste_wrapped(shadow, sh, x, y, W, H, 3, 4)
        fr.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)))
        fr.alpha_composite(layer)
        frames.append(fr.convert("RGB"))
    _save_anim(frames, f"{OUT}/Pink_Pill_pills.gif", colors=160, duration=120)
    frames[0].save(f"{PREV}/pink_pill.png")


# ---------------------------------------------------------------- Goth (damask)
def goth():
    im = Image.open(f"{S}/dl_cand/maid4.jpg").convert("L")
    w, h = im.size
    im = im.crop((int(w * .04), int(h * .02), int(w * .96), int(h * .98)))
    a = np.asarray(im).astype(np.float32)
    rgb = gradient_map(a, [(0, (8, 4, 8)), (185, (14, 8, 14)), (200, (26, 10, 20)),
                           (215, (60, 14, 32)), (235, (92, 22, 44)), (255, (120, 34, 58))])
    out = Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(0.5))
    out = out.resize((out.width * 3 // 4, out.height * 3 // 4), Image.LANCZOS)
    out.save(f"{OUT}/Goth_damask.jpg", quality=88)
    out.save(f"{PREV}/goth.png")


# ------------------------------------------------- French Maid (black & white)
def _cutout(path: str) -> Image.Image:
    """Lift a sticker off its fake-checkerboard JPEG background: keep the
    saturated (colored) and dark (outline) pixels."""
    im = Image.open(path).convert("RGB")
    hsv = np.asarray(im.convert("HSV")).astype(np.float32)
    keep = ((hsv[..., 1] > 70) | (hsv[..., 2] < 90)).astype(np.uint8) * 255
    m = Image.fromarray(keep).filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
    out = im.convert("RGBA")
    out.putalpha(m)
    return out.crop(m.point(lambda v: 255 if v > 40 else 0).getbbox())


def _recolor(im: Image.Image, stops) -> Image.Image:
    a = np.asarray(im.convert("L")).astype(np.float32)
    a = (a - a.min()) / (np.ptp(a) + 1e-6) * 255
    out = Image.fromarray(gradient_map(a, stops)).convert("RGBA")
    out.putalpha(im.getchannel("A"))
    return out


def _sticker(im: Image.Image, border: int, rgb=(255, 255, 255)) -> Image.Image:
    """Die-cut sticker outline (white unless `rgb` is given) around a motif."""
    pad = border * 2
    big = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
    big.alpha_composite(im, (pad, pad))
    a = big.getchannel("A").point(lambda v: 255 if v > 60 else 0)
    a = a.filter(ImageFilter.MaxFilter(border * 2 + 1)).filter(ImageFilter.GaussianBlur(1))
    out = Image.new("RGBA", big.size, rgb + (0,))
    out.putalpha(a)
    out.alpha_composite(big)
    return out.crop(out.getbbox())


def _scatter_anim(motifs, W, H, count, seed, base, shadow_rgb, frames_n=12, wobble=7):
    """Seamless tile of wobbling stickers over `base`; returns RGB frames."""
    rnd = random.Random(seed)
    items, tries = [], 0
    while len(items) < count and tries < 8000:
        tries += 1
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        mi = len(items) % len(motifs)
        size = motifs[mi][1] * rnd.uniform(0.85, 1.1)
        if all(min(abs(x - px), W - abs(x - px)) ** 2 + min(abs(y - py), H - abs(y - py)) ** 2
               > ((size + ps) * 0.62) ** 2 for px, py, _m, ps, *_ in items):
            items.append((x, y, mi, size, rnd.uniform(-25, 25), rnd.uniform(0, 2 * math.pi)))
    frames = []
    for i in range(frames_n):
        fr = base.copy()
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for x, y, mi, size, ang, ph in items:
            m = motifs[mi][0]
            sc = size / max(m.size)
            p = m.resize((max(1, int(m.width * sc)), max(1, int(m.height * sc))), Image.LANCZOS)
            p = p.rotate(ang + wobble * math.sin(2 * math.pi * i / frames_n + ph),
                         resample=Image.BICUBIC, expand=True)
            sh = Image.new("RGBA", p.size, shadow_rgb + (0,))
            sh.putalpha(p.getchannel("A").point(lambda v: v * 0.3))
            _paste_wrapped(layer, p, x, y, W, H)
            _paste_wrapped(shadow, sh, x, y, W, H, 3, 4)
        fr.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)))
        fr.alpha_composite(layer)
        frames.append(fr.convert("RGB"))
    return frames


def french_maid():
    d = f"{S}/dl_maid"
    bow = _cutout(f"{d}/bow_ill.png")
    bow.thumbnail((240, 240), Image.LANCZOS)
    heart = _cutout(f"{d}/heart2.png")
    heart.thumbnail((200, 200), Image.LANCZOS)
    black_bow = _recolor(bow, [(0, (6, 6, 8)), (90, (28, 28, 30)), (190, (85, 85, 90)), (255, (160, 160, 165))])
    # A white satin bow with black linework, like an apron ribbon.
    white_bow = _recolor(bow, [(0, (10, 10, 12)), (40, (30, 30, 34)), (85, (228, 228, 232)), (255, (255, 255, 255))])
    black_heart = _recolor(heart, [(0, (8, 8, 10)), (160, (40, 40, 44)), (255, (90, 90, 96))])
    ink = (18, 18, 20)
    motifs = [(_sticker(black_bow, 5), 92), (_sticker(white_bow, 3, ink), 86),
              (_sticker(black_heart, 4), 44), (_sticker(black_bow, 5), 70),
              (_sticker(white_bow, 3, ink), 66), (_sticker(black_heart, 4), 38)]
    W = H = 460
    base = _dots(W, H, "#ffffff", "#141416", r=4)
    frames = _scatter_anim(motifs, W, H, 15, 33, base, (0, 0, 0))
    _save_anim(frames, f"{OUT}/French_Maid_bows.gif", colors=64)
    frames[0].save(f"{PREV}/french_maid.png")


# ---------------------------------------------------- Sissy (purple glitter hearts)
def _tone(im: Image.Image, hue_deg: float, sat: float, val: float) -> Image.Image:
    """Set every pixel's hue (keeps glitter highlights white)."""
    alpha = im.getchannel("A")
    hsv = np.asarray(im.convert("RGB").convert("HSV")).astype(np.float32)
    hsv[..., 0] = hue_deg / 360 * 255
    hsv[..., 1] = np.clip(hsv[..., 1] * sat, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * val, 0, 255)
    out = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGBA")
    out.putalpha(alpha)
    return out


def _twinkle(size: int, alpha: float) -> Image.Image:
    """A soft white four-point star."""
    s = size * 4
    im = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(im)
    c, w = s / 2, s * 0.07
    d.polygon([(c, 0), (c + w, c - w), (s, c), (c + w, c + w), (c, s), (c - w, c + w), (0, c),
               (c - w, c - w)], fill=255)
    im = im.filter(ImageFilter.GaussianBlur(s * 0.015)).resize((size, size), Image.LANCZOS)
    glow = im.filter(ImageFilter.GaussianBlur(size * 0.12))
    a = ImageChops.lighter(im, glow.point(lambda v: v * 0.8)).point(lambda v: v * alpha)
    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    out.putalpha(a)
    return out


def sissy():
    src = [f.convert("RGBA") for f in ImageSequence.Iterator(Image.open(f"{S}/dl_sissy/heart.gif"))]
    bbox = src[0].getchannel("A").getbbox()
    src = [f.crop(bbox) for f in src]
    tones = [(275, 1.0, 0.78), (290, 0.85, 1.0), (268, 0.55, 1.08)]   # violet, orchid, lilac
    hearts = [[_tone(f, *t) for f in src] for t in tones]
    W = H = 440
    rnd = random.Random(8)
    items, tries = [], 0
    sizes = (96, 78, 62, 50)
    while len(items) < 13 and tries < 9000:
        tries += 1
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        size = sizes[len(items) % len(sizes)] * rnd.uniform(0.9, 1.1)
        if all(min(abs(x - px), W - abs(x - px)) ** 2 + min(abs(y - py), H - abs(y - py)) ** 2
               > ((size + ps) * 0.6) ** 2 for px, py, ps, *_ in items):
            items.append((x, y, size, rnd.randrange(3), rnd.uniform(-22, 22), rnd.randrange(3)))
    stars, tries = [], 0
    while len(stars) < 16 and tries < 9000:
        tries += 1
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        if all(min(abs(x - px), W - abs(x - px)) ** 2 + min(abs(y - py), H - abs(y - py)) ** 2
               > ((ps * 0.5 + 12) ** 2) for px, py, ps, *_ in items):
            stars.append((x, y, rnd.choice((14, 18, 24)), rnd.uniform(0, 2 * math.pi)))
    base = _dots(W, H, "#ead6f1", "#f4e8f8", step=40, r=3)
    N = 12
    frames = []
    for i in range(N):
        fr = base.copy()
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for x, y, size, tone, ang, ph in items:
            f = hearts[tone][(i // 2 + ph) % 3]          # glitter twinkle
            sc = size / max(f.size)
            p = f.resize((max(1, int(f.width * sc)), max(1, int(f.height * sc))), Image.LANCZOS)
            p = p.rotate(ang + 4 * math.sin(2 * math.pi * i / N + ph), resample=Image.BICUBIC,
                         expand=True)
            sh = Image.new("RGBA", p.size, (90, 40, 120, 0))
            sh.putalpha(p.getchannel("A").point(lambda v: v * 0.28))
            _paste_wrapped(layer, p, x, y, W, H)
            _paste_wrapped(shadow, sh, x, y, W, H, 2, 3)
        for x, y, size, ph in stars:
            a = max(0.0, math.sin(2 * math.pi * i / N + ph)) ** 1.5
            if a > 0.05:
                _paste_wrapped(layer, _twinkle(size, a), x, y, W, H)
        fr.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)))
        fr.alpha_composite(layer)
        frames.append(fr.convert("RGB"))
    _save_anim(frames, f"{OUT}/Sissy_hearts.gif", colors=128, duration=120)
    frames[0].save(f"{PREV}/sissy.png")


BUILDERS = {"hypno": hypno, "pink_pill": pink_pill, "goth": goth,
            "french_maid": french_maid, "sissy": sissy}

if __name__ == "__main__":
    names = sys.argv[1:] or list(BUILDERS)
    unknown = [n for n in names if n not in BUILDERS]
    if unknown:
        sys.exit(f"unknown background(s): {', '.join(unknown)}; choose from {', '.join(BUILDERS)}")
    os.makedirs(PREV, exist_ok=True)
    for n in names:
        BUILDERS[n]()
        print("built", n)
