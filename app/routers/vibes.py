import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import language_filter, models, musicbrainz_client, reccobeats_client, recommender, schemas, spotify_art
from ..database import get_db

router = APIRouter(prefix="/api/vibes", tags=["vibes"])

MIN_SEEDS = 3
MAX_API_SEEDS = 5  # ReccoBeats' /recommendation endpoint caps the `seeds` param at 5
RECS_PER_ROUND = 5
# Pulled larger than the 5 we actually return since the English-only filter (and dedupe
# against already-seen tracks) can knock out a sizeable chunk of any given pool.
CANDIDATE_POOL_SIZE = 80
MAX_FEATURE_WEIGHT = 5.0
# How many top (audio-features-only) candidates get MusicBrainz era/genre enrichment.
# Bounds worst-case "Generate recommendations" latency to roughly this many seconds
# (MusicBrainz allows ~1 request/second) regardless of how large the popularity-sliced
# pool is.
GENRE_ERA_SHORTLIST_SIZE = 15


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


def _ensure_genre_era(db: Session, isrcs: list[str]) -> dict[str, dict]:
    """Return {isrc: {"release_year": int|None, "genre_tags": dict}} for every ISRC,
    fetching + caching whatever's missing.

    Unlike _ensure_audio_features, this is deliberately sequential (see
    musicbrainz_client) -- MusicBrainz enforces roughly 1 request/second for
    unauthenticated clients, so fanning this out with a thread pool would just get
    every request past the first couple rejected.
    """
    unique_isrcs = list(dict.fromkeys(isrcs))  # de-dupe while preserving order
    if not unique_isrcs:
        return {}

    cached_rows = db.query(models.MusicBrainzCache).filter(models.MusicBrainzCache.isrc.in_(unique_isrcs)).all()
    cache = {row.isrc: row for row in cached_rows}

    missing = [isrc for isrc in unique_isrcs if isrc not in cache]
    for isrc in missing:
        result = musicbrainz_client.get_genre_and_era(isrc)
        if result is None:
            continue  # request itself failed (network/HTTP error) -- don't cache, worth retrying later
        row = models.MusicBrainzCache(
            isrc=isrc, release_year=result["release_year"], genre_tags=result["genre_tags"] or None
        )
        db.add(row)
        cache[isrc] = row
    if missing:
        db.commit()

    return {isrc: {"release_year": row.release_year, "genre_tags": row.genre_tags or {}} for isrc, row in cache.items()}


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


@router.delete("/{vibe_id}", status_code=204)
def delete_vibe(vibe_id: int, db: Session = Depends(get_db)):
    vibe = _get_vibe_or_404(db, vibe_id)
    db.delete(vibe)
    db.commit()


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


@router.put("/{vibe_id}/weights", response_model=schemas.VibeDetail)
def update_weights(vibe_id: int, payload: schemas.FeatureWeightsUpdate, db: Session = Depends(get_db)):
    vibe = _get_vibe_or_404(db, vibe_id)

    unknown = set(payload.weights) - set(recommender.ALL_FEATURES)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown feature(s): {', '.join(sorted(unknown))}")

    clamped = {
        feature: max(0.0, min(MAX_FEATURE_WEIGHT, value)) for feature, value in payload.weights.items()
    }
    vibe.feature_weights = {**(vibe.feature_weights or {}), **clamped}
    db.commit()
    db.refresh(vibe)
    return vibe


