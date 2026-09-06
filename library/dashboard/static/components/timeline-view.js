/**
 * timeline-view.js - Rough cut timeline visualization.
 *
 * Displays A-roll, B-roll and caption placements as horizontal blocks
 * on a timeline with a time ruler.
 *
 * The track names are the built timeline's own, so what is drawn here
 * and what step 6.01 places in Resolve are called the same thing.
 * V3 is one block per caption CARD, which is the unit a reviewer is
 * looking at when they type a note about the captions.
 */

/**
 * Drop the label of every block too narrow to hold it.
 *
 * Ellipsised to a character or two a label reads as damage rather than
 * as a name: 001's 30 caption cards across 56.6s rendered as
 * "i ju to 2! de th ev on", and V1 showed "clip_0" beside "clip_011".
 * A row of clean bars is easier to read than a row of stubs, and the
 * text is still on the tooltip and in the inspector.
 *
 * The fit is MEASURED rather than guessed from a width percentage: only
 * the browser knows how wide the text it just laid out actually is, and
 * a percentage that suits one label length is wrong for the next.
 */
function dropLabelsThatDoNotFit(container) {
    for (const el of container.querySelectorAll('.timeline-block')) {
        if (el.scrollWidth > el.clientWidth) el.textContent = '';
    }
}

async function renderTimelineView() {
    const container = document.getElementById('timeline-content');
    container.innerHTML = '<div class="empty-state"><div class="empty-state-text">Loading timeline...</div></div>';

    try {
        const timeline = await api('/timeline');

        if (!timeline.blocks || !timeline.blocks.length) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#9776;</div>
                    <div class="empty-state-text">
                        No timeline data yet. Run through Phase 3 (assembly) to see the rough cut here.
                    </div>
                </div>
            `;
            return;
        }

        const totalDur = timeline.total_duration_s || 1;
        let html = '';

        // Header
        html += `<div class="timeline-header">`;
        html += `<h2>Rough Cut Timeline</h2>`;
        // "blocks", not "clips": a caption card is not a clip, and V3
        // is most of the count on a finished cut.
        const trackNames = [...new Set(timeline.blocks.map(b => b.track))];
        html += `<div class="text-sm muted">${timeline.blocks.length} blocks`
              + ` across ${trackNames.length} track${trackNames.length === 1 ? '' : 's'}`
              + ` - ${totalDur.toFixed(1)}s total</div>`;
        html += `</div>`;

        // Time ruler
        html += renderRuler(totalDur);

        // Separate blocks by track
        const tracks = {};
        for (const block of timeline.blocks) {
            const track = block.track || 'V1';
            if (!tracks[track]) tracks[track] = [];
            tracks[track].push(block);
        }

        // Everything from here is inside one positioned wrapper, so the
        // selection band can span every track the way an NLE's does
        // rather than living inside one of them.
        html += `<div id="timeline-tracks">`;

        // Render tracks in the built timeline's own order.
        const trackOrder = ['V1', 'V2', 'V3', 'A1', 'A2'];
        for (const trackName of trackOrder) {
            const blocks = tracks[trackName];
            if (!blocks) continue;

            html += `<div class="timeline-track">`;
            html += `<div class="timeline-track-label">${trackName}</div>`;
            html += `<div class="timeline-track-blocks">`;

            for (const block of blocks) {
                const left = (block.start_s / totalDur * 100).toFixed(2);
                const width = ((block.duration_s || (block.end_s - block.start_s)) / totalDur * 100).toFixed(2);
                const blockClass = block.block_type || 'a_roll';
                const label = block.clip_name || block.clip_id || '';
                // A caption's label IS its text, so appending the text
                // again would tooltip "i can feel the silent - i can
                // feel the silent".
                const textPreview = (block.text && block.text !== label)
                    ? ` - ${block.text.substring(0, 30)}` : '';

                html += `<div class="timeline-block ${blockClass}"
                            style="left: ${left}%; width: ${Math.max(0.5, parseFloat(width))}%;"
                            title="${escapeHtml(label)}${escapeHtml(textPreview)} (${block.start_s?.toFixed(1)}s - ${block.end_s?.toFixed(1)}s)"
                            onclick="showBlockDetail('${escapeHtml(block.id)}')">`;
                html += escapeHtml(label);
                html += `</div>`;
            }

            html += `</div>`;
            html += `</div>`;
        }

        html += `</div>`;                       // #timeline-tracks
        html += `<div id="timeline-region-panel"></div>`;

        container.innerHTML = html;
        // Measured after layout, never guessed before it.
        dropLabelsThatDoNotFit(container);
        armRegionSelection(totalDur);

    } catch (err) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-text">Error loading timeline: ${escapeHtml(err.message)}</div>
            </div>
        `;
    }
}

