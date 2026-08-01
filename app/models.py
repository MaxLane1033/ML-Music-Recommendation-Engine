from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Vibe(Base):
    __tablename__ = "vibes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    seeds: Mapped[list["SeedSong"]] = relationship(
        back_populates="vibe", cascade="all, delete-orphan", order_by="SeedSong.added_at"
    )
    rounds: Mapped[list["RecommendationRound"]] = relationship(
        back_populates="vibe", cascade="all, delete-orphan", order_by="RecommendationRound.round_number"
    )


class SeedSong(Base):
    __tablename__ = "seed_songs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vibe_id: Mapped[int] = mapped_column(ForeignKey("vibes.id"), nullable=False)
    reccobeats_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    artist: Mapped[str] = mapped_column(String, nullable=False)
    spotify_url: Mapped[str | None] = mapped_column(String, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    vibe: Mapped["Vibe"] = relationship(back_populates="seeds")


class RecommendationRound(Base):
    __tablename__ = "recommendation_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vibe_id: Mapped[int] = mapped_column(ForeignKey("vibes.id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    seed_count_at_time: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    vibe: Mapped["Vibe"] = relationship(back_populates="rounds")
    songs: Mapped[list["RecommendedSong"]] = relationship(
        back_populates="round", cascade="all, delete-orphan", order_by="RecommendedSong.rank"
    )


class RecommendedSong(Base):
    __tablename__ = "recommended_songs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("recommendation_rounds.id"), nullable=False)
    reccobeats_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    artist: Mapped[str] = mapped_column(String, nullable=False)
    spotify_url: Mapped[str | None] = mapped_column(String, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str] = mapped_column(String, nullable=False)

    round: Mapped["RecommendationRound"] = relationship(back_populates="songs")


class AudioFeatureCache(Base):
    """Local cache of ReccoBeats audio-features lookups, keyed by ReccoBeats track id."""

    __tablename__ = "audio_feature_cache"

    reccobeats_id: Mapped[str] = mapped_column(String, primary_key=True)
    acousticness: Mapped[float] = mapped_column(Float)
    danceability: Mapped[float] = mapped_column(Float)
    energy: Mapped[float] = mapped_column(Float)
    instrumentalness: Mapped[float] = mapped_column(Float)
    key: Mapped[int] = mapped_column(Integer)
    liveness: Mapped[float] = mapped_column(Float)
    loudness: Mapped[float] = mapped_column(Float)
    mode: Mapped[int] = mapped_column(Integer)
    speechiness: Mapped[float] = mapped_column(Float)
    tempo: Mapped[float] = mapped_column(Float)
    valence: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ArtCache(Base):
    """Local cache of Spotify oEmbed thumbnail lookups, keyed by Spotify track URL."""

    __tablename__ = "art_cache"

    spotify_url: Mapped[str] = mapped_column(String, primary_key=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
