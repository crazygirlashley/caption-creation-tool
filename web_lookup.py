"""Online image/GIF lookup — search Barnorama + AcidCow and pull a source
file from a result.

Barnorama is a WordPress site with a real, working `?s=` search (unlike some
similar sites, e.g. izispicy.com, which only offer date/tag browsing — it
runs DataLife Engine with full-text search disabled server-side, and no
tag/category nav either, so there's no keyword-addressable path into its
content at all) — search hits are `<article class="item-list">` cards
linking to a post, and each post's `<div class="entry">` is just a stream of
`<img class="... size-full ...">` tags at full upload resolution, no JS
rendering needed to read either.

AcidCow also has a real search, despite first appearances — it's DataLife
Engine too, but its search form posts `story=<query>` (not `?s=`), which is
why a naive `?s=` probe on it looks broken. The real endpoint is a POST (or
equivalent GET) to `/index.php?do=search` with `do=search&subaction=search&
story=<query>&full_search=1&result_from=<(page-1)*10+1>&search_start=<page>`
— see _search_acidcow(). Results are `<div class="post">` cards; each post's
`#dle-content` is a stream of `<img>` tags whose `src` lives under
`cdn.acidcow.com/uploads/posts/...` (a handful of UI icons — like/dislike
buttons, an avatar placeholder — share the container and are filtered out by
that path check). Search results also expose a `catlist[]` category filter
that can exclude AcidCow's "Celebs" category server-side, but testing found
it doesn't actually change what matches (celebrity content showed up
un-tagged, under "Pics" too), so it isn't relied on — _is_celebrity_title()
does the real filtering, for both sites, after the fact.
"""

import collections
import os
import re
import tempfile
import urllib.parse

import requests
from bs4 import BeautifulSoup

import app_paths

_BARNORAMA_BASE = "https://www.barnorama.com"
_ACIDCOW_BASE = "https://acidcow.com"
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

# Barnorama titles are all rendered in Title Case, so capitalization can't
# distinguish a real name from an ordinary headline word — "The Girls Turning
# an Ordinary Wednesday" is every bit as capitalized as "Emily Ratajkowski
# Exposes...". A hand-curated stoplist of "generic headline vocabulary" was
# tried first and false-positived on ordinary site content its sample hadn't
# covered (e.g. flagged "40 Times Actors Transformed Themselves Beyond
# Recognition" as a name, because "Recognition" wasn't in the list). Real
# dictionaries fix that: _CELEBRITY_STOPWORDS is a ~370k-word English
# wordlist (assets/english_words.txt) and _CELEBRITY_GIVEN_NAMES is real
# US given-name data filtered to reasonably common names (assets/
# given_names.txt) — see _load_word_set(). _is_celebrity_title() flags a
# title on either of two signals:
#   1. any word of 3+ letters that isn't in the English dictionary at all —
#      catches distinctive surnames like "Ratajkowski" or "Qualley" outright.
#   2. a recognized given name immediately followed by another non-stopword
#      word — catches "<First> <Last>..." pairs where both names happen to
#      also be ordinary dictionary words (e.g. "Dakota Johnson", "Maya Jama").
# Verified against a live sample of 39 real Barnorama titles (both celebrity
# posts and generic listicles): 11/12 recall, 0 false positives. The one
# miss was "Ice Spice" — a title using only mononym/stage names, which
# aren't in a given-names dataset and aren't distinctive enough to be
# missing from the dictionary either.
#
# AcidCow surfaced a third case neither signal above catches: compilation
# posts like "Famous Actresses At The Beginning Of Their Careers" or
# "Childhood Photos Of Celebrities" — real photos of real celebrities, but
# the title names no one specifically, so there's no name pair to find. A
# plain topic-word check (_CELEBRITY_TOPIC_WORDS) covers this a lot more
# reliably than either wordlist signal, at the cost of also dropping
# Barnorama's "17 Celebrities That Were A Proper Challenge For These
# Average Folks" — a post that isn't actually about real celebrities, but
# does *say* "celebrities", which is what was asked to be filtered.
_CELEBRITY_TOPIC_WORDS = {
    "celebrity", "celebrities", "celeb", "celebs", "famous", "hollywood",
    "actress", "actresses",
}
_CELEBRITY_NAME_AMBIGUOUS = {"will", "may", "mark", "art", "grant"}
_CELEBRITY_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "so", "nor", "for", "yet", "of",
    "in", "on", "at", "to", "from", "with", "without", "into", "onto",
    "out", "over", "under", "up", "down", "off", "near", "before", "after",
    "again", "further", "once", "here", "there", "why", "how", "both",
    "each", "few", "more", "most", "other", "some", "such", "only", "own",
    "same", "too", "very", "can", "will", "just", "should", "now", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "having", "do", "does", "did", "doing", "this", "that", "these",
    "those", "who", "whom", "which", "what", "when", "where", "while",
    "as", "if", "than", "then", "because", "until", "no", "not",
    "nothing", "all", "any", "her", "his", "its", "my", "your", "our",
    "their", "them", "they", "he", "she", "it", "i", "we", "you", "me",
    "him", "us", "itself", "themselves", "every", "never", "may", "mark",
}


def _load_word_set(filename: str) -> frozenset:
    path = os.path.join(app_paths.RESOURCE_DIR, "assets", filename)
    try:
        with open(path, encoding="utf-8") as f:
            return frozenset(line.strip().lower() for line in f if line.strip())
    except OSError:
        return frozenset()


