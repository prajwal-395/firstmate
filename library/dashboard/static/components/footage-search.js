/**
 * footage-search.js - search the project's own footage, by hand.
 *
 * Its own view rather than a tab inside the Footage Library, because the
 * two answer different questions about different units. The Library is a
 * browser over CLIPS: seventeen cards, one per file, filtered on
 * clip-level tags it computes from the cards it already loaded. This is a
 * search over SPANS INSIDE clips: four hundred segments, each with its own
 * timecode, its own score and its own reason for being here, filtered on
 * index facets the server computes. Folding spans into the card grid would
 * make one filter bar mean two things.
 *
 * They are linked rather than merged: a hit names its clip, and the clip
 * opens the Library's own inspector.
 */

const footageSearch = {
    status: null,
    report: null,
    query: '',
    mode: 'hybrid',
    topK: 10,
    floor: null,          // null = the index's measured default
    filters: {},
    filtersOpen: false,
    showWeak: false,
    busy: false,
    warmTimer: null,
};

// The facet controls, and the one place their labels live. Each names a
// `filter()` keyword the index really accepts (§ footage_query.filter).
const FOOTAGE_FACET_SELECTS = [
    ['kind', 'Kind'],
    ['clip_id', 'Clip'],
    ['framing', 'Framing'],
    ['camera_mode', 'Camera'],
    ['stability', 'Stability'],
    ['movement', 'Movement'],
    ['scene_type', 'Scene'],
    ['content_type', 'Content'],
];

// key, label, placeholder (the box is narrow - keep it to a few
// characters), tooltip, min, max, step.
const FOOTAGE_FACET_NUMBERS = [
    ['min_face_presence', 'Face &ge;', '0..1',
     'mean face-detection rate over the span. ~0.5 requires the subject on screen.', 0, 1, 0.05],
    ['max_face_presence', 'Face &le;', '0..1',
     'mean face-detection rate over the span. A low value finds footage with nobody in frame.', 0, 1, 0.05],
    ['max_motion', 'Motion &le;', '0..1',
     'mean motion energy over the span. A low value finds steady footage.', 0, 1, 0.05],
    ['min_duration', 'Longer than', 'secs', 'segment length in seconds', 0, null, 0.5],
    ['max_duration', 'Shorter than', 'secs', 'segment length in seconds', 0, null, 0.5],
];

// ── Entry point ────────────────────────────────────────────────

async function renderFootageSearch() {
    const container = document.getElementById('footage-search-content');
    if (!footageSearch.status) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-text">Reading the index…</div></div>`;
    }
    try {
        footageSearch.status = await api('/footage/search/status');
    } catch (err) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-text">${escapeHtml(err.message)}</div></div>`;
        return;
    }
    paintFootageSearch();
    scheduleWarmPoll();
}

/** The embedder loads once per server. Say so while it happens. */
function scheduleWarmPoll() {
    const embedState = footageSearch.status?.embed?.state;
    if (embedState !== 'loading') {
        if (footageSearch.warmTimer) { clearTimeout(footageSearch.warmTimer); footageSearch.warmTimer = null; }
        return;
    }
    if (footageSearch.warmTimer) return;
    footageSearch.warmTimer = setTimeout(async () => {
        footageSearch.warmTimer = null;
        if (state.currentView !== 'footage-search') return;   // app.js's view state
        try {
            footageSearch.status = await api('/footage/search/status');
            paintFootageSearch();
            scheduleWarmPoll();
        } catch (err) { /* the banner keeps its last honest reading */ }
    }, 900);
}

function paintFootageSearch() {
    const container = document.getElementById('footage-search-content');
    const s = footageSearch.status;

    if (!s.exists) {
        container.innerHTML = renderIndexAbsent(s);
        return;
    }
    container.innerHTML = `
        ${renderIndexBanner(s)}
        ${renderQueryBar(s)}
        ${renderFacetBar(s)}
        <div id="footage-search-results">${renderSearchResults()}</div>
    `;
    const box = document.getElementById('footage-query-input');
    if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
}

// ── The index, before any query ────────────────────────────────