function renderRuler(totalDur) {
    const interval = totalDur < 30 ? 5 : totalDur < 120 ? 10 : 30;
    let html = '<div class="timeline-ruler">';

    for (let t = 0; t <= totalDur; t += interval) {
        const left = (t / totalDur * 100).toFixed(2);
        html += `<div class="timeline-ruler-mark" style="left: ${left}%">${formatTimecode(t)}</div>`;
    }

    html += '</div>';
    return html;
}

function showBlockDetail(blockId) {
    // Fetch timeline and find the block
    api('/timeline').then(timeline => {
        const block = timeline.blocks.find(b => b.id === blockId);
        if (!block) return;

        let content = '';

        if (block.thumbnail_url) {
            content += `<img src="${escapeHtml(block.thumbnail_url)}" style="width: 100%; border-radius: 6px; margin-bottom: 12px;">`;
        }

        content += `<div class="inspector-section">`;
        content += `<div class="inspector-section-title">Block Info</div>`;
        content += `<div class="inspector-field"><div class="inspector-field-label">ID</div><div class="inspector-field-value">${escapeHtml(block.id)}</div></div>`;
        content += `<div class="inspector-field"><div class="inspector-field-label">Track</div><div class="inspector-field-value">${escapeHtml(block.track)}</div></div>`;
        content += `<div class="inspector-field"><div class="inspector-field-label">Type</div><div class="inspector-field-value">${escapeHtml(block.block_type)}</div></div>`;
        // A caption is not cut from a clip, so it has no clip to name -
        // and a "Clip" row repeating the caption text reads as though
        // the words were a filename.
        const isCaption = block.block_type === 'subtitle';
        if (!isCaption) {
            content += `<div class="inspector-field"><div class="inspector-field-label">Clip</div><div class="inspector-field-value">${escapeHtml(block.clip_name || block.clip_id)}</div></div>`;
        }
        content += `<div class="inspector-field"><div class="inspector-field-label">Time</div><div class="inspector-field-value">${block.start_s?.toFixed(2)}s - ${block.end_s?.toFixed(2)}s (${block.duration_s?.toFixed(2)}s)</div></div>`;
        content += `</div>`;

        if (block.text) {
            content += `<div class="inspector-section">`;
            content += `<div class="inspector-section-title">${isCaption ? 'Caption' : 'Speech'}</div>`;
            content += `<div class="inspector-field-value">${escapeHtml(block.text)}</div>`;
            content += `</div>`;
        }

        openInspector(`${block.track}: ${block.clip_name || block.id}`, content);
    });
}

// ── Selecting a region, and what can act on it ──────────────────────
//
// The captain drags a span in the timeline they already have and is told
// what can act on it - AND, which is the half that makes this a control
// surface rather than a menu, what CANNOT and why. A picker that
// silently hides what it cannot do teaches nothing.
//
// This extends the existing view rather than opening a new page
// (AGENTS.md section 4).

/** The selected span, in timeline seconds, or null. */
let selectedRegion = null;

/** Seconds under a page x, measured off the track the browser laid out.
 *
 *  The inverse of the `left` computation each block already uses. Taken
 *  from the element's own rect rather than a stored width, because the
 *  panel and the sidebar both resize it. */
function secondsAt(clientX, track, totalDur) {
    const rect = track.getBoundingClientRect();
    const ratio = (clientX - rect.left) / rect.width;
    return Math.max(0, Math.min(totalDur, ratio * totalDur));
}