_CELEBRITY_DICTIONARY = _load_word_set("english_words.txt")
_CELEBRITY_GIVEN_NAMES = (
    _load_word_set("given_names.txt") - _CELEBRITY_NAME_AMBIGUOUS
)
_CELEBRITY_BRAND_EXCLUDE = {"barno", "nyfw", "weblinks", "selfie"}


def _is_celebrity_title(title: str) -> bool:
    """True if title looks like it names a real person — see the block
    comment above _CELEBRITY_NAME_AMBIGUOUS for the two signals used. If
    the bundled wordlists failed to load, this always returns False rather
    than filtering every result."""
    if not _CELEBRITY_DICTIONARY:
        return False
    words = re.findall(r"[A-Za-z]+", title)
    lowered = [w.lower() for w in words]

    if any(lw in _CELEBRITY_TOPIC_WORDS for lw in lowered):
        return True

    for lw in lowered:
        if (len(lw) >= 3 and lw not in _CELEBRITY_DICTIONARY
                and lw not in _CELEBRITY_BRAND_EXCLUDE):
            return True

    for lw, lw2 in zip(lowered, lowered[1:]):
        if (lw in _CELEBRITY_GIVEN_NAMES and len(lw2) >= 2
                and lw2 not in _CELEBRITY_STOPWORDS):
            return True

    return False


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


def _search_barnorama(query: str, page: int) -> list:
    url = f"{_BARNORAMA_BASE}/page/{page}/" if page > 1 else f"{_BARNORAMA_BASE}/"
    resp = requests.get(url, params={"s": query}, headers={"User-Agent": _UA},
                        timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for art in soup.select("article.item-list"):
        title_a = art.select_one("h2.post-box-title a")
        if not title_a or not title_a.get("href"):
            continue
        title = title_a.get_text(strip=True) or "(untitled)"
        if _is_celebrity_title(title):
            continue
        thumb_img = art.select_one(".post-thumbnail img")
        results.append({
            "title": title,
            "url": title_a["href"],
            "thumb_url": _smallest_srcset_url(thumb_img) if thumb_img else "",
        })
    return results


# AcidCow's search form (id="fullsearch") submits these fields via POST to
# /index.php?do=search — see the module docstring. catlist[]=0 means "all
# categories"; every other field just reproduces the form's own defaults
# (whole-time, sort by date descending, search post bodies not just titles).
def _search_acidcow(query: str, page: int) -> list:
    data = {
        "do": "search",
        "subaction": "search",
        "story": query,
        "full_search": "1",
        "result_from": str((page - 1) * 10 + 1),
        "search_start": str(page),
        "titleonly": "0",
        "showposts": "0",
        "searchdate": "0",
        "beforeafter": "after",
        "sortby": "date",
        "resorder": "desc",
        "replyless": "0",
        "replylimit": "0",
        "catlist[]": "0",
    }
    resp = requests.post(f"{_ACIDCOW_BASE}/index.php", params={"do": "search"},
                          data=data, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for post in soup.select("div.post"):
        title_a = post.select_one("h1 a")
        if not title_a or not title_a.get("href"):
            continue
        title = title_a.get_text(strip=True) or "(untitled)"
        if _is_celebrity_title(title):
            continue
        thumb_img = post.select_one("img")
        results.append({
            "title": title,
            "url": title_a["href"],
            "thumb_url": thumb_img.get("src", "") if thumb_img else "",
        })
    return results


def search(query: str, page: int = 1) -> list:
    """Return [{"title", "url", "thumb_url"}, ...] merging one search
    results page each from Barnorama and AcidCow (page N of each,
    concatenated — the two sites paginate independently, so "page 2" isn't
    a single ranked list across both, just each site's own next page).
    Empty for a source on no results (or a request/parse failure — callers
    can't tell the difference, which is fine here: both just mean "nothing
    to show" for that source), and a failing source never blocks the other
    one's results. Results whose title names or references a real person
    (see _is_celebrity_title) are dropped rather than returned."""
    query = query.strip()
    if not query:
        return []
    results = []
    for source in (_search_barnorama, _search_acidcow):
        try:
            results.extend(source(query, page))
        except requests.RequestException:
            pass
    return results


def _fetch_gallery_barnorama(post_url: str) -> list:
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


def _fetch_gallery_acidcow(post_url: str) -> list:
    """Content images live on the cdn.acidcow.com subdomain; the like/
    dislike icons and avatar placeholder sharing #dle-content are on the
    main domain or a template-relative path, so a netloc check separates
    them cleanly. (Tried matching the "/uploads/posts/" path segment first
    — some posts use a different path, e.g. "/pics/<date>/<id>_<hash>.jpg",
    so that missed real galleries entirely.)"""
    resp = requests.get(post_url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    entry = soup.select_one("#dle-content")
    if entry is None:
        return []

    urls, seen = [], set()
    for img in entry.select("img"):
        src = img.get("src", "")
        if urllib.parse.urlparse(src).netloc == "cdn.acidcow.com" and src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


def fetch_gallery_images(post_url: str) -> list:
    """Return the full-resolution image/GIF URLs embedded in a post, in the
    order they appear on the page. Dispatches on post_url's host, since
    Barnorama and AcidCow need different selectors."""
    host = urllib.parse.urlparse(post_url).netloc
    if "acidcow.com" in host:
        return _fetch_gallery_acidcow(post_url)
    return _fetch_gallery_barnorama(post_url)


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
