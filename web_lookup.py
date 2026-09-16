"""Online image/GIF lookup — search Barnorama and pull a source file from a result.

Barnorama is a WordPress site with a real, working `?s=` search (unlike some
similar sites, e.g. izispicy.com, which only offer date/tag browsing) — search
hits are `<article class="item-list">` cards linking to a post, and each post's
`<div class="entry">` is just a stream of `<img class="... size-full ...">`
tags at full upload resolution, no JS rendering needed to read either.
"""

import collections
import os
import re
import tempfile
import urllib.parse

import requests
from bs4 import BeautifulSoup

_BASE = "https://www.barnorama.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CaptionCreator/1.0"
_TIMEOUT = 15
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")

# Word lists for the physical/visual attributes that actually narrow an
# image search on a photo site — a caption's plot nouns ("genie", "lamp",
# "wish") don't correspond to anything a site's search index tags photos
# with, so extract_keywords() below deliberately ignores everything outside
# these categories rather than doing generic keyword frequency over the
# whole text. Order here is also the order categories appear in the query.
_GENDER_WORDS = {
    "man", "men", "male", "guy", "guys", "boy", "boys",
    "woman", "women", "female", "girl", "girls", "lady", "ladies",
}
_HAIR_COLOR_WORDS = {
    "blonde", "blond", "brunette", "redhead", "auburn", "ginger",
    "platinum", "silver", "brown", "black", "red", "gray", "grey",
    "dark", "raven", "chestnut",
}
_BODY_TYPE_WORDS = {
    "slender", "curvy", "curvaceous", "athletic", "petite", "muscular",
    "slim", "thin", "chubby", "voluptuous", "toned", "fit", "stocky",
    "lean", "busty", "skinny", "tall", "short", "hourglass", "plump",
}
_AGE_WORDS = {
    # Deliberately excludes "old" — transformation captions routinely say
    # "his old body"/"his old self" meaning *former*, not elderly, which
    # made every such caption falsely pick up an age keyword it didn't mean.
    "young", "teen", "teenage", "teenager", "elderly", "mature",
    "youthful", "adult", "senior",
}
_CLOTHING_WORDS = {
    "dress", "skirt", "jeans", "shirt", "blouse", "lingerie", "bikini",
    "swimsuit", "gown", "heels", "stockings", "leggings", "shorts",
    "top", "bra", "panties", "corset", "uniform", "costume", "lace",
    "nightgown", "robe", "jacket", "sweater", "pants", "suit",
}
_ATTRIBUTE_CATEGORIES = (
    _GENDER_WORDS, _HAIR_COLOR_WORDS, _BODY_TYPE_WORDS, _AGE_WORDS,
    _CLOTHING_WORDS,
)


def extract_keywords(text: str, max_words: int = 6) -> str:
    """Pull a short, image-search-scoped query out of a caption: one word
    per attribute category (gender, hair color, body type, age, clothing)
    — whichever member of that category's word list occurs most often in
    the text, ties broken by first appearance. A category with no match in
    the text is skipped entirely rather than padded with an unrelated
    word, so a caption with few physical descriptors yields a shorter (or
    empty) query instead of one diluted by story/plot nouns."""
    words = re.findall(r"[a-z']+", text.lower())
    counts = collections.Counter(words)

    picked = []
    for category in _ATTRIBUTE_CATEGORIES:
        candidates = [w for w in category if counts[w]]
        if not candidates:
            continue
        candidates.sort(key=lambda w: (-counts[w], words.index(w)))
        picked.append(candidates[0])

    return " ".join(picked[:max_words])


def _smallest_srcset_url(img_tag) -> str:
    """Prefer the smallest `srcset` candidate for a thumbnail — the bare
    `src` on a search-results page is the full 660px-wide image, several
    times the bytes of the thumbnail we actually display."""
    srcset = img_tag.get("srcset")
    if not srcset:
        return img_tag.get("src", "")
    candidates = []
    for part in srcset.split(","):
        bits = part.strip().rsplit(" ", 1)
        if len(bits) == 2 and bits[1].endswith("w"):
            try:
                candidates.append((int(bits[1][:-1]), bits[0]))
            except ValueError:
                continue
    if not candidates:
        return img_tag.get("src", "")
    return min(candidates, key=lambda c: c[0])[1]


def search(query: str, page: int = 1) -> list:
    """Return [{"title", "url", "thumb_url"}, ...] for a Barnorama search
    results page. Empty list on no results (or a request/parse failure —
    callers can't tell the difference, which is fine here: both just mean
    "nothing to show")."""
    query = query.strip()
    if not query:
        return []
    url = f"{_BASE}/page/{page}/" if page > 1 else f"{_BASE}/"
    resp = requests.get(url, params={"s": query}, headers={"User-Agent": _UA},
                        timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for art in soup.select("article.item-list"):
        title_a = art.select_one("h2.post-box-title a")
        if not title_a or not title_a.get("href"):
            continue
        thumb_img = art.select_one(".post-thumbnail img")
        results.append({
            "title": title_a.get_text(strip=True) or "(untitled)",
            "url": title_a["href"],
            "thumb_url": _smallest_srcset_url(thumb_img) if thumb_img else "",
        })
    return results


def fetch_gallery_images(post_url: str) -> list:
    """Return the full-resolution image/GIF URLs embedded in a post, in the
    order they appear on the page."""
    resp = requests.get(post_url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    entry = soup.select_one("div.entry")
    if entry is None:
        return []

    urls, seen = [], set()
    for img in entry.select("img.size-full"):
        src = img.get("src")
        if src and src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


def download_bytes(url: str) -> bytes:
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def download_to_tempfile(url: str) -> str:
    """Download url's bytes to a new temp file named with the URL's own
    extension (falls back to .jpg for anything unrecognized), so callers can
    hand the path straight to Image.open()/imageio without guessing format."""
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if ext not in _IMG_EXTS:
        ext = ".jpg"
    data = download_bytes(url)
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return tmp.name
