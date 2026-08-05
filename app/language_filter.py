"""Heuristic English-only filter applied to recommendation candidates.

There's no language field anywhere in ReccoBeats' data (checked every endpoint we use --
tracks, artists, audio-features, recommendations -- none of them carry it), and no lyrics
data either, so this can only ever be a heuristic guess from the track title, not a
guarantee about the actual lyrics.

Checks, in order:

1. Reject anything containing non-Latin script characters (Cyrillic, CJK, Hangul, Arabic,
   Hebrew, Devanagari, Thai) in the title or artist. Cheap, and essentially zero false
   positives.
2. Reject titles with fewer than 2 alphabetic characters (e.g. a purely numeric title like
   DAVICHI's "8282"). There's no text to classify, so there's nothing to verify it's
   English from -- default to rejecting rather than assuming.
3. Run TWO independent language-id models (langid, fastText) over the title alone -- never
   mixed with the artist, since artist/DJ names are frequently stylized in English
   regardless of the song's actual language (e.g. "DJ Trick Z"), which was dragging
   genuinely non-English titles across the line into "English." Only accept if both agree
   the title is English.
4. Both models above turned out to be close to a coin flip on short titles specifically --
   verified empirically (see the project's test notes): real English words like "Numb" and
   "Yesterday" get misclassified as non-English about as often as real non-English words
   like "Amor" and "Mond" get misclassified *as* English. Neither model has enough text to
   work with. So for short titles (<=2 words, or <=6 letters), the artist name is brought
   back in as a *corroborating veto*, not a decider: if both models independently agree the
   ARTIST name is the same specific non-English language, that's treated as strong enough
   evidence to reject even though the title alone looked English. This is what actually
   catches cases like "Al Natural" by Enigma Norteño and "OK" by Rammstein that slipped
   through the title-only check. It does NOT reject short titles just because the artist
   name looks foreign in only one of the two models, or looks foreign but ambiguous (e.g.
   "MHD", "Loi") -- that would start rejecting real English songs by artists with
   non-English-looking names (tested against Ed Sheeran, Justin Bieber, Beyonce, etc. with
   zero false rejections).
5. Separately, a different failure mode: romanized titles from non-Latin-script languages
   (Hindi, Punjabi, etc. written in the Latin alphabet) can read as plausible-enough English
   to fool BOTH models confidently -- caught live with "Garaj Garaj Jugalbandi" by
   Shankar-Ehsaan-Loy, which both langid and fastText called English with high confidence
   even though none of its words are English. Neither model is actually checking "is this a
   real English word," they're just pattern-matching character sequences. As a check that
   *is* directly about that: every title (regardless of length) is required to contain at
   least one real English word (checked against a bundled dictionary, with basic suffix
   handling for plurals/verb forms). This only rejects titles with ZERO recognizable English
   words, so it's conservative -- verified against a large set of real English titles with
   only two edge-case false rejections (a stylized all-consonant title, and a title that's
   only a brand name not in a dictionary), which is an acceptable cost for catching genuine
   leaks given this project's stated priority (zero non-English leakage over max recall).

Still a heuristic, not a certainty -- a short, ambiguous title by an artist whose name is
*also* ambiguous in both models (e.g. a single foreign word title from an artist whose name
doesn't read as clearly non-English to either model) can still slip through. That's a real
limitation of judging language from title/artist text alone, with no lyrics data available.
"""

import re
import warnings
from pathlib import Path

import fasttext
import langid

_WORD_LIST_PATH = Path(__file__).resolve().parent / "models_data" / "english_words.txt"
with _WORD_LIST_PATH.open(encoding="utf-8", errors="ignore") as f:
    _ENGLISH_WORDS = {line.strip().lower() for line in f if line.strip()}

