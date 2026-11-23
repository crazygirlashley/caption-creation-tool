"""Rebuild assets/celebrities.txt — the big-name celebrities Find Online
skips (titles, tags and searches that name them).

Asks Wikidata (CC0) for people in famous-making occupations, keeping those
covered by many Wikipedia language editions (the "sitelinks" count; the
bar is per occupation, see OCCUPATIONS). One-word stage names are kept only
when they aren't ordinary English words, so "Pink" or "Prince" can't block a
search for pink lingerie; a few superstars with word names are added by
hand (WORD_NAMES).

    python tools/build_celebrities.py
"""

import os
import re
import sys
import time
import unicodedata

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "assets", "celebrities.txt")
HEADERS = {"User-Agent": "CaptionCreator-celebrity-list/1.0 (offline build script)",
           "Accept": "application/sparql-results+json"}

# Wikidata occupation -> minimum number of Wikipedia editions.
OCCUPATIONS = {
    "Q33999": 55,      # actor
    "Q10800557": 55,   # film actor
    "Q10798782": 55,   # television actor
    "Q177220": 55,     # singer
    "Q639669": 60,     # musician
    "Q36834": 70,      # composer
    "Q4610556": 45,    # model
    "Q947873": 50,     # television presenter
    "Q245068": 50,     # comedian
    "Q17125263": 30,   # YouTuber
    "Q94791573": 25,   # Instagram influencer
    "Q15077007": 25,   # podcaster
    "Q488111": 30,     # pornographic actor (only the biggest names)
    "Q2066131": 90,    # athlete
    "Q937857": 90,     # association football player
    "Q3665646": 70,    # basketball player
    "Q10833314": 70,   # tennis player
    "Q2526255": 80,    # film director
    "Q3282637": 80,    # film producer
    "Q130857": 60,     # disc jockey
    "Q183945": 60,     # record producer
    "Q753110": 60,     # songwriter
}
# Politicians: a much higher bar (and born 1930 or later).
POLITICIAN_MIN = 160

# Superstars known by a single name that is also an ordinary word.
WORD_NAMES = {"madonna", "cher", "shakira", "beyonce", "eminem", "adele"}


def _query(sparql: str) -> list:
    for _attempt in range(4):
        try:
            resp = requests.get("https://query.wikidata.org/sparql", params={"query": sparql},
                                headers=HEADERS, timeout=120)
            resp.raise_for_status()
            return resp.json()["results"]["bindings"]
        except (requests.RequestException, ValueError) as e:
            print("  retrying:", e, file=sys.stderr)
            time.sleep(5)
    print("  gave up", file=sys.stderr)
    return []


def fetch() -> list:
    labels = {}
    for occ, minimum in OCCUPATIONS.items():
        rows = _query(f"""SELECT ?p ?label WHERE {{
          ?p wdt:P106 wd:{occ}; wdt:P31 wd:Q5; wikibase:sitelinks ?s.
          FILTER(?s >= {minimum})
          ?p rdfs:label ?label FILTER(lang(?label) = "en")
        }}""")
        for b in rows:
            labels[b["p"]["value"]] = b["label"]["value"]
        print(f"{occ}: {len(rows)} (total {len(labels)})")
        time.sleep(1)
    rows = _query(f"""SELECT ?p ?label WHERE {{
      ?p wdt:P106 wd:Q82955; wdt:P31 wd:Q5; wikibase:sitelinks ?s.
      FILTER(?s >= {POLITICIAN_MIN})
      ?p wdt:P569 ?born FILTER(YEAR(?born) >= 1930)
      ?p rdfs:label ?label FILTER(lang(?label) = "en")
    }}""")
    for b in rows:
        labels[b["p"]["value"]] = b["label"]["value"]
    print(f"politicians: {len(rows)} (total {len(labels)})")
    return list(labels.values())


def normalize(name: str) -> str:
    """Lowercase ASCII words: "Beyoncé (singer)" -> "beyonce"."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"\s*\(.*?\)", "", name)
    name = re.sub(r"[^A-Za-z0-9' .-]", " ", name).lower()
    name = re.sub(r"[.\-]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def build(labels: list) -> set:
    with open(os.path.join(REPO, "assets", "english_words.txt"), encoding="utf-8") as f:
        words = {line.strip().lower() for line in f}
    names = set(WORD_NAMES)
    for label in labels:
        n = normalize(label)
        if not n or re.fullmatch(r"q\d+", n):
            continue
        parts = n.split()
        if len(parts) == 1:
            if len(n) >= 4 and n not in words:
                names.add(n)
            continue
        names.add(n)
        # "samuel l jackson" is also written "samuel jackson"
        short = " ".join(p for p in parts if len(p) > 1)
        if short != n and len(short.split()) >= 2:
            names.add(short)
    return names


def main() -> None:
    names = build(fetch())
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Big-name celebrities (lowercase) — Find Online skips results that name them.\n"
                "# Built from Wikidata (CC0) by tools/build_celebrities.py.\n")
        for n in sorted(names):
            f.write(n + "\n")
    print(f"{len(names)} names -> {OUT}")


if __name__ == "__main__":
    main()
