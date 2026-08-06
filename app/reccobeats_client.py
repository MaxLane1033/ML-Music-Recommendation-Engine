"""Thin wrapper around the ReccoBeats public API (no API key required).

Every function returns plain dicts/lists (or None on a clean miss) so the
rest of the app never has to deal with HTTP or ReccoBeats' response
envelopes directly.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

BASE_URL = "https://api.reccobeats.com/v1"
TIMEOUT = 15.0
MAX_WORKERS = 10

_client = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers={"Accept": "application/json"})


def _track_summary(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "reccobeats_id": raw["id"],
        "title": raw.get("trackTitle") or raw.get("name") or "Unknown title",
        "artist": ", ".join(a["name"] for a in raw.get("artists", [])) or "Unknown artist",
        "spotify_url": raw.get("href"),
        "popularity": raw.get("popularity", 0),
        "isrc": raw.get("isrc"),
    }


def _dedupe_by_song(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse ReccoBeats' duplicate catalog entries for one song down to its single
    most popular entry, then sort what's left by popularity (ReccoBeats' own result
    order is neither deduped nor popularity-sorted).

    ReccoBeats -- wrapping Spotify's catalog -- lists the same recording separately per
    territory/label release, each with its own (often near-zero) popularity score. ISRC
    is the industry-standard unique-recording id and is present on virtually every
    result, so it's a reliable grouping key -- unlike title/artist text, it won't
    accidentally merge genuinely different versions (a remix or extended mix has its
    own ISRC and correctly stays separate). Falls back to a normalized title+artist key
    only for the rare result with no ISRC.
    """
    best: dict[Any, dict[str, Any]] = {}
    for t in tracks:
        key = t.get("isrc") or (t["title"].strip().lower(), t["artist"].strip().lower())
        current = best.get(key)
        if current is None or t.get("popularity", 0) > current.get("popularity", 0):
            best[key] = t
    return sorted(best.values(), key=lambda t: t.get("popularity", 0), reverse=True)


def search_tracks(query: str, limit: int = 8, artist: str | None = None) -> list[dict[str, Any]]:
    # Pull a larger raw pool than `limit` -- duplicate catalog entries for the same song
    # (see _dedupe_by_song) can otherwise eat most of a small result page.
    params: dict[str, Any] = {"searchText": query, "size": max(limit * 4, 30)}
    if artist:
        params["artist"] = artist
    response = _client.get("/track/search", params=params)
    response.raise_for_status()
    content = response.json().get("content", [])
    tracks = _dedupe_by_song([_track_summary(t) for t in content])
    return tracks[:limit]


def search_artists(query: str, limit: int = 8) -> list[dict[str, Any]]:
    response = _client.get("/artist/search", params={"searchText": query, "size": limit})
    response.raise_for_status()
    content = response.json().get("content", [])
    return [{"artist_id": a["id"], "name": a["name"], "spotify_url": a.get("href")} for a in content]


def get_artist_tracks(artist_id: str, limit: int = 25) -> list[dict[str, Any]]:
    """This endpoint doesn't support sorting, so we pull a larger page and sort by popularity ourselves."""
    response = _client.get(f"/artist/{artist_id}/track", params={"size": 50})
    response.raise_for_status()
    content = response.json().get("content", [])
    tracks = _dedupe_by_song([_track_summary(t) for t in content])
    return tracks[:limit]


def get_audio_features(reccobeats_id: str) -> dict[str, Any] | None:
    response = _client.get(f"/track/{reccobeats_id}/audio-features")
    if response.status_code >= 400:
        return None
    data = response.json()
    return {
        "acousticness": data["acousticness"],
        "danceability": data["danceability"],
        "energy": data["energy"],
        "instrumentalness": data["instrumentalness"],
        "key": data["key"],
        "liveness": data["liveness"],
        "loudness": data["loudness"],
        "mode": data["mode"],
        "speechiness": data["speechiness"],
        "tempo": data["tempo"],
        "valence": data["valence"],
    }


def get_audio_features_bulk(reccobeats_ids: list[str]) -> dict[str, dict[str, Any] | None]:
    """Fetch audio features for many tracks concurrently. Missing/failed lookups map to None."""
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = pool.map(get_audio_features, reccobeats_ids)
    return dict(zip(reccobeats_ids, results))


def get_recommendations(
    seed_ids: list[str],
    target_features: dict[str, float],
    size: int = 50,
    feature_weight: float = 2.0,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "size": size,
        "seeds": seed_ids,
        "featureWeight": feature_weight,
        **target_features,
    }
    response = _client.get("/track/recommendation", params=params)
    response.raise_for_status()
    content = response.json().get("content", [])
    return [_track_summary(t) for t in content]
