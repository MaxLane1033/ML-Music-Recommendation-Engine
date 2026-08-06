const state = {
  vibes: [],
  currentVibe: null, // full detail object from GET /api/vibes/:id
  featureMeta: null, // [{key, label, description, default_weight}, ...], fetched once and cached
};

const el = {
  vibeList: document.getElementById("vibe-list"),
  newVibeBtn: document.getElementById("new-vibe-btn"),
  emptyState: document.getElementById("empty-state"),
  newVibeForm: document.getElementById("new-vibe-form"),
  newVibeName: document.getElementById("new-vibe-name"),
  createVibeBtn: document.getElementById("create-vibe-btn"),
  vibeView: document.getElementById("vibe-view"),
  vibeTitle: document.getElementById("vibe-title"),
  songSearch: document.getElementById("song-search"),
  artistFilterToggle: document.getElementById("artist-filter-toggle"),
  artistFilterInput: document.getElementById("artist-filter-input"),
  searchResults: document.getElementById("search-results"),
  artistBrowseToggle: document.getElementById("artist-browse-toggle"),
  artistBrowsePanel: document.getElementById("artist-browse-panel"),
  artistBrowseSearch: document.getElementById("artist-browse-search"),
  artistBrowseResults: document.getElementById("artist-browse-results"),
  artistTrackList: document.getElementById("artist-track-list"),
  weightsToggle: document.getElementById("weights-toggle"),
  weightsPanel: document.getElementById("weights-panel"),
  weightsList: document.getElementById("weights-list"),
  seedChips: document.getElementById("seed-chips"),
  generateBtn: document.getElementById("generate-btn"),
  generateError: document.getElementById("generate-error"),
  rounds: document.getElementById("rounds"),
};

const MIN_SEEDS = 3;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showPanel(panel) {
  [el.emptyState, el.newVibeForm, el.vibeView].forEach((p) => p.classList.add("hidden"));
  panel.classList.remove("hidden");
}

async function loadVibes() {
  state.vibes = await api("/api/vibes");
  renderSidebar();
}

function renderSidebar() {
  el.vibeList.innerHTML = "";
  for (const vibe of state.vibes) {
    const row = document.createElement("div");
    row.className = "vibe-row";

    const btn = document.createElement("button");
    btn.className = "vibe-item" + (state.currentVibe && state.currentVibe.id === vibe.id ? " active" : "");
    btn.textContent = vibe.name;
    btn.addEventListener("click", () => selectVibe(vibe.id));
    row.appendChild(btn);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "vibe-delete";
    deleteBtn.title = "Delete vibe";
    deleteBtn.textContent = "\u{1F5D1}️";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteVibe(vibe);
    });
    row.appendChild(deleteBtn);

    el.vibeList.appendChild(row);
  }
}

async function deleteVibe(vibe) {
  if (!confirm(`Delete "${vibe.name}"? This removes its seeds and all recommendation rounds. This can't be undone.`)) {
    return;
  }
  await api(`/api/vibes/${vibe.id}`, { method: "DELETE" });
  const wasCurrent = state.currentVibe && state.currentVibe.id === vibe.id;
  if (wasCurrent) state.currentVibe = null;
  await loadVibes();
  if (wasCurrent) {
    showPanel(el.emptyState);
  }
}

async function selectVibe(id) {
  state.currentVibe = await api(`/api/vibes/${id}`);
  renderSidebar();
  renderVibeView();
}

function renderVibeView() {
  showPanel(el.vibeView);
  el.vibeTitle.textContent = state.currentVibe.name;
  el.songSearch.value = "";
  el.artistFilterInput.value = "";
  el.artistFilterInput.classList.add("hidden");
  el.searchResults.innerHTML = "";
  el.artistBrowsePanel.classList.add("hidden");
  el.artistBrowseSearch.value = "";
  el.artistBrowseResults.innerHTML = "";
  el.artistTrackList.innerHTML = "";
  el.weightsPanel.classList.add("hidden");
  el.generateError.classList.add("hidden");
  renderSeedChips();
  renderRounds();
  renderWeights();
}

function renderSeedChips() {
  el.seedChips.innerHTML = "";
  for (const seed of state.currentVibe.seeds) {
    const chip = document.createElement("div");
    chip.className = "seed-chip";

    if (seed.thumbnail_url) {
      const img = document.createElement("img");
      img.className = "thumb";
      img.src = seed.thumbnail_url;
      chip.appendChild(img);
    }

    const label = document.createElement("span");
    label.textContent = `${seed.title} — ${seed.artist}`;
    chip.appendChild(label);

    const removeBtn = document.createElement("button");
    removeBtn.textContent = "x";
    removeBtn.addEventListener("click", () => removeSeed(seed.id));
    chip.appendChild(removeBtn);

    el.seedChips.appendChild(chip);
  }

  const seedCount = state.currentVibe.seeds.length;
  const hasRounds = state.currentVibe.rounds.length > 0;
  el.generateBtn.disabled = seedCount < MIN_SEEDS;
  el.generateBtn.textContent = hasRounds ? "Generate 5 more" : "Generate recommendations";
}

