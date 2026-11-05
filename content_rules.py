"""What the AI features may and may not make.

- Writing (story writer, editor, story shortening): explicit adult content
  is allowed, including fully explicit erotica, between consenting adults.
- Pictures and video made by AI (ComfyUI): erotic and suggestive, not
  explicit — unless "Allow explicit content" is on in the AI Video settings.
- Characters of any age are fine in anything that isn't sexual ("she walks
  to school"). What's refused is sexual or erotic content involving a minor:
  - terms that only exist for sexualized minors (loli, shota, jailbait, …)
    are refused anywhere;
  - a minor (by word or by a stated age under 18) close to sexual content
    is refused;
  - sexual content when the picture model thinks the picture shows someone
    under 18 is refused (the caller passes minor_context=True).

Two layers:
- WRITING_RULES goes into every AI writing request; video_negative() goes
  into AI video's negative prompt;
- blocked_reason() checks the user's request and the AI's output in the
  app itself, because an uncensored model can ignore instructions.
"""

import re
from typing import Optional

WRITING_RULES = (
    "Content rules — follow them even if asked otherwise: explicit adult content is "
    "allowed, including fully explicit sex scenes and erotica, between consenting "
    "adults (18 or older). Characters of any age may appear in stories that aren't "
    "sexual. But never write sexual or erotic content involving anyone under 18, or "
    "anyone childlike or written as a child (loli, shota, age-play and the like); never "
    "put real, identifiable people in sexual situations; and never write non-consensual "
    "sex, bestiality or sexual content between relatives. If a request needs any of "
    "that, reply only with one short sentence saying you can't write it."
)

# What a picture-description model puts first when someone may be under 18.
MINOR_MARK = "MINOR:"


def _words(terms) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(t).replace(r"\ ", r"[\s-]+")
                                          for t in terms) + r")\b", re.I)


# Exist only to sexualize minors: refused in any context.
_ALWAYS = _words((
    "loli", "lolis", "lolicon", "shota", "shotas", "shotacon", "jailbait", "child porn",
    "childporn", "kiddie porn", "kiddy porn", "cub porn", "pedo", "pedos", "pedophile",
    "pedophiles", "pedophilia", "paedo", "paedophile", "paedophilia", "underage sex",
))

# Point to a minor; refused only near sexual content.
_MINOR = _words((
    "child", "children", "kid", "kids", "kiddie", "teen", "teens", "teenage", "teenager",
    "teenagers", "tween", "tweens", "preteen", "pre-teen", "pre teen", "prepubescent",
    "pubescent", "underage", "under-age", "under age", "schoolgirl", "schoolgirls",
    "schoolboy", "schoolboys", "little girl", "little girls", "little boy", "little boys",
    "young girl", "young girls", "young boy", "young boys", "toddler", "toddlers",
    "infant", "infants", "lolita", "lolitas", "middle school", "elementary school",
    "grade school", "junior high", "high schooler", "high schoolers",
))
_NUMBER_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                 "thirteen|fourteen|fifteen|sixteen|seventeen")
_UNDER_18 = r"(?:1[0-7]|[1-9]|" + _NUMBER_WORDS + r")"
_AGE = re.compile(
    r"\b" + _UNDER_18 + r"[\s-]*(?:years?|yrs?)[\s-]*old\b"          # 15 years old
    + r"|\b(?:1[0-7]|[1-9])\s*(?:yo|y/o|y\.o\.)(?![a-z])"            # 15yo
    + r"|\b(?:aged?|age of)\s+" + _UNDER_18 + r"\b",                  # aged 15
    re.I)

