from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import reccobeats_client, schemas, spotify_art
from ..database import get_db

router = APIRouter(prefix="/api/artists", tags=["artists"])


@router.get("/search", response_model=list[schemas.ArtistResult])
def search_artists(q: str = Query(min_length=1)):
    results = reccobeats_client.search_artists(q, limit=8)
    return [schemas.ArtistResult(**r) for r in results]


@router.get("/{artist_id}/tracks", response_model=list[schemas.SearchResult])
def get_artist_tracks(artist_id: str, db: Session = Depends(get_db)):
    results = reccobeats_client.get_artist_tracks(artist_id, limit=25)
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