@router.put("/{vibe_id}/popularity", response_model=schemas.VibeDetail)
def update_popularity_fraction(
    vibe_id: int, payload: schemas.PopularityFractionUpdate, db: Session = Depends(get_db)
):
    vibe = _get_vibe_or_404(db, vibe_id)
    vibe.popularity_fraction = max(
        recommender.MIN_POPULARITY_FRACTION, min(recommender.MAX_POPULARITY_FRACTION, payload.popularity_fraction)
    )
    db.commit()
    db.refresh(vibe)
    return vibe


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

    # Enrich seeds with era/genre from MusicBrainz (via ISRC) before building the
    # centroid, so both dimensions are part of the vibe's definition from the start.
    seed_details = reccobeats_client.get_track_details_bulk(seed_ids)
    seed_isrcs = [d["isrc"] for d in seed_details.values() if d and d.get("isrc")]
    seed_genre_era = _ensure_genre_era(db, seed_isrcs)
    for sid in seed_ids:
        if sid not in seed_features_map:
            continue
        detail = seed_details.get(sid)
        genre_era = seed_genre_era.get(detail["isrc"]) if detail and detail.get("isrc") else None
        if genre_era:
            seed_features_map[sid]["era"] = genre_era["release_year"]
            seed_features_map[sid]["genre_tags"] = genre_era["genre_tags"]

    centroid = recommender.build_centroid(seed_features)

    # Rocchio-style feedback: nudge the centroid using every past "Rank Recs"
    # submission for this vibe, toward #1-2 picks and away from #4-5 picks. Always
    # recomputed from the base seed centroid above (not compounded across repeated
    # "generate more" calls) -- see recommender.apply_feedback_nudge.
    ranked_songs = [song for round_ in vibe.rounds for song in round_.songs if song.user_rank is not None]
    liked_ids = [song.reccobeats_id for song in ranked_songs if song.user_rank <= 2]
    disliked_ids = [song.reccobeats_id for song in ranked_songs if song.user_rank >= 4]
    liked_features = list(_ensure_audio_features(db, liked_ids).values())
    disliked_features = list(_ensure_audio_features(db, disliked_ids).values())
    centroid = recommender.apply_feedback_nudge(centroid, liked_features, disliked_features)

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
        if not language_filter.is_english(c["title"], c["artist"]):
            continue
        deduped[c["reccobeats_id"]] = c

    candidate_features = _ensure_audio_features(db, list(deduped.keys()))
    usable_features = {cid: feats for cid, feats in candidate_features.items() if cid in deduped}

    if not usable_features:
        raise HTTPException(
            status_code=502,
            detail="No usable English-language candidates came back. Try again, or add a few more seeds.",
        )

    # Restrict to the most popular fraction of the eligible pool *before* ranking by
    # centroid distance, so the top-5 we hand back are always drawn from well-known songs
    # (unless the user's turned the "Popularity" slider up to allow deeper cuts through).
    # Always keep at least enough candidates to fill a round, even if the fraction alone
    # would cut deeper than that.
    fraction = vibe.popularity_fraction or recommender.DEFAULT_POPULARITY_FRACTION
    pool_size = max(RECS_PER_ROUND, round(len(usable_features) * fraction))
    most_popular_ids = sorted(
        usable_features, key=lambda cid: deduped[cid].get("popularity", 0), reverse=True
    )[:pool_size]
    popular_features = {cid: usable_features[cid] for cid in most_popular_ids}

    weights = {**recommender.DEFAULT_WEIGHTS, **(vibe.feature_weights or {})}

    # MusicBrainz's ~1 req/sec limit makes enriching the whole popularity-sliced pool
    # (which can run into the dozens) too slow to be worth it -- most of those candidates
    # are audio-feature outliers that'd never make the final 5 anyway. So: do a coarse
    # audio-features-only ranking first (era/genre aren't fetched yet, so this pass
    # naturally skips them), take the strongest contenders from that, and only spend the
    # MusicBrainz budget enriching those before the real, final ranking below.
    provisional = recommender.rank_candidates(
        centroid, popular_features, deduped, weights=weights, top_n=min(GENRE_ERA_SHORTLIST_SIZE, len(popular_features))
    )
    shortlist_features = {r["reccobeats_id"]: popular_features[r["reccobeats_id"]] for r in provisional}

    candidate_isrcs = [deduped[cid]["isrc"] for cid in shortlist_features if deduped[cid].get("isrc")]
    candidate_genre_era = _ensure_genre_era(db, candidate_isrcs)
    for cid, feats in shortlist_features.items():
        isrc = deduped[cid].get("isrc")
        genre_era = candidate_genre_era.get(isrc) if isrc else None
        if genre_era:
            feats["era"] = genre_era["release_year"]
            feats["genre_tags"] = genre_era["genre_tags"]

    ranked = recommender.rank_candidates(centroid, shortlist_features, deduped, weights=weights, top_n=RECS_PER_ROUND)

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


@router.put("/{vibe_id}/rounds/{round_id}/rank", response_model=schemas.RoundOut)
def submit_round_rank(
    vibe_id: int, round_id: int, payload: schemas.RoundRankSubmission, db: Session = Depends(get_db)
):
    _get_vibe_or_404(db, vibe_id)

    round_ = db.get(models.RecommendationRound, round_id)
    if round_ is None or round_.vibe_id != vibe_id:
        raise HTTPException(status_code=404, detail="Round not found")

    songs_by_id = {song.id: song for song in round_.songs}
    if set(payload.song_ids) != set(songs_by_id):
        raise HTTPException(
            status_code=400,
            detail="Ranking must include exactly the songs in this round, each exactly once.",
        )

    for position, song_id in enumerate(payload.song_ids, start=1):
        songs_by_id[song_id].user_rank = position

    db.commit()
    db.refresh(round_)
    return round_