function renderIndexAbsent(s) {
    return `
        <div class="fs-banner fs-banner-absent">
            <div class="fs-banner-title">No footage index for this project yet</div>
            <div class="fs-banner-line">${escapeHtml(s.hint || '')}</div>
            <div class="fs-banner-line fs-path">writes to: <span class="mono">${escapeHtml(s.index_dir)}</span></div>
            <div class="fs-banner-actions">
                <button class="btn btn-primary" onclick="buildFootageIndex()">Build the index (3-4 s)</button>
            </div>
        </div>
        <div class="empty-state">
            <div class="empty-state-icon">&#128269;</div>
            <div class="empty-state-text">
                The index is cut from what steps 1.02-1.05 already measured: every
                spoken utterance, every vision observation, every object appearance.
                It reads that output and writes nowhere else.
            </div>
        </div>
    `;
}

function renderIndexBanner(s) {
    const sum = s.summary || {};
    const stale = s.staleness || {};
    const kinds = Object.entries(sum.kinds || {})
        .map(([k, n]) => `<span class="fs-kind-chip">${escapeHtml(k)} ${n}</span>`).join('');

    const embed = s.embed || {};
    const backendLabel = {
        cold: 'not loaded yet',
        loading: 'loading the embedding model - once per server, ~2 s',
        ready: escapeHtml(embed.backend || 'ready'),
        unavailable: 'no embedder - keyword match only',
    }[embed.state] || escapeHtml(embed.state || '');
    const degraded = embed.state === 'unavailable'
        || (embed.state === 'ready' && embed.backend !== 'sentence-transformers');

    let backendNote = '';
    if (embed.state === 'unavailable') {
        backendNote = `<div class="fs-warn">Neither <span class="mono">sentence-transformers</span> nor
            <span class="mono">transformers</span> loaded, so every answer below is exact-word BM25.
            A keyword floor cannot tell "not in this footage" from "phrased differently".</div>`;
    } else if (degraded && embed.state === 'ready') {
        backendNote = `<div class="fs-note"><span class="mono">sentence-transformers</span> is not installed here,
            so the same checkpoint is loaded through plain <span class="mono">transformers</span> with mean pooling.
            Same vectors, different code path.</div>`;
    }

    let staleRow = '';
    if (stale.stale === true) {
        staleRow = `<div class="fs-warn">Stale: ${escapeHtml(stale.reason || '')}. This index answers with
            the ingest it was built from.</div>`;
    } else if (stale.stale === null) {
        staleRow = `<div class="fs-note">${escapeHtml(stale.reason || '')}</div>`;
    }

    return `
        <div class="fs-banner">
            <div class="fs-banner-row">
                <div>
                    <div class="fs-banner-title">
                        ${sum.segment_count ?? 0} segments across ${sum.clips ?? 0} clips
                        ${sum.spoken_seconds ? ` · ${sum.spoken_seconds}s of speech` : ''}
                    </div>
                    <div class="fs-kind-chips">${kinds}</div>
                </div>
                <div class="fs-banner-actions">
                    <button class="btn btn-ghost" onclick="buildFootageIndex()"
                        title="Rebuild from the current ingest output">&#8635; Rebuild</button>
                </div>
            </div>
            <div class="fs-banner-line">
                embedding backend:
                <span class="fs-backend ${embed.state === 'unavailable' ? 'bad' : (degraded ? 'warn' : 'good')}">
                    ${backendLabel}</span>
                ${stale.stale === false ? '<span class="fs-backend good">index current</span>' : ''}
            </div>
            <div class="fs-banner-line fs-path">
                <span class="mono">${escapeHtml(s.index_dir)}</span>
                ${sum.built_at ? ` · built ${escapeHtml(agoLabel(sum.built_at))}` : ''}
            </div>
            ${backendNote}
            ${staleRow}
        </div>
    `;
}

function agoLabel(epochSeconds) {
    const secs = Math.max(0, Date.now() / 1000 - epochSeconds);
    if (secs < 90) return `${Math.round(secs)}s ago`;
    if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
    if (secs < 172800) return `${Math.round(secs / 3600)} h ago`;
    return `${Math.round(secs / 86400)} days ago`;
}

// ── The controls ───────────────────────────────────────────────

