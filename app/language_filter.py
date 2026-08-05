"""Heuristic English-only filter applied to recommendation candidates.

There's no language field anywhere in ReccoBeats' data (checked every endpoint we use --
tracks, artists, audio-features, recommendations -- none of them carry it), and no lyrics
data either, so this can only ever be a heuristic guess from the track title, not a
guarantee about the actual lyrics.

Three checks, in order:

1. Reject anything containing non-Latin script characters (Cyrillic, CJK, Hangul, Arabic,
   Hebrew, Devanagari, Thai) in the title or artist. Cheap, and essentially zero false
   positives.
2. Run TWO independent language-id models over the title alone (never the artist -- artist
   / DJ names are frequently stylized in English regardless of the song's actual language,
   e.g. stock-music-library aliases like "DJ Trick Z", and mixing that text in was dragging
   genuinely non-English titles across the line into "English").
3. Only accept the track as English if BOTH models agree. This was tuned against real
   misses pulled from live recommendation output, not just hand-picked examples: neither
   model alone was reliable enough on short, proper-noun-heavy song titles (langid.py
   alone let through "Se Vale Llorar", "Halleluja", "Se Vale Llorar" and others; fastText
   alone let through a different, overlapping set). Requiring both to agree fixed nearly
   all of those at the cost of occasionally rejecting an ambiguous English title (e.g.
   "Sierra Leone", "Novacane") -- a deliberate tradeoff, since the goal here is "don't show
   non-English songs," not "never wrongly exclude an English one."

Still a heuristic, not a certainty -- a handful of very short, ambiguous titles (e.g. a
single word that looks plausible in several languages) will still slip through either way.
"""

import re
import warnings
from pathlib import Path

import fasttext
import langid

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

_MODEL_PATH = Path(__file__).resolve().parent / "models_data" / "lid.176.ftz"

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    _fasttext_model = fasttext.load_model(str(_MODEL_PATH))


def _fasttext_lang(text: str) -> str:
    labels, _ = _fasttext_model.predict(text.replace("\n", " "), k=1)
    return labels[0].replace("__label__", "")


def is_english(title: str, artist: str) -> bool:
    combined = f"{title} {artist}".strip()
    if not combined:
        return False
    if _NON_LATIN_PATTERN.search(combined):
        return False
    if not title.strip():
        return False

    langid_lang, _ = langid.classify(title)
    if langid_lang != "en":
        return False

    return _fasttext_lang(title) == "en"