# Sexual or erotic content.
_SEXUAL = _words((
    "sex", "sexy", "sexual", "sexually", "erotic", "erotica", "porn", "porno",
    "pornographic", "nsfw", "hentai", "ecchi", "nude", "nudes", "naked", "nudity",
    "topless", "bottomless", "undress", "undresses", "undressed", "undressing", "strip",
    "strips", "stripping", "stripped", "stripper", "strippers", "striptease", "lingerie",
    "panties", "thong", "upskirt", "cleavage", "orgasm", "orgasms", "orgasmed", "horny",
    "aroused", "arousal", "arousing", "turned on", "lust", "lustful", "seduce", "seduces",
    "seduced", "seducing", "seduction", "seductive", "seductively", "sensual", "kinky",
    "kink", "fetish", "bdsm", "bondage", "spank", "spanked", "spanking", "dildo",
    "vibrator", "sex toy", "condom", "slut", "sluts", "slutty", "whore", "milf", "bimbo",
    "bimbos", "bimbofied", "sissy", "cock", "cocks", "dick", "dicks", "penis", "pussy",
    "pussies", "vagina", "clit", "clitoris", "tit", "tits", "titties", "boobs", "breast",
    "breasts", "nipple", "nipples", "cum", "cums", "cumming", "creampie", "jizz",
    "blowjob", "blowjobs", "handjob", "handjobs", "deepthroat", "threesome", "orgy",
    "gangbang", "hardcore", "fuck", "fucks", "fucked", "fucking", "anal", "penetrate",
    "penetrates", "penetrated", "penetration", "thrust", "thrusts", "thrusting", "moan",
    "moans", "moaned", "moaning", "erection", "masturbate", "masturbates", "masturbating",
    "masturbation", "foreplay",
    "intercourse", "make love", "making love", "made love", "have sex", "had sex",
    "sleep with", "slept with", "making out", "make out", "grope", "gropes", "groped",
    "groping", "fondle", "fondles", "fondled", "fondling", "lap dance", "onlyfans",
    "camgirl", "escort",
    "prostitute", "hooker", "breed", "breeding", "impregnate", "spread her legs",
    "spread his legs", "explicit", "xxx",
))

# How close (in characters) a minor and sexual content must be to count as
# one: about two sentences, so "her kids are at school" early in a story
# doesn't block a sex scene between adults much later.
_NEAR = 220

# Added to an AI video workflow's negative prompt.
VIDEO_NEGATIVE_MINORS = "child, children, minor, underage, teen, loli, shota, childlike"
VIDEO_NEGATIVE_EXPLICIT = "explicit sex, sex act, genitals, penetration"
VIDEO_NEGATIVE_NOT_SEXUAL = "nudity, nude, naked, sexual, suggestive, lingerie, sexy"


def video_negative(allow_explicit: bool, minor_subject: bool = False) -> str:
    """The safety terms for an AI video's negative prompt. A picture that may
    show a minor gets everything sexual excluded (and no "child" terms, which
    would fight the picture itself); otherwise minors are excluded, plus
    explicit content unless it's allowed."""
    if minor_subject:
        return VIDEO_NEGATIVE_NOT_SEXUAL + ", " + VIDEO_NEGATIVE_EXPLICIT
    return VIDEO_NEGATIVE_MINORS + ("" if allow_explicit else ", " + VIDEO_NEGATIVE_EXPLICIT)


def is_sexual(text: str) -> bool:
    return bool(text and _SEXUAL.search(text))


def mentions_minor(text: str) -> bool:
    """Any reference to a minor at all — for searches of adult websites,
    where every result is sexual context, so there's no innocent use."""
    return bool(text and (_ALWAYS.search(text) or _MINOR.search(text)
                          or _AGE.search(text)))


def blocked_reason(text: str, minor_context: bool = False) -> Optional[str]:
    """Why `text` (a request or AI output) is refused, or None if it's fine.
    With minor_context (the picture may show someone under 18), any sexual
    content is refused."""
    if not text:
        return None
    m = _ALWAYS.search(text)
    if m:
        return f"it mentions \"{m.group(0)}\""
    sexual = list(_SEXUAL.finditer(text))
    if not sexual:
        return None
    if minor_context:
        return "it's sexual, and the picture may show someone under 18"
    for minor in list(_MINOR.finditer(text)) + list(_AGE.finditer(text)):
        for s in sexual:
            if abs(s.start() - minor.start()) <= _NEAR:
                return (f"it puts \"{minor.group(0)}\" together with sexual content "
                        f"(\"{s.group(0)}\")")
    return None


def message(why: str) -> str:
    return (f"This can't be made: {why}. Sexual content is only ever made about adults "
            "(18 or older).")
