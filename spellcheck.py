"""Spell check and lightweight grammar hints for the caption box.

Spelling uses the bundled ~370k-word English list (assets/english_words.txt)
plus common given names (assets/given_names.txt) and the user's own
dictionary (user_dictionary.txt next to the app). Grammar hints are a small
set of reliable rules — repeated words, "i", sentence capitals, a/an,
"could of", spacing around punctuation — not a full grammar checker; the AI
assistant's "Fix spelling & grammar" covers the rest. Pure Python, no Tk.
"""

import difflib
import os
import re
import threading
from typing import Optional

import app_paths

USER_DICT_PATH = os.path.join(app_paths.BASE_DIR, "user_dictionary.txt")

_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
_CONTRACTION_SUFFIXES = ("n't", "'s", "'re", "'ll", "'ve", "'d", "'m")
_LETTERS = "abcdefghijklmnopqrstuvwxyz"

_lock = threading.Lock()
_words: Optional[frozenset] = None
_user_words: set = set()


def _load_list(name: str) -> set:
    path = os.path.join(app_paths.RESOURCE_DIR, "assets", name)
    try:
        with open(path, encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except OSError:
        return set()


def _dictionary() -> frozenset:
    """Loaded on first use (~0.3s) and shared after that."""
    global _words
    with _lock:
        if _words is None:
            words = _load_list("english_words.txt") | _load_list("given_names.txt")
            # A few everyday words the bundled list is missing.
            words |= {"ok", "okay", "email", "emails", "online", "selfie", "selfies",
                      "tg", "lol", "omg", "lingerie", "bimbo", "bimbos", "hypnotized",
                      "feminized", "feminine", "feminize", "makeup", "boyfriend",
                      "girlfriend", "smartphone", "instagram"}
            _words = frozenset(words)
            try:
                with open(USER_DICT_PATH, encoding="utf-8") as f:
                    _user_words.update(line.strip().lower() for line in f if line.strip())
            except OSError:
                pass
        return _words


def available() -> bool:
    return bool(_dictionary())


def add_to_dictionary(word: str) -> None:
    word = word.strip().lower().replace("’", "'")
    if not word:
        return
    _dictionary()
    _user_words.add(word)
    try:
        with open(USER_DICT_PATH, "a", encoding="utf-8") as f:
            f.write(word + "\n")
    except OSError:
        pass


# Common misspellings the bundled word list happens to include as entries.
_KNOWN_MISSPELLINGS = {"wierd", "untill", "persue", "publically", "wich", "alot", "thier",
                       "recieve", "seperate", "definately", "occured", "teh", "freind",
                       "becuase", "realy", "tommorow", "begining", "truely"}


def is_known(word: str) -> bool:
    words = _dictionary()
    if not words:
        return True  # no dictionary: never flag anything
    w = word.lower().replace("’", "'")
    if w in _KNOWN_MISSPELLINGS and w not in _user_words:
        return False
    if w in words or w in _user_words or len(w) <= 1:
        return True
    for suffix in _CONTRACTION_SUFFIXES:
        if w.endswith(suffix) and len(w) > len(suffix):
            base = w[:-len(suffix)]
            if base in words or base in _user_words or suffix == "n't" and base + "n" in words:
                return True
    if w.endswith("'") and w[:-1] in words:  # plural possessive: girls'
        return True
    return False


def misspellings(text: str) -> list:
    """[(start, end, word)] character offsets of unknown words."""
    out = []
    for m in _WORD_RE.finditer(text):
        word = m.group()
        if not is_known(word):
            out.append((m.start(), m.end(), word))
    return out


def _edits1(word: str) -> set:
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = [a + b[1:] for a, b in splits if b]
    transposes = [a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1]
    replaces = [a + c + b[1:] for a, b in splits if b for c in _LETTERS]
    inserts = [a + c + b for a, b in splits for c in _LETTERS]
    return set(deletes + transposes + replaces + inserts)


def suggestions(word: str, limit: int = 6) -> list:
    """Likely corrections, closest first, matching the word's capitalization."""
    words = _dictionary()
    w = word.lower().replace("’", "'")
    near = {c for c in _edits1(w) if c in words and c not in _KNOWN_MISSPELLINGS}
    if not near and len(w) <= 12:
        near = {c2 for c1 in _edits1(w) for c2 in _edits1(c1)
                if c2 in words and c2 not in _KNOWN_MISSPELLINGS}
    def rank(c: str) -> tuple:
        # Same first letter, then swapped letters (same letters, e.g.
        # "Megna" -> "Megan"), then overall similarity.
        return (c[:1] != w[:1], sorted(c) != sorted(w),
                -difflib.SequenceMatcher(None, w, c).ratio(), abs(len(c) - len(w)), c)

    ranked = sorted(near, key=rank)[:limit]
    if word[:1].isupper():
        ranked = [c.upper() if word.isupper() and len(word) > 1 else c.capitalize()
                  for c in ranked]
    return ranked


# --- grammar hints ----------------------------------------------------------------

_ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "st", "jr", "sr",
                  "approx", "min", "max", "no"}
