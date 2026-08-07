"""Thin wrapper around the MusicBrainz API (no API key required, but rate-limited).

Used for the two pieces of metadata ReccoBeats doesn't have at all: a track's release
date (for the "era" feature) and its genre tags (for the "genre" feature). MusicBrainz
gives us BOTH from a single lookup -- querying a recording by ISRC with `inc=tags`
returns `first-release-date` unconditionally alongside genre-ish free-text tags,
verified live while building this client.

Rate limit: MusicBrainz enforces roughly 1 request/second for unauthenticated clients
and returns a 503 ("server is currently busy") if that's exceeded -- also verified live.
So, unlike reccobeats_client, calls here are always sequential, never pooled with a
ThreadPoolExecutor, with a minimum spacing enforced before every request.

A descriptive User-Agent is mandatory -- MusicBrainz blocks requests without one.
"""

import re
import threading
import time
from typing import Any

import httpx

BASE_URL = "https://musicbrainz.org/ws/2"
TIMEOUT = 15.0
MIN_REQUEST_INTERVAL = 1.05  # seconds -- MusicBrainz's ~1 req/sec limit, with a small margin
USER_AGENT = "VibeRecommendationEngine/1.0 (local dev project, no public deployment)"

_client = httpx.Client(
    base_url=BASE_URL, timeout=TIMEOUT, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
)

_rate_lock = threading.Lock()
_last_request_at = 0.0

_YEAR_PATTERN = re.compile(r"^(\d{4})")


def _throttle() -> None:
    global _last_request_at
    with _rate_lock:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def get_genre_and_era(isrc: str) -> dict[str, Any] | None:
    """Returns {"release_year": int | None, "genre_tags": {tag: count, ...}} for the
    given ISRC, or None if the lookup itself failed (network/HTTP error).

    A dict with an empty/None release_year and empty genre_tags is a valid, cacheable
    result -- it means MusicBrainz has no recording for this ISRC, which is distinct
    from "couldn't check" and shouldn't be retried on every call.
    """
    _throttle()
    try:
        response = _client.get(f"/isrc/{isrc}", params={"inc": "tags", "fmt": "json"})
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None

    recordings = response.json().get("recordings", [])
    if not recordings:
        return {"release_year": None, "genre_tags": {}}

    recording = recordings[0]
    release_year = None
    date = recording.get("first-release-date")
    if date:
        match = _YEAR_PATTERN.match(date)
        if match:
            release_year = int(match.group(1))

    genre_tags = {tag["name"]: tag["count"] for tag in recording.get("tags", []) if tag.get("count", 0) > 0}
    return {"release_year": release_year, "genre_tags": genre_tags}
