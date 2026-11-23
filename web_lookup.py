"""Online image/GIF lookup — search Barnorama + AcidCow (and, with Mature
on, the explicit sites sex.com, xgroovy.com and porngifs.com) and pull a
source file from a result.

Tuned for what Find Online is used for — photos of one attractive person:
both sources are searched in parallel (results stream in per source),
multi-word queries also search their most distinctive term, ranking
prefers person galleries over memes/listicles, titles naming celebrities or
referencing minors are dropped, and each preview gets a face count so the
UI can show single-person photos only (see count_faces).

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
import concurrent.futures
import io
import os
import re
import tempfile
import threading
import urllib.parse
from typing import Optional

import requests
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app_paths
import content_rules

_BARNORAMA_BASE = "https://www.barnorama.com"
_ACIDCOW_BASE = "https://acidcow.com"
# sex.com: its pages render client-side, but the JSON search API they call
# (/portal/api/{pictures,gifs}/search — allowed by its robots.txt) returns
# each pin's title and image path; files are served from cdn.sex.com, which
# also resizes on request (?width=N) for previews.
_SEXCOM_API = "https://www.sex.com/portal/api/{kind}/search"
_SEXCOM_CDN = "https://cdn.sex.com"
_SEXCOM_PAGE_SIZE = 40
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CaptionCreator/1.0"
_TIMEOUT = 15
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")

# One pooled, retrying session for everything — searches, galleries and the
# many thumbnail downloads run concurrently, and reusing TLS connections to
# the same few hosts is much faster than a fresh handshake per request.
_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = _UA
_adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16,
                       max_retries=Retry(total=2, backoff_factor=0.5,
                                         status_forcelist=(429, 502, 503, 504),
                                         allowed_methods=("GET", "HEAD", "POST")))
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)

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
    # Also excludes teen/teenage/teenager: images found here end up in
    # adult captions, so a query must never ask for minors.
    "elderly", "mature", "adult", "senior",
}
_CLOTHING_WORDS = {
    "dress", "skirt", "jeans", "shirt", "blouse", "lingerie", "bikini",
    "swimsuit", "gown", "heels", "stockings", "leggings", "shorts",
    "top", "bra", "panties", "corset", "uniform", "costume", "lace",
    "nightgown", "robe", "jacket", "sweater", "pants", "suit",
}
_ATTRIBUTE_CATEGORIES = (
    _HAIR_COLOR_WORDS, _GENDER_WORDS, _BODY_TYPE_WORDS, _AGE_WORDS,
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


# Titles framed around children or teens are dropped outright — these images
# are used for adult captions, so anything that might depict a minor (or
# frames an adult by their childhood, e.g. "Child Stars Who Grew Up Hot") is
# never offered.
_MINOR_WORDS = {
    "child", "children", "childhood", "kid", "kids", "kiddo", "teen", "teens",
    "teenage", "teenager", "teenagers", "schoolgirl", "schoolgirls", "schoolboy",
    "schoolboys", "underage", "minor", "minors", "baby", "babies", "toddler",
    "toddlers", "preteen", "tween", "tweens", "youngster", "youngsters",
}
_MINOR_PHRASES = ("grew up", "growing up", "high school", "as kids", "young girls",
                  "young boys", "little girls", "little boys")

# Search-term synonyms, so "woman" also ranks "Girls"/"Babes" titles and a
# hair color matches its other spellings.
_SYNONYM_GROUPS = (
    {"woman", "women", "girl", "girls", "lady", "ladies", "babe", "babes", "chick",
     "chicks", "beauty", "beauties", "hottie", "hotties", "female", "females"},
    {"man", "men", "guy", "guys", "dude", "dudes", "hunk", "hunks", "male", "males",
     "gentleman", "gentlemen"},
    {"blonde", "blond", "blondes", "blonds", "platinum"},
    {"brunette", "brunettes"},
    {"redhead", "redheads", "ginger", "gingers", "auburn"},
)
# Title words that suggest a gallery of individual attractive people, and
# ones that suggest memes, listicles or non-people content.
_PERSON_HINTS = {
    "girl", "girls", "woman", "women", "babe", "babes", "beauty", "beauties",
    "hottie", "hotties", "lady", "ladies", "guy", "guys", "men", "hunk", "hunks",
    "model", "models", "bikini", "bikinis", "lingerie", "selfie", "selfies",
    "sexy", "hot", "cute", "gorgeous", "stunning", "beautiful", "pretty",
    "curvy", "fit", "bombshell", "bombshells", "blonde", "blondes", "brunette",
    "brunettes", "redhead", "redheads", "busty", "petite", "legs", "yoga",
}
_OFFTOPIC_HINTS = {
    "meme", "memes", "funny", "fail", "fails", "prank", "pranks", "comic",
    "comics", "joke", "jokes", "facts", "history", "historical", "car", "cars",
    "animal", "animals", "cat", "cats", "dog", "dogs", "food", "signs", "letters",
    "tweets", "texts", "design", "house", "houses", "then", "how", "things",
    "stories", "story", "photoshop", "weird", "random", "wtf", "side", "effects",
    "top",
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


def _is_minor_title(title: str) -> bool:
    lowered = title.lower()
    if any(phrase in lowered for phrase in _MINOR_PHRASES):
        return True
    return any(w in _MINOR_WORDS for w in re.findall(r"[a-z]+", lowered))


def _is_excluded_title(title: str) -> bool:
    return _is_minor_title(title) or _is_celebrity_title(title)


def _person_score(title: str) -> int:
    """How strongly a title suggests a gallery of single attractive people
    (positive) versus memes/listicles/objects (negative)."""
    words = re.findall(r"[a-z]+", title.lower())
    hits = sum(1 for w in words if w in _PERSON_HINTS)
    misses = sum(1 for w in words if w in _OFFTOPIC_HINTS)
    return min(hits, 3) - 2 * misses


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


_VAGUE_HINTS = {"sexy", "hot", "cute", "gorgeous", "stunning", "beautiful", "pretty", "beauty"}


def similar_query(title: str) -> str:
    """Search query for "More like this" on a result: the post title's
    descriptive words (hair color, gender, body type, clothing), with
    plurals folded to their singular so "Beauty Brunettes" still gives
    "brunette"; falls back to its person words if none of those match."""
    vocab = set().union(*_ATTRIBUTE_CATEGORIES)
    words = []
    for w in re.findall(r"[a-z]+", title.lower()):
        if w not in vocab and w.endswith("s") and w[:-1] in vocab:
            w = w[:-1]
        words.append(w)
    query = extract_keywords(" ".join(words))
    if query:
        return query
    fallback = [w for w in words if w in _PERSON_HINTS and w not in _VAGUE_HINTS]
    return " ".join(fallback[:2])


def _smallest_srcset_url(img_tag, min_width: int = 0) -> str:
    """Prefer the smallest `srcset` candidate for a thumbnail — the bare
    `src` on a search-results page is the full 660px-wide image, several
    times the bytes of the thumbnail we actually display. With min_width,
    the smallest candidate at least that wide (falling back to `src`)."""
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
    candidates = [c for c in candidates if c[0] >= min_width]
    if not candidates:
        return img_tag.get("src", "")
    return min(candidates, key=lambda c: c[0])[1]


def _search_barnorama(query: str, page: int) -> list:
    url = f"{_BARNORAMA_BASE}/page/{page}/" if page > 1 else f"{_BARNORAMA_BASE}/"
    resp = _SESSION.get(url, params={"s": query}, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for art in soup.select("article.item-list"):
        title_a = art.select_one("h2.post-box-title a")
        if not title_a or not title_a.get("href"):
            continue
        title = title_a.get_text(strip=True) or "(untitled)"
        if _is_excluded_title(title):
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
    resp = _SESSION.post(f"{_ACIDCOW_BASE}/index.php", params={"do": "search"},
                         data=data, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for post in soup.select("div.post"):
        title_a = post.select_one("h1 a")
        if not title_a or not title_a.get("href"):
            continue
        title = title_a.get_text(strip=True) or "(untitled)"
        if _is_excluded_title(title):
            continue
        thumb_img = post.select_one("img")
        results.append({
            "title": title,
            "url": title_a["href"],
            "thumb_url": thumb_img.get("src", "") if thumb_img else "",
        })
    return results


# sex.com results are single pins, not gallery posts, so each search's pins
# are grouped into one pseudo-post per kind ("sexcom:<kind>:<term>:<page>");
# fetch_gallery() serves its items from here.
_SEXCOM_GROUPS: dict = {}
_sexcom_lock = threading.Lock()


def _search_sexcom(query: str, page: int, gifs_only: bool = False) -> list:
    terms = query.lower().split()
    male = set(_SYNONYM_GROUPS[1])
    gender = set().union(*_SYNONYM_GROUPS[:2])
    # Its search matches whole phrases poorly ("redhead man" -> 0), so use
    # the most distinctive term; male subjects live under "gay".
    term = next((t for t in terms if t not in gender), terms[0] if terms else "")
    if not term:
        return []
    orientation = "gay" if any(t in male for t in terms) else "straight"
    posts = []
    kinds = (("gifs", "GIFs"),) if gifs_only else (("pictures", "pictures"), ("gifs", "GIFs"))
    for kind, label in kinds:
        resp = _SESSION.get(_SEXCOM_API.format(kind=kind), timeout=_TIMEOUT, params={
            "search": term, "sexual-orientation": orientation, "order": "likeCount",
            "page": page, "limit": _SEXCOM_PAGE_SIZE})
        resp.raise_for_status()
        items = []
        for pin in resp.json().get("data", []):
            uri = pin.get("uri") or ""
            title = re.sub(r"\s+", " ", pin.get("title") or "")
            if not uri.startswith("/") or _is_excluded_title(title):
                continue
            url = _SEXCOM_CDN + uri
            item = _gallery_item(url, url + "?width=300")
            item["gif"] = kind == "gifs" or item["gif"]
            items.append(item)
        if not items:
            continue
        key = f"sexcom:{kind}:{term}:{orientation}:{page}"
        _store_group(key, items)
        posts.append({"title": f"sex.com {label}: {term} ({len(items)})", "url": key,
                      "thumb_url": items[0]["thumb"], "mature": True})
    return posts


def _store_group(key: str, items: list) -> None:
    """Keep a pseudo-post's items (sources whose results are single images or
    GIFs, not gallery posts) for fetch_gallery()."""
    with _sexcom_lock:
        _SEXCOM_GROUPS[key] = items
        while len(_SEXCOM_GROUPS) > 80:
            _SEXCOM_GROUPS.pop(next(iter(_SEXCOM_GROUPS)))


# ---------------------------------------------------------------------------
# Mature sources (searched only with Mature on)
#
# xgroovy.com: /gifs/search/<q>/<page>/ lists GIFs — really short MP4s, which
# the app opens like GIFs — as <div class="gif-wrap" data-full=… (1080p MP4)
# data-poster=…> with the title in the item's .title-link; /photos/search/
# <q>/<page>/ lists albums (.list-albums .item a) whose pages show their own
# pictures at contents/albums/main/420x999/<bucket>/<album id>/<n>.jpg, the
# full size being the same path under contents/albums/sources/.
#
# porngifs.com: no free-text search; it browses ~950 tags. GET
# /ajax/scrolldown?loadcount=<offset>&tag=<tag> returns 20 [{"src": id}] at a
# time, each GIF at cdn.porngifs.com/img/<id> (no titles, no thumbnails). A
# query's words are matched to its tag list (read once from the home page).
# (wetgifs.com, also asked for, is a parked domain with no content.)
# ---------------------------------------------------------------------------

_XGROOVY = "https://xgroovy.com"
_PORNGIFS = "https://porngifs.com"
_PORNGIFS_CDN = "https://cdn.porngifs.com/img/"
_PORNGIFS_PER_TAG = 20
_porngifs_tags: Optional[set] = None
_porngifs_lock = threading.Lock()


def _search_xgroovy_gifs(query: str, page: int) -> list:
    q = urllib.parse.quote(query.strip().lower())
    url = f"{_XGROOVY}/gifs/search/{q}/" + (f"{page}/" if page > 1 else "")
    resp = _SESSION.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for wrap in soup.select("div.gif-wrap[data-full]"):
        item_div = wrap.find_parent("div", class_="item") or wrap.parent
        link = item_div.select_one("a.title-link") if item_div else None
        title = (link.get("title") or link.get_text(strip=True)) if link else ""
        if _is_excluded_title(title):
            continue
        items.append({"url": wrap["data-full"], "thumb": wrap.get("data-poster") or "",
                      "gif": True})
    items = [i for i in items if i["thumb"]]
    if not items:
        return []
    key = f"xgroovy:gifs:{query.lower()}:{page}"
    _store_group(key, items)
    return [{"title": f"xgroovy GIFs: {query} ({len(items)})", "url": key,
             "thumb_url": items[0]["thumb"], "mature": True}]


def _search_xgroovy_photos(query: str, page: int) -> list:
    q = urllib.parse.quote(query.strip().lower())
    url = f"{_XGROOVY}/photos/search/{q}/" + (f"{page}/" if page > 1 else "")
    resp = _SESSION.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    posts = []
    for item in soup.select(".list-albums .item"):
        a = item.select_one("a[href*='/photos/']")
        title_el = item.select_one(".title")
        if not a:
            continue
        title = title_el.get_text(strip=True) if title_el else "(untitled)"
        if _is_excluded_title(title):
            continue
        img = item.select_one("img")
        posts.append({"title": f"xgroovy: {title}", "url": a["href"],
                      "thumb_url": img.get("src", "") if img else "", "mature": True})
    return posts


def _fetch_gallery_xgroovy(album_url: str) -> list:
    m = re.search(r"/photos/(\d+)/", album_url)
    if not m:
        return []
    album_id = m.group(1)
    resp = _SESSION.get(album_url, timeout=_TIMEOUT)
    resp.raise_for_status()
    thumbs = re.findall(r"https://photos\.xgroovy\.com/contents/albums/main/\d+x\d+/\d+/"
                        + album_id + r"/\d+\.(?:jpg|jpeg|png|webp|gif)", resp.text)
    items, seen = [], set()
    for t in thumbs:
        if t in seen:
            continue
        seen.add(t)
        full = re.sub(r"/albums/main/\d+x\d+/", "/albums/sources/", t)
        items.append(_gallery_item(full, t))
    return items


def _porngifs_tag_set() -> set:
    """porngifs.com's tags, read from its home page once per run."""
    global _porngifs_tags
    with _porngifs_lock:
        if _porngifs_tags is None:
            resp = _SESSION.get(_PORNGIFS + "/", timeout=_TIMEOUT)
            resp.raise_for_status()
            _porngifs_tags = {urllib.parse.unquote(t).strip().lower()
                              for t in re.findall(r'href="/tag/([^"]+)"', resp.text)}
        return _porngifs_tags