function renderQueryBar(s) {
    const floorDefault = s.floor?.default ?? 0.4;
    const floor = footageSearch.floor === null ? floorDefault : footageSearch.floor;
    const modeOpt = (v, label, title) =>
        `<option value="${v}" title="${escapeHtml(title)}" ${footageSearch.mode === v ? 'selected' : ''}>${escapeHtml(label)}</option>`;
    return `
        <div class="fs-querybar">
            <input type="text" id="footage-query-input" class="fs-query" spellcheck="false"
                placeholder="where does he talk about parking · wide shot of the lot · leave empty to filter only"
                value="${escapeHtml(footageSearch.query)}"
                oninput="footageSearch.query = this.value"
                onkeydown="if (event.key === 'Enter') runFootageSearch()">
            <button class="btn btn-primary" onclick="runFootageSearch()" ${footageSearch.busy ? 'disabled' : ''}>
                ${footageSearch.busy ? 'Searching…' : 'Search'}</button>
            <label class="fs-opt">Mode
                <select class="run-select" onchange="footageSearch.mode = this.value; runFootageSearch()">
                    ${modeOpt('hybrid', 'hybrid', 'meaning blended with keyword match')}
                    ${modeOpt('dense', 'meaning', 'embedding similarity only - tolerates paraphrase')}
                    ${modeOpt('lexical', 'words', 'BM25 only - exact words, no model')}
                </select>
            </label>
            <label class="fs-opt" title="Minimum embedding similarity for a segment to count as evidence. Measured default ${floorDefault}. 0 shows the ranking with no floor, the way it behaved before there was one.">
                Floor
                <input type="number" class="fs-num" min="0" max="1" step="0.02" value="${floor}"
                    onchange="footageSearch.floor = this.value === '' ? null : parseFloat(this.value); runFootageSearch()">
            </label>
            <label class="fs-opt">Results
                <input type="number" class="fs-num" min="1" max="100" step="1" value="${footageSearch.topK}"
                    onchange="footageSearch.topK = Math.max(1, parseInt(this.value) || 10); runFootageSearch()">
            </label>
        </div>
    `;
}

function renderFacetBar(s) {
    const facets = s.facets || {};
    const active = Object.keys(footageSearch.filters).filter(k => footageSearch.filters[k] !== '' && footageSearch.filters[k] != null).length;
    const open = footageSearch.filtersOpen || active > 0;

    const selects = FOOTAGE_FACET_SELECTS.filter(([key]) => (facets[key] || []).length).map(([key, label]) => {
        const opts = facets[key].map(v =>
            `<option value="${escapeHtml(v)}" ${footageSearch.filters[key] === v ? 'selected' : ''}>${escapeHtml(v)}</option>`).join('');
        return `<label class="fs-opt">${escapeHtml(label)}
            <select class="run-select" onchange="setFootageFilter('${key}', this.value)">
                <option value="">any</option>${opts}
            </select></label>`;
    }).join('');

    // `input` RECORDS and `change`/Enter SEARCHES. Recording on every
    // keystroke means a repaint can never lose a half-typed bound, and it
    // means the filter is real even when only one of the two events fires.
    const read = key => `this.value === '' ? '' : parseFloat(this.value)`;
    const numbers = FOOTAGE_FACET_NUMBERS.map(([key, label, hint, title, min, max, step]) => `
        <label class="fs-opt" title="${escapeHtml(title)}">${label}
            <input type="number" class="fs-num" step="${step}" min="${min}" ${max != null ? `max="${max}"` : ''}
                value="${footageSearch.filters[key] ?? ''}" placeholder="${escapeHtml(hint)}"
                oninput="recordFootageFilter('${key}', ${read(key)})"
                onchange="setFootageFilter('${key}', ${read(key)})"
                onkeydown="if (event.key === 'Enter') setFootageFilter('${key}', ${read(key)})">
        </label>`).join('');

    return `
        <div class="fs-facets">
            <button class="fs-facet-toggle" onclick="footageSearch.filtersOpen = !footageSearch.filtersOpen; paintFootageSearch()">
                ${open ? '&#9660;' : '&#9654;'} Filters${active ? ` <span class="fs-count">${active}</span>` : ''}
            </button>
            <div class="fs-facet-body ${open ? '' : 'hidden'}">
                <div class="fs-facet-row">${selects}</div>
                <div class="fs-facet-row">${numbers}
                    <button class="btn btn-ghost" onclick="clearFootageFilters()">Clear</button>
                </div>
                <div class="fs-facet-hint">
                    A filter with an empty query is a SELECTION, not a search - "steady wide footage
                    with nobody in frame" is <span class="mono">Framing wide</span> +
                    <span class="mono">Stability stable</span> + <span class="mono">Face &le; 0.1</span>,
                    with the search box left empty. An UNMEASURED facet fails its bound rather than
                    passing it: a clip whose face curve step 1.04 never wrote is not evidence that
                    nobody is on screen.
                </div>
            </div>
        </div>
    `;
}

