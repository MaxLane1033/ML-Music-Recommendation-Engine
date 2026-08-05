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
from typing import TypedDict

FeatureDict = dict[str, float]

# (min, max) for every linearly-scaled feature, per ReccoBeats' documentation.
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
}
LINEAR_FEATURES = list(LINEAR_FEATURE_BOUNDS)

# Every dimension folded into the distance calculation, including the
# specially-handled `key` (circular) and `mode` (binary, already 0-1).
ALL_FEATURES = LINEAR_FEATURES + ["key", "mode"]

DEFAULT_WEIGHTS: dict[str, float] = {feature: 1.0 for feature in ALL_FEATURES}

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


@dataclass
class Centroid:
    linear: dict[str, float]  # feature -> normalized [0, 1] mean
    mode: float  # fraction of seeds in major key, already [0, 1]
    key: float | None  # circular mean position on the pitch wheel, or None if no seed had a detected key


def build_centroid(seed_features: list[FeatureDict]) -> Centroid:
    if not seed_features:
        raise ValueError("Need at least one seed's audio features to build a centroid.")

    linear = {
        feature: sum(normalize_linear(feature, f[feature]) for f in seed_features) / len(seed_features)
        for feature in LINEAR_FEATURES
    }
    mode = sum(f["mode"] for f in seed_features) / len(seed_features)
    key = circular_mean_key([f["key"] for f in seed_features])
    return Centroid(linear=linear, mode=mode, key=key)


def centroid_target_features(centroid: Centroid) -> dict[str, float]:
    """Raw-scale feature values (for ReccoBeats' /recommendation target-feature params)."""
    return {feature: round(denormalize_linear(feature, value), 4) for feature, value in centroid.linear.items()}


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
