"""Online image/GIF lookup — search Barnorama + AcidCow and pull a source
file from a result.

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

import requests
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app_paths

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


def _search_sexcom(query: str, page: int) -> list:
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
    for kind, label in (("pictures", "pictures"), ("gifs", "GIFs")):
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
        with _sexcom_lock:
            _SEXCOM_GROUPS[key] = items
            while len(_SEXCOM_GROUPS) > 50:
                _SEXCOM_GROUPS.pop(next(iter(_SEXCOM_GROUPS)))
        posts.append({"title": f"sex.com {label}: {term} ({len(items)})", "url": key,
                      "thumb_url": items[0]["thumb"]})
    return posts


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


def search_stream(query: str, page: int = 1):
    """Like search(), but yields each source's new (deduplicated, ranked)
    results as soon as that source answers — Barnorama replies in under a
    second while AcidCow's search takes ~5s server-side, so callers can show
    the fast results without waiting on the slow ones."""
    query = query.strip()
    if not query:
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
    jobs = [(src, q) for q in queries for src in (_search_barnorama, _search_acidcow)]
    jobs.append((_search_sexcom, query))  # picks its own single term
    seen = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(src, q, page) for src, q in jobs]
        for fut in concurrent.futures.as_completed(futures):
            try:
                batch = fut.result()
            except (requests.RequestException, ValueError):
                continue
            new = [r for r in batch if r["url"] not in seen]
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
    if post_url.startswith("sexcom:"):
        with _sexcom_lock:
            return list(_SEXCOM_GROUPS.get(post_url, []))
    host = urllib.parse.urlparse(post_url).netloc
    if "acidcow.com" in host:
        return _fetch_gallery_acidcow(post_url)
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


def load_preview(item: dict, size: tuple = (130, 95)) -> tuple:
    """(thumbnail PIL image, face count or None) for a gallery item —
    downloads its small `thumb` version, cached so paging back and forth
    or reopening a gallery doesn't download it again."""
    key = item["thumb"]
    with _preview_lock:
        if key in _PREVIEW_CACHE:
            _PREVIEW_CACHE.move_to_end(key)
            return _PREVIEW_CACHE[key]
    img = Image.open(io.BytesIO(download_bytes(key)))
    img.seek(0)
    img = img.convert("RGB")
    faces = count_faces(img)
    img.thumbnail(size, Image.LANCZOS)
    result = (img, faces)
    with _preview_lock:
        _PREVIEW_CACHE[key] = result
        while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_MAX:
            _PREVIEW_CACHE.popitem(last=False)
    return result


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
