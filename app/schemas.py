from datetime import datetime

from pydantic import BaseModel


class VibeCreate(BaseModel):
    name: str


class VibeSummary(BaseModel):
    id: int
    name: str
    created_at: datetime

    class Config:
        from_attributes = True


class SearchResult(BaseModel):
    reccobeats_id: str
    title: str
    artist: str
    spotify_url: str | None
    thumbnail_url: str | None


class ArtistResult(BaseModel):
    artist_id: str
    name: str
    spotify_url: str | None


class SeedCreate(BaseModel):
    reccobeats_id: str
    title: str
    artist: str
    spotify_url: str | None = None
    thumbnail_url: str | None = None


class SeedOut(BaseModel):
    id: int
    reccobeats_id: str
    title: str
    artist: str
    spotify_url: str | None
    thumbnail_url: str | None

    class Config:
        from_attributes = True


class RecommendedSongOut(BaseModel):
    id: int
    reccobeats_id: str
    title: str
    artist: str
    spotify_url: str | None
    thumbnail_url: str | None
    rank: int
    match_score: float
    explanation: str

    class Config:
        from_attributes = True


class RoundOut(BaseModel):
    id: int
    round_number: int
    seed_count_at_time: int
    created_at: datetime
    songs: list[RecommendedSongOut]

    class Config:
        from_attributes = True


class VibeDetail(BaseModel):
    id: int
    name: str
    created_at: datetime
    seeds: list[SeedOut]
    rounds: list[RoundOut]

    class Config:
        from_attributes = True