_TITLE_WORD_PATTERN = re.compile(r"[A-Za-z']+")
# (suffix, characters to strip) -- cheap normalization so plurals/verb forms ("hours",
# "believin'") match their base dictionary entry ("hour", "believe") without a full stemmer.
_SUFFIX_STRIPS = [("'s", 2), ("ies", 3), ("es", 2), ("ing", 3), ("ed", 2), ("s", 1)]
# Splits a title into its "core" (before any parenthetical/bracketed suffix, dash-separated
# clause, or colon-separated subtitle) -- e.g. "Deewangi (Original Score)" -> "Deewangi".
_TITLE_CORE_SPLIT = re.compile(r"\s*[(\[]|\s+-\s+|\s*[:–—]\s*")


def _is_english_word(word: str) -> bool:
    w = word.lower().strip("'")
    if w in _ENGLISH_WORDS:
        return True
    for suffix, strip_len in _SUFFIX_STRIPS:
        if w.endswith(suffix) and len(w) - strip_len >= 2:
            base = w[:-strip_len]
            if base in _ENGLISH_WORDS:
                return True
            if suffix == "ies" and (base + "y") in _ENGLISH_WORDS:
                return True
    return False


def _has_any_english_word(title: str) -> bool:
    """False only if the title has real words (len >= 3) and NONE of them are English."""
    words = [w for w in _TITLE_WORD_PATTERN.findall(title) if len(w) >= 3]
    if not words:
        return True
    return any(_is_english_word(w) for w in words)


def _title_core(title: str) -> str:
    """The lead clause of the title, stripped of any trailing "(feat. X)"-style suffix.

    Non-English titles are sometimes packaged with an English parenthetical or
    dash-separated subtitle (e.g. a Bollywood track released as "Deewangi (Original
    Score)", or "Madhosh - From 'Toxic'") -- that suffix alone can carry enough English
    text to pass `_has_any_english_word` on the full title even though the actual title
    is not English. Checking the core separately closes that gap.
    """
    core = _TITLE_CORE_SPLIT.split(title, maxsplit=1)[0].strip()
    return core or title

_NON_LATIN_PATTERN = re.compile(
    "["
    "Ѐ-ӿ"  # Cyrillic
    "一-鿿"  # CJK Unified Ideographs
    "぀-ヿ"  # Hiragana / Katakana
    "가-힯"  # Hangul
    "؀-ۿ"  # Arabic
    "֐-׿"  # Hebrew
    "ऀ-ॿ"  # Devanagari
    "฀-๿"  # Thai
    "]"
)

# Any Unicode letter -- used to check whether a title has enough text to classify at all.
_ALPHA_PATTERN = re.compile(r"[^\W\d_]", re.UNICODE)

# Below this many words, or this many letters, langid/fastText degrade to near-random on
# real test data -- see module docstring. Below the threshold, the artist-veto kicks in.
_SHORT_TITLE_MAX_WORDS = 2
_SHORT_TITLE_MAX_LETTERS = 6

_MODEL_PATH = Path(__file__).resolve().parent / "models_data" / "lid.176.ftz"

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    _fasttext_model = fasttext.load_model(str(_MODEL_PATH))


def _fasttext_lang(text: str) -> str:
    labels, _ = _fasttext_model.predict(text.replace("\n", " "), k=1)
    return labels[0].replace("__label__", "")


def _langid_lang(text: str) -> str:
    lang, _score = langid.classify(text)
    return lang


def _alpha_char_count(text: str) -> int:
    return len(_ALPHA_PATTERN.findall(text))


def is_english(title: str, artist: str) -> bool:
    combined = f"{title} {artist}".strip()
    if not combined:
        return False
    if _NON_LATIN_PATTERN.search(combined):
        return False
    if not title.strip():
        return False
    if _alpha_char_count(title) < 2:
        return False

    if _langid_lang(title) != "en":
        return False
    if _fasttext_lang(title) != "en":
        return False
    if not _has_any_english_word(title):
        return False
    if not _has_any_english_word(_title_core(title)):
        return False

    is_short = (
        len(title.split()) <= _SHORT_TITLE_MAX_WORDS or _alpha_char_count(title) <= _SHORT_TITLE_MAX_LETTERS
    )
    if is_short and artist.strip():
        artist_langid = _langid_lang(artist)
        artist_fasttext = _fasttext_lang(artist)
        if artist_langid != "en" and artist_langid == artist_fasttext:
            return False

    return True
