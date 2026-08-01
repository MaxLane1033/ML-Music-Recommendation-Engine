import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, reccobeats_client, recommender, schemas, spotify_art
from ..database import get_db

router = APIRouter(prefix="/api/vibes", tags=["vibes"])

MIN_SEEDS = 3
MAX_API_SEEDS = 5  # ReccoBeats' /recommendation endpoint caps the `seeds` param at 5
CANDIDATE_POOL_SIZE = 50


def _ensure_audio_features(db: Session, reccobeats_ids: list[str]) -> dict[str, recommender.FeatureDict]:
    """Return {id: features} for every id we could resolve, fetching + caching whatever is missing."""
    if not reccobeats_ids:
        return {}

    cached_rows = (
        db.query(models.AudioFeatureCache)
        .filter(models.AudioFeatureCache.reccobeats_id.in_(reccobeats_ids))
        .all()
    )
    cache = {row.reccobeats_id: row for row in cached_rows}

    missing = [rid for rid in reccobeats_ids if rid not in cache]
    if missing:
        fetched = reccobeats_client.get_audio_features_bulk(missing)
        for rid, features in fetched.items():
            if features is None:
                continue
            row = models.AudioFeatureCache(reccobeats_id=rid, **features)
            db.add(row)
            cache[rid] = row
        db.commit()

    result: dict[str, recommender.FeatureDict] = {}
    for rid in reccobeats_ids:
        row = cache.get(rid)
        if row is None:
            continue
        result[rid] = {
            "acousticness": row.acousticness,
            "danceability": row.danceability,
            "energy": row.energy,
            "instrumentalness": row.instrumentalness,
            "key": row.key,
            "liveness": row.liveness,
            "loudness": row.loudness,
            "mode": row.mode,
            "speechiness": row.speechiness,
            "tempo": row.tempo,
            "valence": row.valence,
        }
    return result


def _get_vibe_or_404(db: Session, vibe_id: int) -> models.Vibe:
    vibe = db.get(models.Vibe, vibe_id)
    if vibe is None:
        raise HTTPException(status_code=404, detail="Vibe not found")
    return vibe


@router.post("", response_model=schemas.VibeSummary)
def create_vibe(payload: schemas.VibeCreate, db: Session = Depends(get_db)):
    vibe = models.Vibe(name=payload.name.strip() or "Untitled vibe")
    db.add(vibe)
    db.commit()
    db.refresh(vibe)
    return vibe


@router.get("", response_model=list[schemas.VibeSummary])
def list_vibes(db: Session = Depends(get_db)):
    return db.query(models.Vibe).order_by(models.Vibe.created_at.desc()).all()


@router.get("/{vibe_id}", response_model=schemas.VibeDetail)
def get_vibe(vibe_id: int, db: Session = Depends(get_db)):
    return _get_vibe_or_404(db, vibe_id)


@router.post("/{vibe_id}/seeds", response_model=schemas.SeedOut)
def add_seed(vibe_id: int, payload: schemas.SeedCreate, db: Session = Depends(get_db)):
    vibe = _get_vibe_or_404(db, vibe_id)

    if any(s.reccobeats_id == payload.reccobeats_id for s in vibe.seeds):
        raise HTTPException(status_code=400, detail="That song is already a seed for this vibe.")

    features = _ensure_audio_features(db, [payload.reccobeats_id]).get(payload.reccobeats_id)
    if features is None:
        raise HTTPException(status_code=502, detail="ReccoBeats has no audio features for that track.")

    seed = models.SeedSong(
        vibe_id=vibe.id,
        reccobeats_id=payload.reccobeats_id,
        title=payload.title,
        artist=payload.artist,
        spotify_url=payload.spotify_url,
        thumbnail_url=payload.thumbnail_url,
    )
    db.add(seed)
    db.commit()
    db.refresh(seed)
    return seed


@router.delete("/{vibe_id}/seeds/{seed_id}", status_code=204)
def remove_seed(vibe_id: int, seed_id: int, db: Session = Depends(get_db)):
    seed = db.get(models.SeedSong, seed_id)
    if seed is None or seed.vibe_id != vibe_id:
        raise HTTPException(status_code=404, detail="Seed not found")
    db.delete(seed)
    db.commit()


@router.post("/{vibe_id}/generate", response_model=schemas.RoundOut)
def generate_recommendations(vibe_id: int, db: Session = Depends(get_db)):
    vibe = _get_vibe_or_404(db, vibe_id)

    if len(vibe.seeds) < MIN_SEEDS:
        raise HTTPException(
            status_code=400,
            detail=f"Add at least {MIN_SEEDS} seed songs before generating recommendations.",
        )

    seed_ids = [s.reccobeats_id for s in vibe.seeds]
    seed_features_map = _ensure_audio_features(db, seed_ids)
    seed_features = [seed_features_map[sid] for sid in seed_ids if sid in seed_features_map]

    if len(seed_features) < MIN_SEEDS:
        raise HTTPException(
            status_code=502,
            detail="Couldn't fetch audio features for enough seed songs. Try again in a moment.",
        )

    centroid = recommender.build_centroid(seed_features)
    target_features = recommender.centroid_target_features(centroid)

    # ReccoBeats caps `seeds` at 5 -- bias the candidate pool with the most recently added seeds.
    # Our own scoring below still weighs every seed the vibe has ever been given equally.
    api_seed_ids = seed_ids[-MAX_API_SEEDS:]

    try:
        candidates = reccobeats_client.get_recommendations(api_seed_ids, target_features, size=CANDIDATE_POOL_SIZE)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ReccoBeats recommendation request failed: {exc}") from exc

    already_seen = set(seed_ids)
    for round_ in vibe.rounds:
        for song in round_.songs:
            already_seen.add(song.reccobeats_id)

    deduped: dict[str, dict] = {}
    for c in candidates:
        if c["reccobeats_id"] in already_seen or c["reccobeats_id"] in deduped:
            continue
        deduped[c["reccobeats_id"]] = c

    candidate_features = _ensure_audio_features(db, list(deduped.keys()))
    usable_features = {cid: feats for cid, feats in candidate_features.items() if cid in deduped}

    if not usable_features:
        raise HTTPException(status_code=502, detail="ReccoBeats didn't return any usable new candidates. Try again.")

    ranked = recommender.rank_candidates(centroid, usable_features, deduped, top_n=5)

    round_ = models.RecommendationRound(
        vibe_id=vibe.id,
        round_number=len(vibe.rounds) + 1,
        seed_count_at_time=len(vibe.seeds),
    )
    db.add(round_)
    db.flush()

    thumbnails = spotify_art.get_thumbnails_bulk(db, [r["spotify_url"] for r in ranked if r["spotify_url"]])

    for i, r in enumerate(ranked, start=1):
        db.add(
            models.RecommendedSong(
                round_id=round_.id,
                reccobeats_id=r["reccobeats_id"],
                title=r["title"],
                artist=r["artist"],
                spotify_url=r["spotify_url"],
                thumbnail_url=thumbnails.get(r["spotify_url"]),
                rank=i,
                match_score=r["match_score"],
                explanation=r["explanation"],
            )
        )
    db.commit()
    db.refresh(round_)
    return round_
