"""Every picture/GIF used in a saved or exported caption, kept in
used_media.csv so Find Online can hide ones already used.

Lives next to the app (app_paths.BASE_DIR) like the other per-user files.
A picture counts as used when its source URL matches, or when its
fingerprint (a 64-bit difference hash of the first frame) is within a few
bits of a used one — so the same GIF re-hosted on another site, or opened
from disk, is still recognized. Every function swallows I/O errors: a
missing or unreadable log just means nothing is marked used.
"""

import csv
import datetime
import os
import threading
import urllib.parse
from typing import Optional

from PIL import Image

from app_paths import BASE_DIR

PATH = os.path.join(BASE_DIR, "used_media.csv")
FIELDS = ["date", "url", "found_on", "title", "batch", "output_file", "hash"]
# Bits (of 64) two fingerprints may differ by and still be the same picture:
# resizing/recompression flips a few; different pictures differ by ~20+.
MATCH_BITS = 6

_lock = threading.Lock()
_cache: dict = {"mtime": None, "urls": set(), "hashes": []}


def normalize_url(url: str) -> str:
    """Scheme, "www.", query and trailing slash don't make a different file
    (sex.com previews add ?width=N to the same picture)."""
    if not url:
        return ""
    p = urllib.parse.urlparse(url.strip())
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{p.path.rstrip('/')}"


def fingerprint(img: Image.Image) -> str:
    """Difference hash of a picture (its first frame for a GIF): 16 hex
    digits. Each bit says whether a pixel is brighter than its right-hand
    neighbour on a 9x8 grayscale shrink, so it survives resizing and
    re-encoding."""
    try:
        img.seek(0)
    except (AttributeError, EOFError, ValueError):
        pass
    small = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def _load() -> dict:
    try:
        mtime = os.path.getmtime(PATH)
    except OSError:
        return {"urls": set(), "hashes": []}
    with _lock:
        if _cache["mtime"] == mtime:
            return _cache
        urls, hashes = set(), []
        try:
            with open(PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("url"):
                        urls.add(normalize_url(row["url"]))
                    h = (row.get("hash") or "").strip()
                    if h:
                        try:
                            hashes.append(int(h, 16))
                        except ValueError:
                            pass
        except (OSError, csv.Error):
            return {"urls": set(), "hashes": []}
        _cache.update(mtime=mtime, urls=urls, hashes=hashes)
        return _cache


def is_used(url: Optional[str] = None, img: Optional[Image.Image] = None,
            hash_hex: Optional[str] = None) -> bool:
    data = _load()
    if url and normalize_url(url) in data["urls"]:
        return True
    if not data["hashes"]:
        return False
    if hash_hex is None and img is not None:
        hash_hex = fingerprint(img)
    if not hash_hex:
        return False
    h = int(hash_hex, 16)
    return any(bin(h ^ u).count("1") <= MATCH_BITS for u in data["hashes"])


def _upgrade_header() -> None:
    """Logs written before fingerprints existed lack the hash column;
    rewrite them with it (old rows keep an empty hash)."""
    try:
        with open(PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames == FIELDS:
                return
            rows = list(reader)
        with open(PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    except (OSError, csv.Error):
        pass


def record(url: Optional[str] = None, img: Optional[Image.Image] = None, *,
           found_on: str = "", title: str = "", batch: str = "app",
           output_file: str = "", hash_hex: Optional[str] = None) -> bool:
    """Log a used picture (by URL and/or fingerprint). Returns False when
    it was already logged, or nothing identifies it."""
    if hash_hex is None and img is not None:
        try:
            hash_hex = fingerprint(img)
        except Exception:
            hash_hex = None
    if not url and not hash_hex:
        return False
    data = _load()
    if (url and normalize_url(url) in data["urls"]) or (
            hash_hex and int(hash_hex, 16) in data["hashes"]):
        return False
    try:
        new = not os.path.exists(PATH)
        if not new:
            _upgrade_header()
        with open(PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow({"date": datetime.date.today().isoformat(), "url": url or "",
                        "found_on": found_on, "title": title, "batch": batch,
                        "output_file": output_file, "hash": hash_hex or ""})
    except OSError:
        return False
    with _lock:
        _cache["mtime"] = None   # re-read on the next check
    return True
