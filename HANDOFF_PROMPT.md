# Context handoff: Music Vibe Recommendation Engine

Paste this entire document as your first message in the new chat.

## What this project is

A web app that recommends songs based on a user-defined "vibe" (e.g. "late-night drive"),
learned from audio characteristics of seed songs the user picks, not genre metadata. The
user creates a "vibe," adds 3-5 seed songs, and the app returns 5 recommended songs ranked
by similarity to the audio-feature centroid of those seeds. Core differentiator from a
plain "recommendation" app: the user gets visibility and control over *why* each song was
picked (per-song explanation text) and *how much each audio feature matters* (adjustable
weight sliders), not just a black-box list.

**Project folder**: `/Users/maxlane/Desktop/music recommendation engine`
**GitHub repo**: https://github.com/MaxLane1033/ML-Music-Recommendation-Engine (public)
**Run command**: `cd "/Users/maxlane/Desktop/music recommendation engine" && source venv/bin/activate && uvicorn app.main:app --reload`, then open http://127.0.0.1:8000
(A server may already be running in one of my terminals from earlier work -- check before starting a second one, `ps aux | grep uvicorn`.)

## Tech stack

- **Backend**: Python, FastAPI, SQLAlchemy (sync), SQLite (`vibes.db`, gitignored, created fresh via `Base.metadata.create_all` on startup -- there is no migration system, so schema changes require deleting `vibes.db` and restarting)
- **Frontend**: Plain HTML/CSS/vanilla JS, no framework, no build step. Served directly by FastAPI's `StaticFiles` mount. Deliberately "bare bones" styling per explicit user instruction -- functional layout only, no design polish expected or wanted.
- **External APIs**: ReccoBeats (audio features + recommendation candidates), Spotify's public oEmbed endpoint (cover art only, no auth), Apple's iTunes Search API (researched for genre, not yet wired in -- see below)
- **venv**: already set up at `venv/` in the project folder using Python 3.13 specifically (NOT the system default, which was 3.14 and breaks `pydantic-core`'s Rust build -- created via `/opt/homebrew/bin/python3.13 -m venv venv`)

## File structure and what each file does

```
app/
  main.py                 FastAPI app setup, mounts routers, mounts static/ at "/"
  database.py             SQLAlchemy engine/session (SQLite, vibes.db in project root)
  models.py                ORM models: Vibe, SeedSong, RecommendationRound, RecommendedSong,
                           AudioFeatureCache, ArtCache
  schemas.py               Pydantic request/response models
  reccobeats_client.py     Thin wrapper around ReccoBeats API (search, audio-features,
                           recommendation, artist search, artist tracks) -- see API details below
  spotify_art.py           Cover art via Spotify oEmbed, with DB-backed caching
  language_filter.py       English-only heuristic filter (see "Language filtering" section)
  recommender.py           ALL the recommendation math -- normalization, centroid, weighted
                           distance, scoring, explanation generation (see "The math" section)
  models_data/lid.176.ftz  fastText's pretrained language-id model file (~940KB, committed to
                           git, loaded once at startup by language_filter.py)
  routers/
    vibes.py               CRUD for vibes/seeds, weight updates, the /generate endpoint
                           (this is where the whole recommendation pipeline is orchestrated)
    search.py               GET /api/search -- song search (title, optional artist filter)
    artists.py              GET /api/artists/search, GET /api/artists/{id}/tracks (pure
                           artist browsing, no title needed)
    features.py              GET /api/features -- returns slider metadata (label/description/
                           default weight) for every scorable audio feature
static/
  index.html                Single page, ChatGPT-sidebar-style layout
  app.js                    All frontend logic, vanilla JS, no build step
  style.css                 Minimal functional CSS only
requirements.txt
.gitignore                 excludes venv/, vibes.db, __pycache__, .DS_Store
```

## Data model (SQLite via SQLAlchemy)

- **Vibe**: id, name, created_at, `feature_weights` (JSON column, nullable -- per-feature
  weight overrides, e.g. `{"energy": 2.0, "tempo": 0.0}`; missing keys default to 1.0)
- **SeedSong**: belongs to a Vibe. reccobeats_id, title, artist, spotify_url, thumbnail_url
- **RecommendationRound**: belongs to a Vibe. round_number, seed_count_at_time (how many
  seeds existed when this round was generated) -- rounds STACK, they never get replaced;
  the UI shows every round's history for a vibe like chat messages
- **RecommendedSong**: belongs to a Round. rank (1-5), match_score (0-100), explanation
  (human-readable string), plus title/artist/spotify_url/thumbnail_url
- **AudioFeatureCache**: keyed by reccobeats_id, caches ReccoBeats audio-features lookups
  forever (features don't change) so we never re-fetch the same track twice
- **ArtCache**: keyed by spotify_url, caches oEmbed thumbnail lookups

## External APIs -- exact details that took real investigation to nail down

### ReccoBeats (`https://api.reccobeats.com/v1`) -- no API key required, verified via direct testing

- `GET /track/search?searchText=&artist=&size=` -- title search (searchText REQUIRED, min 3
  chars), optional artist filter. Used for the main seed-song search box.
- `GET /track/{reccobeats_id}/audio-features` -- returns acousticness, danceability, energy,
  instrumentalness, key (int -1 to 11, -1 = undetected), liveness, loudness, mode (0/1),
  speechiness, tempo (BPM), valence. This is the entire feature vector we do math on.
- `GET /track/recommendation?seeds=&negativeSeeds=&size=&featureWeight=&<any audio feature>=`
  -- ReccoBeats' own candidate-generation endpoint (like old Spotify /recommendations).
  `seeds` is capped at 5 IDs max. We use this to get a rough candidate POOL (currently
  size=80); all actual ranking/scoring is done ourselves afterward, not by this endpoint.
- `GET /artist/search?searchText=` -- artist name search (for "Browse by artist" flow)
- `GET /artist/{artist_id}/track?size=` -- ALL tracks by an artist. This endpoint is
  UNDOCUMENTED (found by probing, not in ReccoBeats' published docs) and doesn't support
  sorting server-side, so we pull size=50 and sort by popularity ourselves client-side
  (in `get_artist_tracks`). Worth knowing it could be less stable than documented endpoints.
- No genre field exists ANYWHERE in ReccoBeats' data. Confirmed by checking every endpoint's
  response shape directly via curl.

### Spotify oEmbed (`https://open.spotify.com/oembed?url=<spotify_track_url>`)

No auth needed, public endpoint. Given a Spotify track URL (which ReccoBeats tracks always
include as `href`), returns `thumbnail_url` (cover art) among other fields. This is how
cover art works throughout the app without ever touching Spotify's real Web API.

### Spotify Developer API -- ABANDONED for now

The user tried setting this up (needed for real genre data since Spotify puts genre on
artists) and ran into problems. We are NOT pursuing this path currently. Do not suggest
resuming it unless the user brings it up.

### iTunes Search API (`https://itunes.apple.com/search?term=&entity=song&limit=`) -- researched, NOT implemented

Found as the alternative to Spotify for genre data. No API key, no account, plain HTTP GET.
Returns `primaryGenreName` (Apple's own clean taxonomy: Pop, R&B/Soul, Latin, K-Pop,
Classical, etc.) -- verified working via direct curl tests against real tracks including
ones from this project. The catch: unauthenticated rate limit is roughly 20 requests/minute
per IP (undocumented but well-reported), which rules out checking all ~80 candidates during
ranking -- it would need to be a POST-filter applied only to the final top-5 (same pattern
already used for cover art fetching), not baked into the scoring math. This is the next
planned feature but the UI/UX design (checkbox list? include vs exclude?) hasn't been
decided yet.

## The recommendation math (in `recommender.py`), in detail

1. **Normalization**: every audio feature is min-max normalized to [0,1] using ReccoBeats'
   documented bounds (tempo 0-250 BPM, loudness -60 to 2 dB, everything else already 0-1).
   Necessary because raw features live on wildly different scales.
2. **Key handling**: `key` (pitch class 0-11, circular) is NOT averaged naively -- that
   would be mathematically wrong (key 11 and key 0 are one semitone apart, not eleven
   apart). Uses proper circular mean (convert to unit-circle angle, average sin/cos
   components, convert back) and circular distance (`min(|Δ|, 12-|Δ|)/6`).
3. **Centroid**: the "vibe" is the mean of ALL the vibe's seed songs' normalized feature
   vectors (every seed ever added, not just the most recent 5 -- ReccoBeats' own `seeds`
   param is capped at 5 so only the 5 most recent are sent there, but our own scoring math
   uses every seed equally).
4. **Candidate pool**: ReccoBeats' `/track/recommendation` gives ~80 candidates near the
   centroid's denormalized target values. This is a rough first pass, not the real ranking.
5. **Dedup**: candidates already used as a seed, or already recommended in ANY previous
   round for this vibe, are filtered out before scoring (so "generate 5 more" never repeats).
6. **English-only filter**: applied next, before fetching audio features (saves wasted API
   calls) -- see "Language filtering" section below.
7. **Scoring**: weighted Euclidean distance from each remaining candidate to the centroid,
   `distance = sqrt(Σ weight_f * (candidate_f - centroid_f)²)` over all 11 dimensions
   (9 linear features + mode + circular key distance). Weights default to 1.0 each but are
   fully configurable per-vibe (see "Feature weight sliders" below).
8. **Match score**: `max(0, 1 - distance/worst_case) * 100`, where worst_case is the
   theoretical maximum distance given the current weights.
9. **Explanation generation**: per recommended song, compute per-feature deltas from the
   centroid (skipping any feature the user weighted to 0), find the 1-2 closest-matching
   features for the "closely matches..." phrase, and the single most-weighted-and-furthest
   feature for the "...though it's more/less X than your seeds" contrast clause.
10. Top 5 by score are returned, stored as a new RecommendationRound, cover art resolved
    only for those final 5 (not the whole 80-candidate pool, to limit oEmbed calls).

## Feature weight sliders (implemented, working)

Every audio feature (acousticness, danceability, energy, instrumentalness, liveness,
loudness, speechiness, tempo, valence, key, mode -- 11 total) has a 0-3 range slider in the
UI ("Feature weights" toggle button in the Seed songs section), each with a plain-English
description pulled from `GET /api/features`. Sliders auto-save on release (the `change`
event, not `input`, so it doesn't spam the API while dragging) via
`PUT /api/vibes/{id}/weights`, which merges into the vibe's stored `feature_weights` JSON
column. A weight of 0 means "ignore this feature entirely" -- verified this actually removes
that feature from both scoring AND the explanation text (not just scoring).

## Language filtering (implemented, working, went through real debugging)

**Why this exists**: user reported non-English songs (Spanish, Italian, German, Portuguese)
being recommended despite wanting English-only. ReccoBeats has no language field, so this
is entirely a heuristic guess from the track TITLE (never the artist name -- important, see
below).

**What was tried and rejected**:
- `langid.classify(title + " " + artist)` -- failed. Artist/DJ names are frequently
  stylized in English regardless of actual song language (e.g. "DJ Trick Z", "The Virtual
  Band"), which dragged genuinely non-English titles across the line into "English."
  Concrete proof case: "Ma Quale Idea" (Italian) by "Berk & The Virtual Band, DJ Trick Z,
  Electro" classified as English combined, but correctly as Italian on the title alone.
- `langid.classify(title)` alone -- better, but still failed on real live-tested output:
  let through "Se Vale Llorar" (Spanish), "Halver Aach" (German), "Dragon Ball Gang"
  (Brazilian Portuguese funk), and others. langid.py (a 2011-era library) just isn't
  reliable enough on short, proper-noun-heavy song titles.

**What's actually implemented now** (`app/language_filter.py`):
1. Reject if title+artist contains non-Latin script (Cyrillic/CJK/Hangul/Arabic/Hebrew/
   Devanagari/Thai) -- cheap, ~zero false positives.
2. Run the title (title ONLY, never artist) through TWO independent classifiers: `langid`
   AND Facebook's fastText `lid.176` model (loaded once at module import from
   `app/models_data/lid.176.ftz`).
3. **Only accept as English if BOTH agree.** This was empirically tuned against real
   failures pulled from live app output, not just hand-picked examples -- requiring
   agreement fixed nearly everything, at the cost of occasionally rejecting a genuinely
   English but ambiguous title (e.g. "Sierra Leone", "Novacane"). That tradeoff is
   deliberate: the user's stated priority is zero non-English leakage, not maximum recall
   of English songs, and the candidate pool (80) has plenty of depth to absorb some
   over-rejection.

Verified via a 23-case test battery (20/23 correct) AND a live multi-round regeneration
test through the actual running app (went from ~7 non-English leaks per 15 recommendations
down to 0 leaks per 20 recommendations after the fix).

**Known remaining limitation, be upfront about this if it comes up again**: very short (1-2
word), ambiguous titles can still slip through either direction (e.g. "Halleluja" -- German
spelling but plausible-looking to both classifiers). This is a heuristic on title text only,
not a guarantee about actual lyrics language, since no lyrics data source is integrated.

**Dependencies added for this**: `langid==1.1.6`, `fasttext-wheel==0.9.2`, `numpy<2` (pinned
below 2.0 because fastText's `predict()` breaks on numpy 2.x's changed `np.array(copy=False)`
behavior -- this bit us once already, don't let numpy get upgraded past 2.0 in this venv).

## Frontend UX flow (implemented)

- Left sidebar lists vibes (ChatGPT-style), "+ New vibe" creates one via a name prompt.
- Main panel per vibe: seed chips (always visible, shows currently added seeds with cover
  art and a remove button), song search box with a "Filter by artist" toggle (narrows title
  search results by artist), a separate "Browse by artist" toggle (search artists directly,
  see their catalog sorted by popularity, click straight into seeds -- no title needed),
  a "Feature weights" toggle panel (11 sliders), then "Generate recommendations" /
  "Generate 5 more" button (label changes once a vibe has at least one round).
- Recommendation rounds STACK below (never replace) -- each round shows its 5 songs with
  cover art, match %, and explanation text, plus a link to open in Spotify.
- Minimum 3 seeds required before generating. No upper limit on seeds (can keep adding to
  "train" a vibe further across multiple rounds -- all seeds ever added count equally
  toward the centroid).

## Testing conventions used throughout this project

No formal test suite exists yet -- testing has been done by hand via curl against the live
server, plus a real browser (Claude's Browser pane tool) for UI verification. Standard
pattern used every time before restarting the server after a change:
```bash
pkill -f "uvicorn app.main:app" 2>/dev/null; sleep 1
rm -f "/Users/maxlane/Desktop/music recommendation engine/vibes.db"   # schema has no migrations
cd "/Users/maxlane/Desktop/music recommendation engine" && source venv/bin/activate && nohup uvicorn app.main:app --port 8000 > /tmp/vibe_server.log 2>&1 &
```
Then curl-based smoke tests (create vibe, add seeds via `/api/search`, POST `/generate`,
inspect JSON output), and `pkill` + delete `vibes.db` again afterward to leave a clean
state for the user. **IMPORTANT**: check `ps aux | grep uvicorn` before killing anything --
the user runs their OWN server in their own terminal sometimes (following the run command
I give them), and that has real data in `vibes.db` that must not be wiped without checking
first.

## Git / GitHub -- important history, do not repeat past mistakes

- The repo is pushed to https://github.com/MaxLane1033/ML-Music-Recommendation-Engine
  (public), with ONE commit so far: "5 song recs fully complete !!!"
- **Critical user requirement, stated explicitly and must always be honored**: commits to
  this repo must NEVER show any Claude/AI attribution -- no `Co-Authored-By: Claude` trailer,
  nothing. Commits must appear as authored solely by the user (Max Lane / MaxLane1033).
  This is a hard rule for this project, overriding the normal default behavior.
- There WAS a serious incident (now resolved) where `/Users/maxlane/.git` existed at the
  user's HOME directory root (not this project), left behind by an old mistaken `git init`
  + `git add -A` around Oct 2025, containing ~300,000 orphaned blob objects (duplicate
  copies of files across the whole home folder, including sensitive PDFs) but zero commits
  and zero remotes -- confirmed nothing ever left the machine. It has been deleted
  (`rm -rf /Users/maxlane/.git`) with the user's explicit permission. This project's own
  git repo is a separate, properly-scoped nested repo at
  `/Users/maxlane/Desktop/music recommendation engine/.git` and is unaffected. No action
  needed here, just be aware this history exists in case it comes up.
- When making new commits, follow the user's existing style (they chose the exact wording
  "5 song recs fully complete !!!" for the first one) rather than imposing a different
  convention unprompted.

## Things NOT yet built (in priority order the user has expressed interest in)

1. **Genre filtering** -- researched (iTunes Search API, see above), not implemented.
   Needs a design decision on UI (which genres, include/exclude) before building, and must
   be architected as a post-filter on the top-5 due to iTunes' rate limit.
2. Ranking the 5 outputs 1-5 (best to worst) as user feedback -- discussed conceptually
   (Rocchio-style centroid nudging) but not implemented at all.
3. Spotify Developer API integration -- deprioritized/abandoned per user request, do not
   suggest resuming unless they bring it up first.

## User's working style / preferences to respect

- Wants terse, direct communication -- avoid over-explaining or padding responses.
- Explicitly values seeing REAL verification (curl output, actual test results) over claims
  of "this should work" -- this project's history includes at least one case where a fix
  was shipped without adequate live testing and it turned out incomplete, which the user
  called out directly. Always test against the actual running app, not just unit-level
  logic, before declaring something fixed.
- Frontend styling is intentionally bare-bones by explicit request -- don't add visual
  polish unless asked.
- Cares about the underlying math/ML being real and explainable, not a black box -- this
  is explicitly a learning project for the user to understand recommendation math, so
  favor transparent, from-scratch implementations (like the circular key-distance math)
  over opaque library calls where reasonable.
- Wants to be asked before any git push, any destructive/irreversible action, or any new
  external service integration (e.g. confirm before setting up a new third-party API).