_AN_EXCEPTIONS = {"hour", "hours", "honest", "honestly", "honor", "honour", "heir", "herb"}
_A_EXCEPTIONS = {"one", "once", "university", "unicorn", "uniform", "unique", "unit",
                 "united", "user", "used", "usual", "usually", "european", "euro", "useful"}
_PHRASE_FIXES = (
    (re.compile(r"\b(could|should|would|must|might) of\b", re.I),
     lambda m: f"{m.group(1)} have", "use “have”, not “of”"),
    (re.compile(r"\balot\b", re.I), lambda m: "a lot", "“a lot” is two words"),
    (re.compile(r"\b(your) welcome\b", re.I), lambda m: "you're welcome", "“you're welcome”"),
    (re.compile(r"\bthier\b", re.I), lambda m: "their", "spelling"),
)


def grammar_issues(text: str) -> list:
    """[(start, end, replacement, message)] for the rules described above."""
    issues = []

    for m in re.finditer(r"\b(\w+)\s+(\1)\b", text, re.I):
        if m.group(1).lower() not in ("had", "that", "is"):  # legit doubles
            issues.append((m.start(), m.end(), m.group(1), "repeated word"))

    for m in re.finditer(r"(?<![\w'’])i(?![\w'’])", text):
        issues.append((m.start(), m.end(), "I", "“I” is always capitalized"))

    for m in re.finditer(r"(?:^|(\S+)[.!?]\s+|\n\s*)([a-z])", text):
        prev = (m.group(1) or "").lower().strip("\"'“”‘’(")
        if prev in _ABBREVIATIONS or "." in prev:  # "e.g. this", "U.S. team"
            continue
        start = m.start(2)
        issues.append((start, start + 1, m.group(2).upper(), "start the sentence with a capital"))

    for m in re.finditer(r"\b([Aa]n?)\s+([A-Za-z]+)", text):
        art, nxt = m.group(1), m.group(2).lower()
        vowel_sound = nxt[0] in "aeiou"
        if nxt in _AN_EXCEPTIONS:
            vowel_sound = True
        elif nxt in _A_EXCEPTIONS:
            vowel_sound = False
        if art.lower() == "a" and vowel_sound:
            issues.append((m.start(1), m.end(1), art + "n", "use “an” before a vowel sound"))
        elif art.lower() == "an" and not vowel_sound:
            issues.append((m.start(1), m.end(1), art[0], "use “a” before a consonant sound"))

    for pattern, fix, message in _PHRASE_FIXES:
        for m in pattern.finditer(text):
            issues.append((m.start(), m.end(), fix(m), message))

    for m in re.finditer(r"[ \t]+([,.!?;:])", text):
        issues.append((m.start(), m.end(), m.group(1), "no space before punctuation"))
    for m in re.finditer(r"([,;:])(?=[A-Za-z])", text):
        issues.append((m.start(), m.end(), m.group(1) + " ", "add a space after punctuation"))
    # "really.Also" — only after a lowercase word, so "U.S." and "e.g." pass.
    for m in re.finditer(r"(?<=[a-z]{2})([.!?])(?=[A-Z][a-z])", text):
        issues.append((m.start(), m.end(), m.group(1) + " ", "add a space after punctuation"))
    for m in re.finditer(r"(?<=\S)  +(?=\S)", text):
        issues.append((m.start(), m.end(), " ", "double space"))

    issues.sort(key=lambda i: i[0])
    # Drop overlaps so one stretch of text only carries one fix.
    out, last_end = [], -1
    for issue in issues:
        if issue[0] >= last_end:
            out.append(issue)
            last_end = issue[1]
    return out
