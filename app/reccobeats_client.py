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
    }


def search_tracks(query: str, limit: int = 8, artist: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"searchText": query, "size": limit}
    if artist:
        params["artist"] = artist
    response = _client.get("/track/search", params=params)
    response.raise_for_status()
    content = response.json().get("content", [])
    return [_track_summary(t) for t in content]


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
    tracks = [_track_summary(t) for t in content]
    tracks.sort(key=lambda t: t["popularity"], reverse=True)
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
