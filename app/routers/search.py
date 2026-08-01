from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import reccobeats_client, schemas, spotify_art
from ..database import get_db

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=list[schemas.SearchResult])
def search_songs(
    q: str = Query(min_length=1),
    artist: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    results = reccobeats_client.search_tracks(q, limit=8, artist=artist)
    urls = [r["spotify_url"] for r in results if r["spotify_url"]]
    thumbnails = spotify_art.get_thumbnails_bulk(db, urls)

    return [
        schemas.SearchResult(
            reccobeats_id=r["reccobeats_id"],
            title=r["title"],
            artist=r["artist"],
            spotify_url=r["spotify_url"],
            thumbnail_url=thumbnails.get(r["spotify_url"]),
        )
        for r in results
    ]