function recordFootageFilter(key, value) {
    if (value === '' || value == null || Number.isNaN(value)) delete footageSearch.filters[key];
    else footageSearch.filters[key] = value;
}

function setFootageFilter(key, value) {
    recordFootageFilter(key, value);
    runFootageSearch();
}

function clearFootageFilters() {
    footageSearch.filters = {};
    runFootageSearch();
}

// ── Running it ─────────────────────────────────────────────────

async function buildFootageIndex() {
    const container = document.getElementById('footage-search-content');
    container.innerHTML = `<div class="empty-state"><div class="empty-state-text">
        Building the index - cutting the ingest into segments and embedding them. 3-4 s.
    </div></div>`;
    try {
        await apiPost('/footage/search/build', {});
    } catch (err) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-text">${escapeHtml(err.message)}</div></div>`;
        return;
    }
    footageSearch.report = null;
    await renderFootageSearch();
}

async function runFootageSearch() {
    footageSearch.showWeak = false;
    footageSearch.busy = true;
    paintFootageSearch();
    try {
        footageSearch.report = await apiPost('/footage/search', {
            query: footageSearch.query,
            top_k: footageSearch.topK,
            mode: footageSearch.mode,
            floor: footageSearch.floor,
            filters: footageSearch.filters,
        });
        // The backend state can change under a query: the first one may be
        // what finished warming the model.
        if (footageSearch.report.embed) footageSearch.status.embed = footageSearch.report.embed;
    } catch (err) {
        footageSearch.report = { error: err.message, results: [], weak: [] };
    } finally {
        footageSearch.busy = false;
        paintFootageSearch();
    }
}

function searchWithoutFloor() {
    footageSearch.floor = 0;
    runFootageSearch();
}

function toggleWeakBand() {
    footageSearch.showWeak = !footageSearch.showWeak;
    const el = document.getElementById('footage-search-results');
    if (el) el.innerHTML = renderSearchResults();
}

// ── Results ────────────────────────────────────────────────────

function renderSearchResults() {
    const r = footageSearch.report;
    if (!r) {
        return `<div class="empty-state">
            <div class="empty-state-icon">&#128269;</div>
            <div class="empty-state-text">
                Type a phrase and press Enter. Every hit comes back with the clip it is in
                and the timecode inside that clip.
            </div>
        </div>`;
    }
    if (r.error && !r.results?.length) {
        return `<div class="empty-state"><div class="empty-state-text">${escapeHtml(r.error)}</div></div>`;
    }

    const meta = [];
    if (r.mode) meta.push(escapeHtml(r.mode));
    if (r.considered != null) meta.push(`${r.considered} of ${r.segment_count} segments considered`);
    if (r.retained != null) {
        meta.push(r.mode === 'filter' ? `${r.retained} selected`
                                      : `${r.retained} above the floor`);
    }
    if (r.floor != null) meta.push(`floor ${Number(r.floor).toFixed(2)}`);
    if (r.elapsed_ms != null) meta.push(`${r.elapsed_ms} ms`);
    if (r.truncated) meta.push(`${r.truncated} more not shown`);

    const degraded = r.degraded
        ? `<div class="fs-warn">${escapeHtml(r.degraded)}</div>` : '';

    if (!r.results.length) return `
        <div class="fs-resultmeta">${meta.map(m => `<span>${m}</span>`).join('')}</div>
        ${degraded}
        ${renderFootageAbstain(r)}`;

    return `
        <div class="fs-resultmeta">${meta.map(m => `<span>${m}</span>`).join('')}</div>
        ${degraded}
        <div class="fs-hits">${r.results.map(renderFootageHit).join('')}</div>
    `;
}