// roundId -> { active: bool, order: [songId,...], confirmation?: string }
// Ephemeral UI-only state for the "Rank Recs" flow -- not part of the persisted vibe.
const rankingState = {};

function renderRounds() {
  el.rounds.innerHTML = "";
  for (const round of state.currentVibe.rounds) {
    el.rounds.appendChild(buildRoundCard(round));
  }
}

function buildRoundCard(round) {
  const roundDiv = document.createElement("div");
  roundDiv.className = "round";

  const headerRow = document.createElement("div");
  headerRow.className = "round-header";

  const heading = document.createElement("h3");
  heading.textContent = `Round ${round.round_number} (from ${round.seed_count_at_time} seed songs)`;
  headerRow.appendChild(heading);

  const entry = rankingState[round.id];
  const rankBtn = document.createElement("button");
  rankBtn.type = "button";
  rankBtn.textContent = entry && entry.active ? "Cancel ranking" : "Rank Recs";
  rankBtn.addEventListener("click", () => toggleRanking(round));
  headerRow.appendChild(rankBtn);

  roundDiv.appendChild(headerRow);

  if (entry && entry.confirmation) {
    const msg = document.createElement("p");
    msg.className = "rank-confirmation";
    msg.textContent = entry.confirmation;
    roundDiv.appendChild(msg);
  }

  if (entry && entry.active) {
    roundDiv.appendChild(buildRankingList(round));
  } else {
    for (const song of round.songs) {
      roundDiv.appendChild(buildSongCard(song));
    }
  }

  return roundDiv;
}

function toggleRanking(round) {
  const current = rankingState[round.id];
  if (current && current.active) {
    delete rankingState[round.id];
  } else {
    const order = [...round.songs]
      .sort((a, b) => (a.user_rank ?? a.rank) - (b.user_rank ?? b.rank))
      .map((s) => s.id);
    rankingState[round.id] = { active: true, order };
  }
  renderRounds();
}

function buildRankingList(round) {
  const wrap = document.createElement("div");
  wrap.className = "ranking-list";

  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "Drag to reorder, or use the arrows — #1 is your best match, #5 your worst.";
  wrap.appendChild(hint);

  const entry = rankingState[round.id];
  const songsById = Object.fromEntries(round.songs.map((s) => [s.id, s]));

  const rows = document.createElement("div");
  rows.className = "ranking-rows";

  let dragFromIndex = null;

  entry.order.forEach((songId, index) => {
    const song = songsById[songId];
    const row = document.createElement("div");
    row.className = "ranking-row";
    row.draggable = true;

    row.addEventListener("dragstart", () => {
      dragFromIndex = index;
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => row.classList.remove("dragging"));
    row.addEventListener("dragover", (e) => e.preventDefault());
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      if (dragFromIndex === null || dragFromIndex === index) return;
      moveRankingItem(round, dragFromIndex, index);
    });

    const posBadge = document.createElement("span");
    posBadge.className = "rank-position";
    posBadge.textContent = `#${index + 1}`;
    row.appendChild(posBadge);

    if (song.thumbnail_url) {
      const img = document.createElement("img");
      img.className = "thumb";
      img.src = song.thumbnail_url;
      row.appendChild(img);
    }

    const label = document.createElement("span");
    label.className = "ranking-label";
    label.textContent = `${song.title} — ${song.artist}`;
    row.appendChild(label);

    const arrows = document.createElement("span");
    arrows.className = "ranking-arrows";

    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.textContent = "↑";
    upBtn.disabled = index === 0;
    upBtn.addEventListener("click", () => moveRankingItem(round, index, index - 1));
    arrows.appendChild(upBtn);

    const downBtn = document.createElement("button");
    downBtn.type = "button";
    downBtn.textContent = "↓";
    downBtn.disabled = index === entry.order.length - 1;
    downBtn.addEventListener("click", () => moveRankingItem(round, index, index + 1));
    arrows.appendChild(downBtn);

    row.appendChild(arrows);
    rows.appendChild(row);
  });

  wrap.appendChild(rows);

  const submitBtn = document.createElement("button");
  submitBtn.type = "button";
  submitBtn.textContent = "Submit";
  submitBtn.addEventListener("click", () => submitRanking(round));
  wrap.appendChild(submitBtn);

  return wrap;
}

function moveRankingItem(round, fromIndex, toIndex) {
  const entry = rankingState[round.id];
  const [moved] = entry.order.splice(fromIndex, 1);
  entry.order.splice(toIndex, 0, moved);
  renderRounds();
}

