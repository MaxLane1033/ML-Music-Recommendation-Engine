"""Cover art lookup via Spotify's public oEmbed endpoint (no API key required).

ReccoBeats does not return album artwork, but every track it returns carries
a Spotify track URL, and Spotify's oEmbed endpoint will hand back a thumbnail
for any public track URL with no authentication.
"""

from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy.orm import Session

from . import models

OEMBED_URL = "https://open.spotify.com/oembed"
TIMEOUT = 10.0
MAX_WORKERS = 10

_client = httpx.Client(timeout=TIMEOUT)


def _fetch_thumbnail(spotify_url: str) -> str | None:
    try:
        response = _client.get(OEMBED_URL, params={"url": spotify_url})
        if response.status_code >= 400:
            return None
        return response.json().get("thumbnail_url")
    except httpx.HTTPError:
        return None


def get_thumbnail(db: Session, spotify_url: str | None) -> str | None:
    if not spotify_url:
        return None

    cached = db.get(models.ArtCache, spotify_url)
    if cached is not None:
        return cached.thumbnail_url

    thumbnail_url = _fetch_thumbnail(spotify_url)
    db.add(models.ArtCache(spotify_url=spotify_url, thumbnail_url=thumbnail_url))
    db.commit()
    return thumbnail_url


def get_thumbnails_bulk(db: Session, spotify_urls: list[str]) -> dict[str, str | None]:
    """Resolve thumbnails for many tracks, hitting the network only for cache misses."""
    result: dict[str, str | None] = {}
    to_fetch: list[str] = []

    for url in spotify_urls:
        cached = db.get(models.ArtCache, url) if url else None
        if cached is not None:
            result[url] = cached.thumbnail_url
        elif url:
            to_fetch.append(url)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            fetched = list(pool.map(_fetch_thumbnail, to_fetch))
        for url, thumb in zip(to_fetch, fetched):
            db.add(models.ArtCache(spotify_url=url, thumbnail_url=thumb))
            result[url] = thumb
        db.commit()

    return result
