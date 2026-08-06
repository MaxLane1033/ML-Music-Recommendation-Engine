from datetime import datetime

from pydantic import BaseModel, model_validator

from .recommender import DEFAULT_POPULARITY_FRACTION, DEFAULT_WEIGHTS


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
    user_rank: int | None = None

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
    feature_weights: dict[str, float] | None = None
    popularity_fraction: float | None = None

    class Config:
        from_attributes = True

    @model_validator(mode="after")
    def _fill_defaults(self) -> "VibeDetail":
        merged = dict(DEFAULT_WEIGHTS)
        merged.update(self.feature_weights or {})
        self.feature_weights = merged
        if self.popularity_fraction is None:
            self.popularity_fraction = DEFAULT_POPULARITY_FRACTION
        return self


class FeatureWeightsUpdate(BaseModel):
    weights: dict[str, float]


class PopularityFractionUpdate(BaseModel):
    popularity_fraction: float


class RoundRankSubmission(BaseModel):
    # Ordered list of that round's RecommendedSong ids, best-to-worst per the user
    # (index 0 -> user_rank 1, ..., last -> user_rank 5). Must be exactly the set of
    # song ids belonging to the round, no more, no less.
    song_ids: list[int]


class FeatureMeta(BaseModel):
    key: str
    label: str
    description: str
    default_weight: float