/** "Nothing" is an answer. Say what was searched and what is actually here. */
function renderFootageAbstain(r) {
    const s = footageSearch.status || {};
    const corpus = s.corpus || {};
    const best = r.best_rejected;
    const asked = r.query
        ? `nothing in this footage matched <span class="fs-quoted">${escapeHtml(r.query)}</span>`
        : 'no segment passes these filters';

    let closest = '';
    if (best) {
        closest = `<div class="fs-closest">
            closest was <span class="fs-score">${Number(best.dense_score).toFixed(3)}</span>,
            under the ${Number(r.floor).toFixed(2)} floor:
            <span class="fs-quoted">${escapeHtml(best.text)}</span>
            <span class="mono">[${escapeHtml(best.kind)}] ${escapeHtml(best.clip_id)}</span>
        </div>`;
    }

    const buttons = [];
    if (r.weak?.length) {
        buttons.push(`<button class="btn btn-ghost" onclick="toggleWeakBand()">
            ${footageSearch.showWeak ? 'Hide' : 'Show'} the ${r.weak.length} weak match${r.weak.length === 1 ? '' : 'es'}
            (${Number(r.weak_floor ?? 0.28).toFixed(2)}-${Number(r.floor).toFixed(2)})</button>`);
    }
    if (r.floor > 0) {
        buttons.push(`<button class="btn btn-ghost" onclick="searchWithoutFloor()">Rank everything, no floor</button>`);
    }

    const contains = [];
    if (corpus.objects?.length) {
        contains.push(`<div class="fs-corpus-line"><span class="fs-corpus-label">objects the vision pass named</span>
            ${corpus.objects.map(o => `<span class="clip-tag">${escapeHtml(o)}</span>`).join('')}</div>`);
    }
    if (corpus.places?.length) {
        contains.push(`<div class="fs-corpus-line"><span class="fs-corpus-label">places</span>
            ${corpus.places.map(o => `<span class="clip-tag">${escapeHtml(o)}</span>`).join('')}</div>`);
    }

    return `
        <div class="fs-abstain">
            <div class="fs-abstain-title">${asked}</div>
            <div class="fs-abstain-body">${r.query
                ? `That is the answer, not a failure. ${r.considered ?? 0} segments were scored and
                   none cleared the confidence floor, so the index is saying the subject is not in
                   this footage rather than handing back its three least-bad guesses.`
                : `Every facet has to hold at once, and an UNMEASURED facet fails its bound rather
                   than passing it - a clip whose face curve step 1.04 never wrote is not evidence
                   that nobody is on screen. Loosen one and try again.`}
            </div>
            ${closest}
            <div class="fs-abstain-actions">${buttons.join('')}</div>
            ${contains.length ? `<div class="fs-corpus"><div class="fs-corpus-head">what this footage does contain</div>${contains.join('')}</div>` : ''}
        </div>
        ${footageSearch.showWeak && r.weak?.length
            ? `<div class="fs-weak-head">weak matches - shown because you asked, not because they are answers</div>
               <div class="fs-hits fs-hits-weak">${r.weak.map(renderFootageHit).join('')}</div>`
            : ''}
    `;
}

/**
 * One hit. The claim it makes about the footage, and the evidence for it.
 *
 * The two halves of a hybrid score routinely disagree - a paraphrase
 * scores high on meaning and zero on words, a proper noun the other way -
 * and a hit whose reason cannot be seen cannot be trusted, so both raw
 * numbers are on the card with the half that actually found it named.
 */
