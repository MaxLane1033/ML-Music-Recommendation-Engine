"""Core recommendation math.

ReccoBeats' own `/track/recommendation` endpoint supplies a *candidate pool*
(tracks broadly in the neighborhood of the seeds). Everything after that --
turning seed songs into a "vibe", scoring every candidate against it, and
explaining why a song was picked -- happens here, independent of whatever
ReccoBeats does internally.

Approach
--------
1. Every audio feature is min-max normalized to [0, 1] using ReccoBeats'
   documented bounds for that feature (tempo 0-250 BPM, loudness -60..2 dB,
   everything else already 0-1). This puts wildly different scales
   (BPM vs. a 0-1 confidence score) on equal footing before we compare them.
2. `key` (pitch class 0-11, or -1 if undetected) is circular, not linear --
   key 11 (B) and key 0 (C) are one semitone apart, not eleven. It's handled
   with proper circular mean/distance instead of naive averaging, which
   would otherwise pull a centroid toward the wrong side of the pitch wheel.
3. A vibe's "centroid" is the mean of its seed songs' normalized vectors --
   the geometric center of the vibe in feature space.
4. Candidates are scored by weighted Euclidean distance from that centroid.
   Weights default to 1.0 per feature (equal weighting) but the function
   signature already takes a per-feature weight map so per-feature
   importance sliders can be added later without changing this module.
"""

import math
from dataclasses import dataclass
from typing import Any, TypedDict

FeatureDict = dict[str, Any]

# (min, max) for every linearly-scaled feature, per ReccoBeats' documentation, plus
# "era" (release year -- from MusicBrainz, not ReccoBeats). era's bounds live here so it
# can reuse normalize_linear/denormalize_linear, but it's deliberately left out of
# LINEAR_FEATURES below -- unlike audio features (always present), a track's release
# year is often unavailable (MusicBrainz has no entry for it), so it's handled as an
# optional dimension alongside `key`, not folded into the always-present linear loop.
LINEAR_FEATURE_BOUNDS: dict[str, tuple[float, float]] = {
    "acousticness": (0.0, 1.0),
    "danceability": (0.0, 1.0),
    "energy": (0.0, 1.0),
    "instrumentalness": (0.0, 1.0),
    "liveness": (0.0, 1.0),
    "loudness": (-60.0, 2.0),
    "speechiness": (0.0, 1.0),
    "tempo": (0.0, 250.0),
    "valence": (0.0, 1.0),
    "era": (1900.0, 2030.0),
}
LINEAR_FEATURES = [f for f in LINEAR_FEATURE_BOUNDS if f != "era"]

# Every dimension folded into the distance calculation, including the specially-handled
# `key` (circular), `mode` (binary), `era` (linear but optional), and `genre` (tag-set
# overlap, optional -- both era and genre come from MusicBrainz and are only present
# when MusicBrainz has an entry for that ISRC).
ALL_FEATURES = LINEAR_FEATURES + ["key", "mode", "era", "genre"]

# Fraction of the eligible (English-filtered, deduped) candidate pool, sorted by
# popularity, that recommendations are drawn from. 1.0 removes the popularity
# restriction entirely (deep cuts allowed); smaller values restrict to progressively
# more mainstream tracks. User-adjustable per vibe via the "Popularity" slider.
DEFAULT_POPULARITY_FRACTION = 0.5
MIN_POPULARITY_FRACTION = 0.05
MAX_POPULARITY_FRACTION = 1.0

DEFAULT_WEIGHTS: dict[str, float] = {feature: 1.0 for feature in ALL_FEATURES}
# `mode` is binary (major/minor), so any mismatch contributes a full point to the
# weighted-distance sum -- the same as being *maximally* different on every continuous
# feature at once. Verified against several user-supplied "these songs are a similar
# vibe" pairs: whenever mode happened to match, match scores looked right (~85%);
# whenever it didn't, mode alone accounted for 45-77% of the total distance and dragged
# otherwise-close pairs down to ~65%, regardless of how close everything else was.
# Lowered so a mode mismatch behaves like one moderately-weighted feature rather than
# swamping the rest. Still fully user-adjustable via the "major/minor tonality" slider.
DEFAULT_WEIGHTS["mode"] = 0.25
# Explicitly requested as a light-touch tiebreaker, not a filter -- genre tags are
# free-text and crowd-sourced (via MusicBrainz), noisier than audio features, and only
# available for a fraction of tracks. Kept low by default; raise the slider for
# stricter genre matching.
DEFAULT_WEIGHTS["genre"] = 0.25