def _porngifs_tags_for(query: str) -> list:
    """The site's tags that match the query: the whole phrase, then each
    word or a synonym of it (e.g. "redhead" → "ginger")."""
    tags = _porngifs_tag_set()
    q = " ".join(query.lower().split())
    picked = [q] if q in tags else []
    for word in q.split():
        group = next((g for g in _SYNONYM_GROUPS if word in g), {word})
        for cand in [word] + sorted(group - {word}):
            for form in (cand, cand.rstrip("s"), cand + "s"):
                if form in tags and form not in picked:
                    picked.append(form)
                    break
            else:
                continue
            break
    # Many of its tags are performers' names; like everywhere else here,
    # real people aren't searched for.
    return [t for t in picked if not content_rules.mentions_minor(t)
            and not _is_celebrity_title(t)][:2]


def _search_porngifs(query: str, page: int) -> list:
    posts = []
    for tag in _porngifs_tags_for(query):
        items = []
        for offset in ((page - 1) * 2 * 20, (page - 1) * 2 * 20 + 20):
            resp = _SESSION.get(_PORNGIFS + "/ajax/scrolldown", timeout=_TIMEOUT,
                                params={"loadcount": offset, "tag": tag},
                                headers={"X-Requested-With": "XMLHttpRequest",
                                         "Referer": _PORNGIFS + "/"})
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                break
            for d in data:
                src = str(d.get("src") or "").strip()
                if src.isdigit():
                    url = _PORNGIFS_CDN + src
                    items.append({"url": url, "thumb": url, "gif": True})
        items = items[:_PORNGIFS_PER_TAG]
        if not items:
            continue
        key = f"porngifs:{tag}:{page}"
        _store_group(key, items)
        posts.append({"title": f"porngifs.com: {tag} ({len(items)})", "url": key,
                      "thumb_url": items[0]["thumb"], "mature": True})
    return posts