function armRegionSelection(totalDur) {
    const wrapper = document.getElementById('timeline-tracks');
    if (!wrapper) return;
    const firstTrack = wrapper.querySelector('.timeline-track-blocks');
    if (!firstTrack) return;

    let anchorSeconds = null;

    const band = document.createElement('div');
    band.className = 'timeline-region';
    band.id = 'timeline-region';          // stable: an anchor selector
    band.hidden = true;
    wrapper.appendChild(band);

    const paint = (from, to) => {
        const lo = Math.min(from, to), hi = Math.max(from, to);
        band.hidden = false;
        band.style.left = `${(lo / totalDur * 100).toFixed(3)}%`;
        band.style.width = `${Math.max(0.2, (hi - lo) / totalDur * 100).toFixed(3)}%`;
        band.setAttribute('data-region', `${lo.toFixed(3)}-${hi.toFixed(3)}`);
        return [lo, hi];
    };

    wrapper.addEventListener('mousedown', (event) => {
        const track = event.target.closest('.timeline-track-blocks');
        if (!track) return;
        // A click on a block still opens it; a DRAG selects. The
        // distinction is made on mouseup by how far the pointer moved,
        // so neither gesture has to be learned.
        anchorSeconds = secondsAt(event.clientX, track, totalDur);
        paint(anchorSeconds, anchorSeconds);
        event.preventDefault();
    });

    wrapper.addEventListener('mousemove', (event) => {
        if (anchorSeconds === null) return;
        paint(anchorSeconds, secondsAt(event.clientX, firstTrack, totalDur));
    });

    const finish = (event) => {
        if (anchorSeconds === null) return;
        const [lo, hi] = paint(
            anchorSeconds, secondsAt(event.clientX, firstTrack, totalDur));
        anchorSeconds = null;
        if (hi - lo < 0.05) {              // a click, not a drag
            band.hidden = true;
            selectedRegion = null;
            document.getElementById('timeline-region-panel').innerHTML = '';
            return;
        }
        selectedRegion = [lo, hi];
        loadRegionOperations(lo, hi);
    };
    wrapper.addEventListener('mouseup', finish);
    wrapper.addEventListener('mouseleave', finish);

    // A re-render rebuilds the tracks, so the band is a new element and
    // the selection would vanish while `selectedRegion` still held one -
    // the view and the state disagreeing about what is selected. Redrawn
    // from the state, which is what makes the id a usable anchor: the
    // element is found again after a re-render (the property
    // `review-channel.js:resolveAnchor` depends on).
    if (selectedRegion) {
        paint(selectedRegion[0], selectedRegion[1]);
        loadRegionOperations(selectedRegion[0], selectedRegion[1]);
    }
}

async function loadRegionOperations(start, end) {
    const panel = document.getElementById('timeline-region-panel');
    const span = `${start.toFixed(3)}-${end.toFixed(3)}`;
    panel.innerHTML = `<div class="text-sm muted">Resolving ${span}s...</div>`;
    let data;
    try {
        data = await api(`/operations?region=${encodeURIComponent(span)}`);
    } catch (err) {
        panel.innerHTML =
            `<div class="text-sm muted">Could not resolve ${escapeHtml(span)}s: `
            + `${escapeHtml(err.message)}</div>`;
        return;
    }

    let html = `<div class="region-panel-head">`;
    html += `<h3>${escapeHtml(data.region)} selected</h3>`;
    html += `<button class="btn btn-ghost" onclick="clearRegionSelection()">Clear</button>`;
    html += `</div>`;

    // The offers.
    html += `<div class="region-group"><div class="region-group-title">`
          + `Can act on this span (${data.offers.length})</div>`;
    if (!data.offers.length) {
        html += `<div class="region-empty">Nothing can act on this span right `
              + `now. The refusals below say why.</div>`;
    }
    for (const op of data.offers) {
        html += `<div class="region-op offer">`
              + `<div class="region-op-name">${escapeHtml(op.name)}`
              + `<span class="region-op-node">${escapeHtml(op.owning_node)}</span></div>`
              + `<div class="region-op-summary">${escapeHtml(op.summary)}</div>`
              + `</div>`;
    }
    html += `</div>`;

    // The refusals - the half that makes this a control surface.
    html += `<div class="region-group"><div class="region-group-title">`
          + `Cannot, and why (${data.refusals.length})</div>`;
    for (const op of data.refusals) {
        html += `<div class="region-op refusal">`
              + `<div class="region-op-name">${escapeHtml(op.name)}`
              + `<span class="region-op-node">${escapeHtml(op.owning_node)}</span></div>`;
        for (const why of op.reasons) {
            html += `<div class="region-reason">`
                  + `<span class="region-reason-name">${escapeHtml(why.requirement)}</span> `
                  + escapeHtml(why.why);
            if (why.produced_by && why.produced_by.length) {
                html += `<div class="region-reason-fix">produced by `
                      + `${escapeHtml(why.produced_by.join(', '))}</div>`;
            }
            html += `</div>`;
        }
        html += `</div>`;
    }
    html += `</div>`;

    // Said, not hidden: ten of the twelve operations are project-scoped,
    // and a captain who selected a span deserves to know that is why.
    if (data.out_of_scope.length) {
        html += `<div class="region-group"><div class="region-group-title">`
              + `Not addressable by a span (${data.out_of_scope.length})</div>`
              + `<div class="region-empty">`
              + escapeHtml(data.out_of_scope.map(o => o.name).join(', '))
              + ` run against the whole project, not a region.</div></div>`;
    }

    // The execute path does not exist yet, and the UI says so rather
    // than implying a button.
    if (!data.executable) {
        html += `<div class="region-note">${escapeHtml(data.executable_note)}</div>`;
    }
    panel.innerHTML = html;
}

function clearRegionSelection() {
    selectedRegion = null;
    const band = document.getElementById('timeline-region');
    if (band) band.hidden = true;
    const panel = document.getElementById('timeline-region-panel');
    if (panel) panel.innerHTML = '';
}