async function submitRanking(round) {
  const entry = rankingState[round.id];
  try {
    await api(`/api/vibes/${state.currentVibe.id}/rounds/${round.id}/rank`, {
      method: "PUT",
      body: JSON.stringify({ song_ids: entry.order }),
    });
  } catch (err) {
    alert(err.message);
    return;
  }
  state.currentVibe = await api(`/api/vibes/${state.currentVibe.id}`);
  rankingState[round.id] = { active: false, confirmation: "Your preferences have been updated." };
  renderRounds();
  setTimeout(() => {
    if (rankingState[round.id]) {
      delete rankingState[round.id].confirmation;
      renderRounds();
    }
  }, 3000);
}

el.weightsToggle.addEventListener("click", () => {
  el.weightsPanel.classList.toggle("hidden");
});

async function renderWeights() {
  if (!state.featureMeta) {
    state.featureMeta = await api("/api/features");
  }

  el.weightsList.innerHTML = "";
  const weights = state.currentVibe.feature_weights || {};

  for (const feature of state.featureMeta) {
    const row = document.createElement("div");
    row.className = "weight-row";

    const labelRow = document.createElement("div");
    labelRow.className = "weight-label-row";

    const label = document.createElement("label");
    label.textContent = feature.label;
    labelRow.appendChild(label);

    const valueDisplay = document.createElement("span");
    valueDisplay.className = "weight-value";
    const currentValue = weights[feature.key] ?? feature.default_weight;
    valueDisplay.textContent = currentValue.toFixed(1);
    labelRow.appendChild(valueDisplay);

    row.appendChild(labelRow);

    const description = document.createElement("p");
    description.className = "hint weight-description";
    description.textContent = feature.description;
    row.appendChild(description);

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = "3";
    slider.step = "0.1";
    slider.value = currentValue;
    slider.addEventListener("input", () => {
      valueDisplay.textContent = parseFloat(slider.value).toFixed(1);
    });
    slider.addEventListener("change", () => {
      saveWeight(feature.key, parseFloat(slider.value));
    });
    row.appendChild(slider);

    el.weightsList.appendChild(row);
  }
}

async function saveWeight(key, value) {
  state.currentVibe = await api(`/api/vibes/${state.currentVibe.id}/weights`, {
    method: "PUT",
    body: JSON.stringify({ weights: { [key]: value } }),
  });
}