# ---------------------------------------------------------------------------
# More sources
#
# redgifs.com (mature): GET /v2/auth/temporary gives a token (kept until it
# stops working), then /v2/gifs/search?query=… returns up to 40 clips with
# tags; each is an MP4 (urls.hd), with a JPEG thumbnail. Clips tagged as
# drawn (hentai, anime...) or with words for minors are skipped, as are tags
# that name a performer.
#
# pornpics.com (mature): /search/srch.php?q=…&offset=… answers JSON galleries
# ({"desc", "g_url", "t_url"}); a gallery page links its full-size pictures
# as a.rel-link (1280px) beside 460px tiles (#tiles img[data-src]).
#
# Wikimedia Commons (any setting): the MediaWiki API's file search with
# imageinfo gives each file's URL and a 330px thumbnail. Freely licensed,
# mostly not explicit.
# ---------------------------------------------------------------------------

_REDGIFS_API = "https://api.redgifs.com/v2"
_REDGIFS_PER_PAGE = 40
_REDGIFS_SKIP_TAGS = re.compile(
    r"\b(?:hentai|anime|cartoon|animated|3d|sfm|cosplay|loli|shota|teen|teens|schoolgirl|"
    r"school|student|young|barely)\b", re.I)
_redgifs_token: Optional[str] = None
_redgifs_lock = threading.Lock()
_PORNPICS = "https://www.pornpics.com"
_PORNPICS_PER_PAGE = 20
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_COMMONS_PER_PAGE = 30