FEATURE_LABELS: dict[str, str] = {
    "acousticness": "acousticness",
    "danceability": "danceability",
    "energy": "energy",
    "instrumentalness": "instrumental feel",
    "liveness": "live-performance feel",
    "loudness": "loudness",
    "speechiness": "spoken-word feel",
    "tempo": "tempo",
    "valence": "mood (valence)",
    "key": "key",
    "mode": "major/minor tonality",
    "era": "era",
    "genre": "genre",
}

# (low-value phrase, high-value phrase) for building explanations.
FEATURE_DIRECTIONS: dict[str, tuple[str, str]] = {
    "acousticness": ("more electronic/produced", "more acoustic"),
    "danceability": ("less danceable", "more danceable"),
    "energy": ("lower energy", "higher energy"),
    "instrumentalness": ("more vocal-driven", "more instrumental"),
    "liveness": ("more studio-polished", "more live-sounding"),
    "loudness": ("quieter", "louder"),
    "speechiness": ("more melodic", "more spoken-word"),
    "tempo": ("slower", "faster"),
    "valence": ("darker/sadder", "brighter/happier"),
    "era": ("older", "newer"),
}

# Plain-English descriptions shown next to each feature's weight slider in the UI.
FEATURE_DESCRIPTIONS: dict[str, str] = {
    "acousticness": "How much the track leans on natural/acoustic instruments vs. electronic production (0 = fully electronic, 1 = fully acoustic).",
    "danceability": "How suitable the track is for dancing, based on tempo, rhythm stability, and beat strength.",
    "energy": "How intense and fast-paced the track feels (low = calm/mellow, high = intense/energetic).",
    "instrumentalness": "How likely the track has no vocals -- higher values mean more purely instrumental.",
    "liveness": "How likely the track was recorded in front of a live audience, rather than in a studio.",
    "loudness": "The track's overall loudness in decibels.",
    "speechiness": "How much spoken word is present, as opposed to singing or instrumental music.",
    "tempo": "The track's speed, in beats per minute (BPM).",
    "valence": "How positive the track's mood sounds (low = sad/dark, high = happy/upbeat).",
    "key": "How closely the track's musical key matches your seed songs (e.g. C major vs. A minor).",
    "mode": "Whether the track is in a major key (brighter, happier-sounding) or a minor key (moodier, more serious-sounding).",
    "era": "How close the track's release year is to your seed songs' (via MusicBrainz). Only applied when release-year data is available.",
    "genre": "How much the track's genre tags overlap with your seed songs' (via MusicBrainz). A light-touch tiebreaker by default -- raise it for stricter genre matching. Only applied when genre data is available.",
}


def feature_metadata() -> list[dict]:
    """Everything the frontend needs to render one weight slider per feature."""
    return [
        {
            "key": feature,
            "label": FEATURE_LABELS[feature],
            "description": FEATURE_DESCRIPTIONS[feature],
            "default_weight": DEFAULT_WEIGHTS[feature],
        }
        for feature in ALL_FEATURES
    ]


def normalize_linear(feature: str, value: float) -> float:
    lo, hi = LINEAR_FEATURE_BOUNDS[feature]
    clamped = max(lo, min(hi, value))
    return (clamped - lo) / (hi - lo)


def denormalize_linear(feature: str, normalized: float) -> float:
    lo, hi = LINEAR_FEATURE_BOUNDS[feature]
    return lo + normalized * (hi - lo)


def circular_mean_key(keys: list[int]) -> float | None:
    """Mean position on the 12-key pitch wheel. -1 ("no key detected") is ignored."""
    valid = [k for k in keys if k is not None and k >= 0]
    if not valid:
        return None
    angles = [k * 2 * math.pi / 12 for k in valid]
    sin_sum = sum(math.sin(a) for a in angles)
    cos_sum = sum(math.cos(a) for a in angles)
    mean_angle = math.atan2(sin_sum, cos_sum)
    if mean_angle < 0:
        mean_angle += 2 * math.pi
    return mean_angle / (2 * math.pi / 12)


def circular_key_distance(key_a: float, key_b: float) -> float:
    """Normalized distance on the pitch wheel: 0 = same key, 1 = maximally distant (tritone, 6 semitones)."""
    diff = abs(key_a - key_b) % 12
    diff = min(diff, 12 - diff)
    return diff / 6.0


def _genre_similarity(profile: dict[str, float], candidate_tags: dict[str, float]) -> float:
    """Weighted Jaccard-style overlap between a vibe's aggregated genre-tag profile and
    a single candidate's own tags. 1.0 = candidate's tags are entirely covered by
    well-represented vibe genres; 0.0 = no shared tags at all.
    """
    if not profile or not candidate_tags:
        return 0.0
    shared_weight = sum(profile.get(tag, 0) for tag in candidate_tags)
    total_weight = sum(profile.values()) + sum(
        weight for tag, weight in candidate_tags.items() if tag not in profile
    )
    return shared_weight / total_weight if total_weight else 0.0