function renderFootageHit(hit, _index, siblings) {
    const f = hit.facets || {};
    const dense = hit.dense_score;
    const lex = hit.lexical_score;

    let why = '', whyClass = '';
    if (dense != null && lex != null) {
        const byMeaning = lex === 0;
        const byWords = dense < (footageSearch.status?.floor?.default ?? 0.4) && lex > 0;
        if (byMeaning) { why = 'found by meaning - no word of the query appears in this text'; whyClass = 'meaning'; }
        else if (byWords) { why = 'found by words - the query terms are here, the meaning is a weaker match'; whyClass = 'words'; }
        else { why = 'meaning and words agree'; whyClass = 'both'; }
    } else if (lex != null) {
        why = 'found by words - this index answered without an embedder'; whyClass = 'words';
    }

    // A cosine has a real ceiling of 1, so the meaning bar is absolute and
    // comparable between searches. BM25 has none, so the words bar is drawn
    // against the best score IN THIS RESULT SET - a relative reading, and
    // drawing it against its own value would make every bar full.
    const lexMax = Math.max(1e-9, ...(siblings || [hit]).map(h => h.lexical_score || 0));
    const bar = (label, value, max, cls) => {
        if (value == null) return '';
        const pct = Math.max(0, Math.min(100, (value / max) * 100));
        return `<div class="fs-bar-row">
            <span class="fs-bar-label">${label}</span>
            <span class="fs-bar"><span class="fs-bar-fill ${cls}" style="width:${pct}%"></span></span>
            <span class="fs-bar-value">${Number(value).toFixed(3)}</span>
        </div>`;
    };

    const tc = hit.source_tc_in
        ? `<span class="fs-tc-frames" title="source timecode from the clip's start, non-drop, at ${hit.fps} fps">${escapeHtml(hit.source_tc_in)} &rarr; ${escapeHtml(hit.source_tc_out)}</span>`
        : `<span class="fs-tc-frames fs-tc-missing" title="the catalog records no frame rate for this clip, so no frame-accurate timecode is offered">no fps in catalog</span>`;

    const words = (hit.word_hits || []).slice(0, 8).map(w =>
        `<span class="fs-word" title="the matching word's own source timing">${escapeHtml(w.word)}
            <span class="mono">${Number(w.start).toFixed(2)}-${Number(w.end).toFixed(2)}</span></span>`).join('');

    const shot = ['framing', 'camera_mode', 'stability', 'movement', 'scene_type', 'lighting']
        .filter(k => f[k]).map(k => `<span class="clip-tag">${escapeHtml(String(f[k]))}</span>`).join('');
    const measured = [
        f.face_presence != null ? `face ${Number(f.face_presence).toFixed(2)}` : '',
        f.motion != null ? `motion ${Number(f.motion).toFixed(2)}` : '',
        f.speech_ratio != null ? `speech ${Number(f.speech_ratio).toFixed(2)}` : '',
        f.brightness != null ? `bright ${Number(f.brightness).toFixed(2)}` : '',
    ].filter(Boolean).map(t => `<span class="fs-measured">${escapeHtml(t)}</span>`).join('');

    const copy = `${hit.filename || hit.clip_id}  ${hit.timecode}` +
        (hit.source_tc_in ? `  [${hit.source_tc_in} - ${hit.source_tc_out} @ ${hit.fps}fps]` : '');

    return `
        <div class="fs-hit">
            <div class="fs-hit-head">
                <span class="fs-kind fs-kind-${escapeHtml(hit.kind)}">${escapeHtml(hit.kind)}</span>
                <span class="fs-clip" onclick="showClipDetail('${escapeHtml(hit.clip_id)}')"
                    title="open this clip in the Footage Library inspector">${escapeHtml(hit.clip_id)}</span>
                <span class="fs-file">${escapeHtml(hit.filename || '')}</span>
                ${hit.score != null ? `<span class="fs-score">${Number(hit.score).toFixed(3)}</span>` : ''}
            </div>
            <div class="fs-tc">
                <span class="fs-tc-seconds">${escapeHtml(hit.timecode)}</span>
                ${tc}
                <span class="fs-dur">${Number(hit.duration).toFixed(2)}s</span>
                <button class="fs-copy" title="copy the clip and timecode"
                    data-copy="${escapeHtml(copy)}" onclick="copyFootageRef(this)">copy</button>
            </div>
            <div class="fs-hit-text">${escapeHtml(hit.text)}</div>
            ${words ? `<div class="fs-words">${words}</div>` : ''}
            ${shot || measured ? `<div class="fs-hit-facets">${shot}${measured}</div>` : ''}
            <div class="fs-why ${whyClass}">
                <div class="fs-why-label">${escapeHtml(why)}</div>
                ${bar('meaning', dense, 1, 'meaning')}
                ${bar('words', lex, lexMax, 'words')}
            </div>
            <div class="fs-hit-foot">
                <span class="mono fs-segid">${escapeHtml(hit.segment_id)}</span>
                <button class="fs-copy" onclick="openFootageSegment('${escapeHtml(hit.segment_id)}')">full record</button>
            </div>
        </div>
    `;
}

function copyFootageRef(button) {
    const text = button.getAttribute('data-copy') || '';
    navigator.clipboard.writeText(text).then(() => {
        const was = button.textContent;
        button.textContent = 'copied';
        setTimeout(() => { button.textContent = was; }, 1200);
    }).catch(() => { button.textContent = 'clipboard blocked'; });
}

async function openFootageSegment(segmentId) {
    try {
        const record = await api(`/footage/search/segment?segment_id=${encodeURIComponent(segmentId)}`);
        const body = document.getElementById('inspector-content');
        openInspector(segmentId, '');
        body.appendChild(createJsonViewer(record));
    } catch (err) {
        openInspector(segmentId, `<div class="inspector-field-value">${escapeHtml(err.message)}</div>`);
    }
}