function buildSongCard(song) {
  const card = document.createElement("div");
  card.className = "song-card";

  if (song.thumbnail_url) {
    const img = document.createElement("img");
    img.className = "thumb";
    img.src = song.thumbnail_url;
    card.appendChild(img);
  }

  const meta = document.createElement("div");
  meta.className = "song-meta";

  const title = document.createElement("div");
  title.innerHTML = `<strong>${escapeHtml(song.title)}</strong> — ${escapeHtml(song.artist)}`;
  meta.appendChild(title);

  const score = document.createElement("div");
  score.className = "score";
  score.textContent = `Match: ${song.match_score}%`;
  if (song.user_rank) {
    score.textContent += ` — Your rank: #${song.user_rank}`;
  }
  meta.appendChild(score);

  const explanation = document.createElement("div");
  explanation.className = "explanation";
  explanation.textContent = song.explanation;
  meta.appendChild(explanation);

  if (song.spotify_url) {
    const link = document.createElement("a");
    link.href = song.spotify_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Open in Spotify";
    meta.appendChild(document.createElement("br"));
    meta.appendChild(link);
  }

  card.appendChild(meta);
  return card;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

let searchTimeout = null;
function scheduleSearch() {
  clearTimeout(searchTimeout);
  const query = el.songSearch.value.trim();
  // ReccoBeats requires at least 3 characters in the title search.
  if (query.length < 3) {
    el.searchResults.innerHTML = "";
    return;
  }
  searchTimeout = setTimeout(() => runSearch(query, el.artistFilterInput.value.trim()), 300);
}

el.songSearch.addEventListener("input", scheduleSearch);
el.artistFilterInput.addEventListener("input", scheduleSearch);

el.artistFilterToggle.addEventListener("click", () => {
  const isHidden = el.artistFilterInput.classList.contains("hidden");
  if (isHidden) {
    el.artistFilterInput.classList.remove("hidden");
    el.artistFilterInput.focus();
  } else {
    el.artistFilterInput.classList.add("hidden");
    el.artistFilterInput.value = "";
    scheduleSearch();
  }
});

async function runSearch(query, artist) {
  let results;
  try {
    const params = new URLSearchParams({ q: query });
    if (artist) params.set("artist", artist);
    results = await api(`/api/search?${params.toString()}`);
  } catch (err) {
    el.searchResults.innerHTML = "";
    return;
  }
  renderTrackRows(el.searchResults, results, addSeed);
}

// Shared renderer for any list of {title, artist, thumbnail_url, ...} tracks that should add a seed on click.
function renderTrackRows(container, tracks, onClick) {
  container.innerHTML = "";
  for (const track of tracks) {
    const row = document.createElement("div");
    row.className = "search-result-row";

    if (track.thumbnail_url) {
      const img = document.createElement("img");
      img.className = "thumb";
      img.src = track.thumbnail_url;
      row.appendChild(img);
    }

    const label = document.createElement("span");
    label.textContent = `${track.title} — ${track.artist}`;
    row.appendChild(label);

    row.addEventListener("click", () => onClick(track));
    container.appendChild(row);
  }
}

el.artistBrowseToggle.addEventListener("click", () => {
  const isHidden = el.artistBrowsePanel.classList.contains("hidden");
  if (isHidden) {
    el.artistBrowsePanel.classList.remove("hidden");
    el.artistBrowseSearch.focus();
  } else {
    el.artistBrowsePanel.classList.add("hidden");
    el.artistBrowseSearch.value = "";
    el.artistBrowseResults.innerHTML = "";
    el.artistTrackList.innerHTML = "";
  }
});

let artistSearchTimeout = null;
el.artistBrowseSearch.addEventListener("input", () => {
  clearTimeout(artistSearchTimeout);
  const query = el.artistBrowseSearch.value.trim();
  el.artistTrackList.innerHTML = "";
  if (query.length < 2) {
    el.artistBrowseResults.innerHTML = "";
    return;
  }
  artistSearchTimeout = setTimeout(() => runArtistSearch(query), 300);
});

async function runArtistSearch(query) {
  let artists;
  try {
    artists = await api(`/api/artists/search?q=${encodeURIComponent(query)}`);
  } catch (err) {
    el.artistBrowseResults.innerHTML = "";
    return;
  }
  el.artistBrowseResults.innerHTML = "";
  for (const artist of artists) {
    const row = document.createElement("div");
    row.className = "search-result-row";
    row.textContent = artist.name;
    row.addEventListener("click", () => loadArtistTracks(artist));
    el.artistBrowseResults.appendChild(row);
  }
}

async function loadArtistTracks(artist) {
  el.artistTrackList.innerHTML = "<p class=\"hint\">Loading songs...</p>";
  let tracks;
  try {
    tracks = await api(`/api/artists/${encodeURIComponent(artist.artist_id)}/tracks`);
  } catch (err) {
    el.artistTrackList.innerHTML = "";
    return;
  }
  const heading = document.createElement("p");
  heading.className = "hint";
  heading.textContent = `Songs by ${artist.name} (most popular first):`;
  el.artistTrackList.innerHTML = "";
  el.artistTrackList.appendChild(heading);

  const list = document.createElement("div");
  el.artistTrackList.appendChild(list);
  renderTrackRows(list, tracks, addSeed);
}

async function addSeed(result) {
  try {
    await api(`/api/vibes/${state.currentVibe.id}/seeds`, {
      method: "POST",
      body: JSON.stringify(result),
    });
  } catch (err) {
    alert(err.message);
    return;
  }
  el.songSearch.value = "";
  el.searchResults.innerHTML = "";
  state.currentVibe = await api(`/api/vibes/${state.currentVibe.id}`);
  renderSeedChips();
}

async function removeSeed(seedId) {
  await api(`/api/vibes/${state.currentVibe.id}/seeds/${seedId}`, { method: "DELETE" });
  state.currentVibe = await api(`/api/vibes/${state.currentVibe.id}`);
  renderSeedChips();
}

el.generateBtn.addEventListener("click", async () => {
  el.generateError.classList.add("hidden");
  el.generateBtn.disabled = true;
  try {
    await api(`/api/vibes/${state.currentVibe.id}/generate`, { method: "POST" });
    state.currentVibe = await api(`/api/vibes/${state.currentVibe.id}`);
    renderSeedChips();
    renderRounds();
  } catch (err) {
    el.generateError.textContent = err.message;
    el.generateError.classList.remove("hidden");
  } finally {
    el.generateBtn.disabled = state.currentVibe.seeds.length < MIN_SEEDS;
  }
});

el.newVibeBtn.addEventListener("click", () => {
  el.newVibeName.value = "";
  showPanel(el.newVibeForm);
});

el.createVibeBtn.addEventListener("click", async () => {
  const name = el.newVibeName.value.trim();
  if (!name) return;
  const vibe = await api("/api/vibes", { method: "POST", body: JSON.stringify({ name }) });
  await loadVibes();
  await selectVibe(vibe.id);
});

el.newVibeName.addEventListener("keydown", (e) => {
  if (e.key === "Enter") el.createVibeBtn.click();
});

loadVibes();