@dataclass
class Centroid:
    linear: dict[str, float]  # feature -> normalized [0, 1] mean
    mode: float  # fraction of seeds in major key, already [0, 1]
    key: float | None  # circular mean position on the pitch wheel, or None if no seed had a detected key
    era: float | None  # normalized [0, 1] mean release year, or None if no seed had one
    genre_profile: dict[str, float] | None  # aggregated tag -> weight across seeds with genre data, or None


def build_centroid(seed_features: list[FeatureDict]) -> Centroid:
    if not seed_features:
        raise ValueError("Need at least one seed's audio features to build a centroid.")

    linear = {
        feature: sum(normalize_linear(feature, f[feature]) for f in seed_features) / len(seed_features)
        for feature in LINEAR_FEATURES
    }
    mode = sum(f["mode"] for f in seed_features) / len(seed_features)
    key = circular_mean_key([f["key"] for f in seed_features])

    eras = [normalize_linear("era", f["era"]) for f in seed_features if f.get("era") is not None]
    era = sum(eras) / len(eras) if eras else None

    genre_profile: dict[str, float] = {}
    has_genre_data = False
    for f in seed_features:
        tags = f.get("genre_tags")
        if tags:
            has_genre_data = True
            for tag, count in tags.items():
                genre_profile[tag] = genre_profile.get(tag, 0) + count

    return Centroid(linear=linear, mode=mode, key=key, era=era, genre_profile=genre_profile if has_genre_data else None)


def centroid_target_features(centroid: Centroid) -> dict[str, float]:
    """Raw-scale feature values (for ReccoBeats' /recommendation target-feature params)."""
    return {feature: round(denormalize_linear(feature, value), 4) for feature, value in centroid.linear.items()}


# Rocchio-style feedback: how far to nudge the centroid toward songs the user ranked
# #1-2 ("liked") and away from songs they ranked #4-5 ("disliked") in past rounds.
# "Medium-aggressive" per explicit request for a first pass -- liked songs pull about a
# third of the way, disliked songs push about half as hard in the other direction
# (pulling toward a positive example is more informative than pushing away from a
# negative one, which only rules things out). Meant to be tuned from here based on
# real testing, not treated as final.
FEEDBACK_LIKE_STRENGTH = 0.3
FEEDBACK_DISLIKE_STRENGTH = 0.15


def apply_feedback_nudge(
    centroid: Centroid,
    liked_features: list[FeatureDict],
    disliked_features: list[FeatureDict],
    like_strength: float = FEEDBACK_LIKE_STRENGTH,
    dislike_strength: float = FEEDBACK_DISLIKE_STRENGTH,
) -> Centroid:
    """Nudge a seed-based centroid using the user's past "Rank Recs" feedback.

    `liked_features`/`disliked_features` are the audio features of every song across
    every past round for this vibe that the user ranked #1-2 / #4-5. Always recomputed
    from the *original* seed centroid (not compounded across repeated calls), so
    repeated "generate more" clicks stay stable instead of drifting further each time --
    the nudge reflects the full ranking history so far, not an accumulation of nudges.

    Only linear audio features and `mode` are nudged in this first pass. `key` (circular,
    no well-defined "push away" direction), `era`, and `genre` are left exactly as the
    seeds define them -- feedback-driven tuning for those can follow once this is
    validated on the simpler, better-understood dimensions.
    """
    if not liked_features and not disliked_features:
        return centroid

    new_linear = dict(centroid.linear)
    new_mode = centroid.mode

    if liked_features:
        liked_centroid = build_centroid(liked_features)
        for feature in LINEAR_FEATURES:
            new_linear[feature] += like_strength * (liked_centroid.linear[feature] - centroid.linear[feature])
        new_mode += like_strength * (liked_centroid.mode - centroid.mode)

    if disliked_features:
        disliked_centroid = build_centroid(disliked_features)
        for feature in LINEAR_FEATURES:
            new_linear[feature] -= dislike_strength * (disliked_centroid.linear[feature] - centroid.linear[feature])
        new_mode -= dislike_strength * (disliked_centroid.mode - centroid.mode)

    new_linear = {feature: max(0.0, min(1.0, value)) for feature, value in new_linear.items()}
    new_mode = max(0.0, min(1.0, new_mode))

    return Centroid(
        linear=new_linear, mode=new_mode, key=centroid.key, era=centroid.era, genre_profile=centroid.genre_profile
    )