def _redgifs_get(path: str, params: dict) -> dict:
    global _redgifs_token
    for attempt in range(2):
        with _redgifs_lock:
            if _redgifs_token is None or attempt:
                resp = _SESSION.get(f"{_REDGIFS_API}/auth/temporary", timeout=_TIMEOUT)
                resp.raise_for_status()
                _redgifs_token = resp.json()["token"]
            token = _redgifs_token
        resp = _SESSION.get(f"{_REDGIFS_API}{path}", params=params, timeout=_TIMEOUT,
                            headers={"Authorization": f"Bearer {token}"})
        if getattr(resp, "status_code", 200) in (401, 403) and not attempt:
            continue
        resp.raise_for_status()
        return resp.json()
    return {}


def _search_redgifs(query: str, page: int) -> list:
    data = _redgifs_get("/gifs/search", {"query": query.strip().lower(),
                                         "count": _REDGIFS_PER_PAGE, "page": page})
    items = []
    for g in data.get("gifs") or []:
        urls = g.get("urls") or {}
        url = urls.get("hd") or urls.get("sd")
        thumb = urls.get("thumbnail") or urls.get("poster")
        tags = [str(t) for t in g.get("tags") or []]
        text = " ".join(tags + [str(g.get("description") or "")])
        if (not url or not thumb or _REDGIFS_SKIP_TAGS.search(text)
                or content_rules.mentions_minor(text) or _is_minor_title(text)
                or any(_is_celebrity_title(t) for t in tags if " " in t)):
            continue
        # Most are clips (MP4, opened like a GIF); some posts are pictures.
        items.append({"url": url, "thumb": thumb,
                      "gif": urllib.parse.urlparse(url).path.lower().endswith(".mp4")})
    if not items:
        return []
    key = f"redgifs:{query.lower()}:{page}"
    _store_group(key, items)
    return [{"title": f"redgifs: {query} ({len(items)})", "url": key,
             "thumb_url": items[0]["thumb"], "mature": True}]