def weighted_distance(
    centroid: Centroid, candidate: FeatureDict, weights: dict[str, float] | None = None
) -> float:
    weights = weights or DEFAULT_WEIGHTS
    total = 0.0

    for feature in LINEAR_FEATURES:
        diff = normalize_linear(feature, candidate[feature]) - centroid.linear[feature]
        total += weights.get(feature, 1.0) * diff**2

    mode_diff = candidate["mode"] - centroid.mode
    total += weights.get("mode", 1.0) * mode_diff**2

    if centroid.key is not None and candidate["key"] is not None and candidate["key"] >= 0:
        key_diff = circular_key_distance(centroid.key, candidate["key"])
        total += weights.get("key", 1.0) * key_diff**2

    if centroid.era is not None and candidate.get("era") is not None:
        era_diff = normalize_linear("era", candidate["era"]) - centroid.era
        total += weights.get("era", 1.0) * era_diff**2

    if centroid.genre_profile and candidate.get("genre_tags"):
        genre_distance = 1.0 - _genre_similarity(centroid.genre_profile, candidate["genre_tags"])
        total += weights.get("genre", 1.0) * genre_distance**2

    return math.sqrt(total)


def max_possible_distance(weights: dict[str, float] | None = None) -> float:
    weights = weights or DEFAULT_WEIGHTS
    return math.sqrt(sum(weights.get(f, 1.0) for f in ALL_FEATURES))


def match_score(distance: float, weights: dict[str, float] | None = None) -> float:
    ceiling = max_possible_distance(weights)
    return round(max(0.0, 1.0 - distance / ceiling) * 100, 1)


def explain(centroid: Centroid, candidate: FeatureDict, weights: dict[str, float] | None = None) -> str:
    """Human-readable explanation built from the features closest to (and furthest from) the centroid.

    Features the user has weighted to 0 ("I don't care about this") are left out entirely -- both
    from the "closely matches" praise and the "though it's ___ than your seeds" contrast, since
    they played no part in the ranking.
    """
    weights = weights or DEFAULT_WEIGHTS
    deltas: list[tuple[str, float, float]] = []  # (feature, abs_normalized_diff, signed_normalized_diff)

    for feature in LINEAR_FEATURES:
        if weights.get(feature, 1.0) <= 0:
            continue
        candidate_norm = normalize_linear(feature, candidate[feature])
        signed = candidate_norm - centroid.linear[feature]
        deltas.append((feature, abs(signed), signed))

    if not deltas:
        return "matched using only de-emphasized features, so no single feature stands out."

    deltas.sort(key=lambda d: d[1])
    closest = [d for d in deltas if d[1] <= 0.12][:2]
    furthest = max(deltas, key=lambda d: weights.get(d[0], 1.0) * d[1])

    parts = []
    if closest:
        labels = " and ".join(FEATURE_LABELS[f] for f, _, _ in closest)
        parts.append(f"closely matches your vibe's {labels}")
    else:
        labels = " and ".join(FEATURE_LABELS[f] for f, _, _ in deltas[:2])
        parts.append(f"is the nearest overall match, mainly on {labels}")

    if furthest[1] > 0.35:
        feature, _, signed = furthest
        low_phrase, high_phrase = FEATURE_DIRECTIONS[feature]
        direction = high_phrase if signed > 0 else low_phrase
        parts.append(f"though it's {direction} than your seeds")

    return "; ".join(parts) + "."


class RankedCandidate(TypedDict):
    reccobeats_id: str
    title: str
    artist: str
    spotify_url: str | None
    match_score: float
    explanation: str


def rank_candidates(
    centroid: Centroid,
    candidates: dict[str, FeatureDict],
    candidate_meta: dict[str, dict],
    weights: dict[str, float] | None = None,
    top_n: int = 5,
) -> list[RankedCandidate]:
    """candidates: reccobeats_id -> audio features. candidate_meta: reccobeats_id -> {title, artist, spotify_url}."""
    scored = []
    for reccobeats_id, features in candidates.items():
        distance = weighted_distance(centroid, features, weights)
        scored.append((reccobeats_id, distance))

    scored.sort(key=lambda pair: pair[1])

    ranked: list[RankedCandidate] = []
    for reccobeats_id, distance in scored[:top_n]:
        meta = candidate_meta[reccobeats_id]
        ranked.append(
            RankedCandidate(
                reccobeats_id=reccobeats_id,
                title=meta["title"],
                artist=meta["artist"],
                spotify_url=meta["spotify_url"],
                match_score=match_score(distance, weights),
                explanation=explain(centroid, candidates[reccobeats_id], weights),
            )
        )
    return ranked