def _search_pornpics(query: str, page: int) -> list:
    resp = _SESSION.get(_PORNPICS + "/search/srch.php", timeout=_TIMEOUT,
                        # Most galleries name their performer and are skipped
                        # (real people), so ask for three times as many.
                        params={"q": query.strip().lower(), "lang": "en",
                                "limit": _PORNPICS_PER_PAGE * 3,
                                "offset": (page - 1) * _PORNPICS_PER_PAGE * 3},
                        headers={"Referer": _PORNPICS + "/"})
    resp.raise_for_status()
    data = resp.json()
    posts = []
    for g in data if isinstance(data, list) else []:
        title = re.sub(r"\s+\d+$", "", re.sub(r"\s+", " ", str(g.get("desc") or ""))).strip()
        url = str(g.get("g_url") or "")
        if not url.startswith(_PORNPICS) or not title or _is_excluded_title(title):
            continue
        posts.append({"title": title, "url": url,
                      "thumb_url": g.get("t_url") or g.get("t_url_460") or "", "mature": True})
    return posts[:_PORNPICS_PER_PAGE]


def _fetch_gallery_pornpics(post_url: str) -> list:
    resp = _SESSION.get(post_url, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    fulls = [a.get("href") for a in soup.select("a.rel-link") if a.get("href")]
    tiles = [i.get("data-src") or i.get("src") or "" for i in soup.select("#tiles img")]
    items = []
    for i, full in enumerate(fulls):
        thumb = tiles[i] if i < len(tiles) and tiles[i].startswith("http") else ""
        items.append(_gallery_item(full, thumb))
    return items


def _search_commons(query: str, page: int) -> list:
    resp = _SESSION.get(_COMMONS_API, timeout=_TIMEOUT, params={
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query.strip()} filetype:bitmap", "gsrnamespace": 6,
        "gsrlimit": _COMMONS_PER_PAGE, "gsroffset": (page - 1) * _COMMONS_PER_PAGE,
        "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 330})
    resp.raise_for_status()
    pages = ((resp.json().get("query") or {}).get("pages") or {}).values()
    items = []
    for p in sorted(pages, key=lambda p: p.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        title = re.sub(r"^File:|\.\w+$", "", str(p.get("title") or ""))
        url = (info.get("url") or "").split("?")[0]
        thumb = (info.get("thumburl") or "").split("?")[0]
        if (not url or not thumb or info.get("mime") not in ("image/jpeg", "image/png",
                                                             "image/gif", "image/webp")
                or _is_excluded_title(title.replace("_", " "))):
            continue
        item = _gallery_item(url, thumb)
        if item["gif"]:
            item["thumb"] = url        # Commons' GIF thumbnails are still frames
        items.append(item)
    if not items:
        return []
    key = f"commons:{query.lower()}:{page}"
    _store_group(key, items)
    return [{"title": f"Wikimedia Commons: {query} ({len(items)})", "url": key,
             "thumb_url": items[0]["thumb"]}]


def _rank_results(results: list, query: str) -> list:
    """Sort results the way a web search does: the query's first word is
    the primary term and decides who's in the top tier, exactly like
    extract_keywords() always puts the hair color first when there is one
    — that's the trait most worth matching on a photo search. The rest of
    the query is secondary — those words only break ties among results
    that already match the primary term (so a title matching the primary
    plus several secondary words sorts above one matching the primary
    alone), they never lift a result that's missing the primary term past
    one that has it. Ties keep each source's own order (stable sort).

    Terms match at the start of a title word (so "blonde" still matches
    "Blondes"), not anywhere in the title — a plain substring check let
    "red" match "Featured"/"Covered" and float unrelated posts to the top."""
    terms = query.lower().split()
    if not terms:
        return results
    patterns = [_term_pattern(t) for t in terms]
    primary, secondary = patterns[0], patterns[1:]

    def rank_key(result):
        title = result["title"].lower()
        secondary_hits = sum(1 for p in secondary if p.search(title))
        # After query relevance, prefer galleries of individual attractive
        # people over memes/listicles — what Find Online is used for.
        return (not primary.search(title), -secondary_hits, -_person_score(title))

    return sorted(results, key=rank_key)


def _term_pattern(term: str) -> "re.Pattern":
    """Word-start match for a query term or any of its synonyms."""
    group = next((g for g in _SYNONYM_GROUPS if term in g), {term})
    alternation = "|".join(re.escape(t) for t in sorted(group, key=len, reverse=True))
    return re.compile(r"\b(?:" + alternation + ")")


def search(query: str, page: int = 1) -> list:
    """Return [{"title", "url", "thumb_url"}, ...] merging one search
    results page each from Barnorama and AcidCow (page N of each,
    concatenated — the two sites paginate independently, so "page 2" isn't
    a single ranked list across both, just each site's own next page), then
    ranked by relevance to query — see _rank_results(). Empty for a source
    on no results (or a request/parse failure — callers can't tell the
    difference, which is fine here: both just mean "nothing to show" for
    that source), and a failing source never blocks the other one's
    results. Results whose title names or references a real person (see
    _is_celebrity_title) are dropped rather than returned."""
    results = []
    for batch in search_stream(query, page):
        results.extend(batch)
    return _rank_results(results, query)


# Post titles that say the post is explicit; hidden when mature results are
# off. (sex.com, a porn site, is skipped entirely then.) Title-based only —
# there's no on-device nudity detector — so it's a filter, not a guarantee.
_EXPLICIT_TITLE = re.compile(
    r"\b(?:nsfw|nudes?|naked|nudity|topless|bottomless|porn\w*|xxx|sex|sexual|explicit|"
    r"pussy|pussies|tits|titties|boobs|nipples?|hentai|onlyfans|uncensored|18\+)\b", re.I)


def is_blocked_query(query: str) -> bool:
    """Find Online searches adult websites, so a search that refers to a
    minor in any way is never run."""
    return content_rules.mentions_minor(query) or _is_minor_title(query)


def is_explicit_title(title: str) -> bool:
    return bool(_EXPLICIT_TITLE.search(title))


def search_stream(query: str, page: int = 1, gifs_only: bool = False,
                  mature: bool = True):
    """Like search(), but yields each source's new (deduplicated, ranked)
    results as soon as that source answers — Barnorama replies in under a
    second while AcidCow's search takes ~5s server-side, so callers can show
    the fast results without waiting on the slow ones. gifs_only asks
    sex.com for GIFs only (the gallery sites are filtered per image by the
    caller); mature=False skips sex.com and drops explicit titles."""
    query = query.strip()
    if not query or is_blocked_query(query):
        return
    # Both sites match multi-word searches as a phrase-ish full-text query,
    # so "brunette woman" returned a handful of off-topic posts while
    # "brunette" alone returns dozens of relevant galleries. Search the
    # full query *and* its most distinctive single term, then let ranking
    # (which still uses every term) sort them.
    queries = [query]
    terms = query.split()
    if len(terms) > 1:
        gender = set().union(*_SYNONYM_GROUPS[:2])
        distinctive = next((t for t in terms if t.lower() not in gender), terms[0])
        queries.append(distinctive)
    jobs = [(src, (q, page)) for q in queries for src in (_search_barnorama, _search_acidcow)]
    if mature:
        # Mature-only sources (explicit sites).
        jobs.append((_search_sexcom, (query, page, gifs_only)))  # picks its own single term
        jobs.append((_search_xgroovy_gifs, (query, page)))
        jobs.append((_search_porngifs, (query, page)))
        jobs.append((_search_redgifs, (query, page)))
        if not gifs_only:
            jobs.append((_search_xgroovy_photos, (query, page)))
            jobs.append((_search_pornpics, (query, page)))
    if not gifs_only:
        jobs.append((_search_commons, (query, page)))
    seen = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(src, *args) for src, args in jobs]
        for fut in concurrent.futures.as_completed(futures):
            try:
                batch = fut.result()
            except (requests.RequestException, ValueError):
                continue
            new = [r for r in batch if r["url"] not in seen
                   and (mature or not is_explicit_title(r["title"]))]
            seen.update(r["url"] for r in new)
            if new:
                yield _rank_results(new, query)


def _gallery_item(url: str, thumb: str = "") -> dict:
    """One gallery image: full-size `url`, a smaller `thumb` to preview it
    with (the full image when the site offers nothing smaller), and whether
    it's an animated GIF."""
    path = urllib.parse.urlparse(url).path.lower()
    return {"url": url, "thumb": thumb or url, "gif": path.endswith(".gif")}


def _fetch_gallery_barnorama(post_url: str) -> list:
    resp = _SESSION.get(post_url, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    entry = soup.select_one("div.entry")
    if entry is None:
        return []

    items, seen = [], set()
    for img in entry.select("img.size-full"):
        src = img.get("src")
        if src and src not in seen:
            seen.add(src)
            # WordPress srcset offers ~240px variants — big enough for a
            # preview and face check, a fraction of the full image's bytes.
            # GIFs keep the original (resized variants are a single frame).
            thumb = "" if src.lower().endswith(".gif") else _smallest_srcset_url(img, 200)
            items.append(_gallery_item(src, thumb))
    return items


def _fetch_gallery_acidcow(post_url: str) -> list:
    """Content images live on the cdn.acidcow.com subdomain; the like/
    dislike icons and avatar placeholder sharing #dle-content are on the
    main domain or a template-relative path, so a netloc check separates
    them cleanly. (Tried matching the "/uploads/posts/" path segment first
    — some posts use a different path, e.g. "/pics/<date>/<id>_<hash>.jpg",
    so that missed real galleries entirely.)"""
    resp = _SESSION.get(post_url, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    entry = soup.select_one("#dle-content")
    if entry is None:
        return []

    items, seen = [], set()
    for img in entry.select("img"):
        src = img.get("src", "")
        if urllib.parse.urlparse(src).netloc == "cdn.acidcow.com" and src not in seen:
            seen.add(src)
            items.append(_gallery_item(src))
    return items


def fetch_gallery(post_url: str) -> list:
    """Return the images embedded in a post as [{"url", "thumb", "gif"}],
    in page order. Dispatches on post_url's host, since Barnorama and
    AcidCow need different selectors."""
    if post_url.startswith(("sexcom:", "xgroovy:", "porngifs:", "redgifs:", "commons:")):
        with _sexcom_lock:
            return list(_SEXCOM_GROUPS.get(post_url, []))
    host = urllib.parse.urlparse(post_url).netloc
    if "acidcow.com" in host:
        return _fetch_gallery_acidcow(post_url)
    if "xgroovy.com" in host:
        return _fetch_gallery_xgroovy(post_url)
    if "pornpics.com" in host:
        return _fetch_gallery_pornpics(post_url)
    return _fetch_gallery_barnorama(post_url)


def download_bytes(url: str) -> bytes:
    resp = _SESSION.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# Previews + single-person detection
#
# Most searches here are for one attractive person per photo, but galleries
# mix in group shots, memes and screenshots. OpenCV's YuNet face detector
# (assets/face_detection_yunet_2023mar.onnx, MIT, from opencv_zoo) counts
# faces in each preview so the results can show single-person photos only.
# On a live sample it found exactly 1 face in 15/16 images of a "Beauty
# Blondes" gallery and 0 in 16/16 of a pranks/memes post. If OpenCV or the
# model is unavailable, face counts are None and nothing is filtered.
# ---------------------------------------------------------------------------

_FACE_MODEL = os.path.join(app_paths.RESOURCE_DIR, "assets", "face_detection_yunet_2023mar.onnx")
# Faces smaller than this share of the image are background people, not the
# subject — ignoring them keeps a crowd behind someone from disqualifying a
# solo shot.
_MIN_FACE_AREA = 0.004
_face_lock = threading.Lock()
_face_detector = None
_face_unavailable = False

_PREVIEW_CACHE: "collections.OrderedDict[str, tuple]" = collections.OrderedDict()
_PREVIEW_CACHE_MAX = 400
_preview_lock = threading.Lock()


def _get_face_detector():
    global _face_detector, _face_unavailable
    if _face_detector is None and not _face_unavailable:
        try:
            import cv2
            _face_detector = cv2.FaceDetectorYN.create(_FACE_MODEL, "", (320, 320),
                                                       0.7, 0.3, 50)
        except Exception:
            _face_unavailable = True
    return _face_detector


def count_faces(img: Image.Image) -> "int | None":
    """Number of subject-sized faces in img, or None if detection isn't
    available."""
    with _face_lock:
        det = _get_face_detector()
        if det is None:
            return None
        import cv2
        import numpy as np
        work = img.convert("RGB")
        work.thumbnail((480, 480))
        arr = cv2.cvtColor(np.asarray(work), cv2.COLOR_RGB2BGR)
        det.setInputSize((arr.shape[1], arr.shape[0]))
        _ok, faces = det.detect(arr)
    if faces is None:
        return 0
    min_area = _MIN_FACE_AREA * arr.shape[0] * arr.shape[1]
    return sum(1 for f in faces if f[2] * f[3] >= min_area)


def find_faces(img: Image.Image, min_area: float = 0.002) -> "list | None":
    """Faces in img as (x0, y0, x1, y1) fractions of its size, largest
    first, or None if detection isn't available. Ignores faces smaller than
    min_area of the picture."""
    with _face_lock:
        det = _get_face_detector()
        if det is None:
            return None
        import cv2
        import numpy as np
        work = img.convert("RGB")
        work.thumbnail((640, 640))
        arr = cv2.cvtColor(np.asarray(work), cv2.COLOR_RGB2BGR)
        det.setInputSize((arr.shape[1], arr.shape[0]))
        _ok, faces = det.detect(arr)
    if faces is None:
        return []
    h, w = arr.shape[:2]
    out = []
    for f in faces:
        x, y, fw, fh = (float(v) for v in f[:4])
        if fw * fh < min_area * w * h:
            continue
        out.append((max(0.0, x / w), max(0.0, y / h), min(1.0, (x + fw) / w),
                    min(1.0, (y + fh) / h)))
    out.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Text detection ("No text" filter)
#
# Results that already carry a caption, meme text or a big watermark aren't
# usable as a base for a new caption. The PP-OCRv3 text detector
# (assets/text_detection_en_ppocrv3_2023may.onnx, Apache-2.0, from
# opencv_zoo) returns a per-pixel "text here" probability map; the share of
# the picture covered by text, and how many separate text regions there
# are, decide. A lone small corner watermark is let through. If OpenCV or
# the model is unavailable, has_text() is None and nothing is filtered.
# ---------------------------------------------------------------------------

_TEXT_MODEL = os.path.join(app_paths.RESOURCE_DIR, "assets",
                           "text_detection_en_ppocrv3_2023may.onnx")
_TEXT_SIDE = 640            # detector input, square; small previews are upscaled
_TEXT_PROB = 0.3            # pixel counts as text above this probability
_TEXT_MIN_REGION = 0.0003   # regions smaller than this share of the picture are noise
_TEXT_FRACTION = 0.004      # this much text anywhere = has text
_TEXT_REGIONS = 2           # ...or this many separate text regions, covering
_TEXT_REGIONS_FRACTION = 0.0035  # at least this much
_text_lock = threading.Lock()
_text_net = None
_text_unavailable = False


def _get_text_net():
    global _text_net, _text_unavailable
    if _text_net is None and not _text_unavailable:
        try:
            import cv2
            _text_net = cv2.dnn.readNet(_TEXT_MODEL)
        except Exception:
            _text_unavailable = True
    return _text_net


def text_coverage(img: Image.Image) -> "tuple | None":
    """(share of the picture covered by text, number of text regions), or
    None if detection isn't available."""
    with _text_lock:
        net = _get_text_net()
        if net is None:
            return None
        import cv2
        import numpy as np
        # Always the same square input (the picture scaled to fit, padded
        # with black): OpenCV 5's new graph engine gave garbage when one
        # network saw several input shapes.
        im = img.convert("RGB")
        w, h = im.size
        s = _TEXT_SIDE / max(w, h)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        canvas = Image.new("RGB", (_TEXT_SIDE, _TEXT_SIDE))
        canvas.paste(im.resize((nw, nh), Image.BICUBIC), (0, 0))
        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        arr = (arr - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
        net.setInput(arr.astype(np.float32).transpose(2, 0, 1)[None])
        prob = np.asarray(net.forward())
    prob = prob.reshape(prob.shape[-2:])[:nh, :nw]      # the picture, not the padding
    mask = np.ascontiguousarray(prob > _TEXT_PROB, dtype=np.uint8)
    _n, _labels, stats, _c = cv2.connectedComponentsWithStats(mask)
    min_px = _TEXT_MIN_REGION * mask.size
    regions = [st for st in stats[1:] if st[4] >= min_px]
    covered = sum(int(st[4]) for st in regions) / mask.size
    return covered, len(regions)


def has_text(img: Image.Image) -> "bool | None":
    """Whether the picture already has text on it (a caption, meme text, a
    big watermark), or None if detection isn't available."""
    try:
        found = text_coverage(img)
    except Exception:
        return None
    if found is None:
        return None
    covered, regions = found
    return covered >= _TEXT_FRACTION or (regions >= _TEXT_REGIONS
                                         and covered >= _TEXT_REGIONS_FRACTION)


def load_preview(item: dict, size: tuple = (130, 95)) -> tuple:
    """(thumbnail PIL image, face count or None, has text or None) for a
    gallery item — downloads its small `thumb` version, cached so paging
    back and forth or reopening a gallery doesn't download it again."""
    key = item["thumb"]
    with _preview_lock:
        if key in _PREVIEW_CACHE:
            _PREVIEW_CACHE.move_to_end(key)
            return _PREVIEW_CACHE[key]
    img = Image.open(io.BytesIO(download_bytes(key)))
    img.seek(0)
    img = img.convert("RGB")
    faces = count_faces(img)
    text = has_text(img)
    img.thumbnail(size, Image.LANCZOS)
    result = (img, faces, text)
    with _preview_lock:
        _PREVIEW_CACHE[key] = result
        while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_MAX:
            _PREVIEW_CACHE.popitem(last=False)
    return result


def download_to_tempfile(url: str) -> str:
    """Download url's bytes to a new temp file named with the URL's own
    extension (falls back to .jpg for anything unrecognized), so callers can
    hand the path straight to Image.open()/imageio without guessing format."""
    # Some hosts end file URLs with a slash (xgroovy's ".../123_1080p_gif.mp4/");
    # an extensionless one from porngifs' CDN is a GIF.
    path = urllib.parse.urlparse(url).path.rstrip("/")
    ext = os.path.splitext(path)[1].lower()
    if url.startswith(_PORNGIFS_CDN):
        ext = ".gif"
    elif ext not in _IMG_EXTS + (".mp4",):
        ext = ".jpg"
    data = download_bytes(url)
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(data)
    finally:
        tmp.close()
    return tmp.name
