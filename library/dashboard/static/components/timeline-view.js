/**
 * timeline-view.js - Rough cut timeline visualization.
 *
 * Displays A-roll and B-roll placements as horizontal blocks
 * on a timeline with a time ruler.
 */

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
        html += `<div class="text-sm muted">${timeline.blocks.length} clips - ${totalDur.toFixed(1)}s total</div>`;
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

        // Render tracks in order: V1, V2, A1, A2
        const trackOrder = ['V1', 'V2', 'A1', 'A2'];
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
                const textPreview = block.text ? ` - ${block.text.substring(0, 30)}` : '';

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

        container.innerHTML = html;

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
        content += `<div class="inspector-field"><div class="inspector-field-label">Clip</div><div class="inspector-field-value">${escapeHtml(block.clip_name || block.clip_id)}</div></div>`;
        content += `<div class="inspector-field"><div class="inspector-field-label">Time</div><div class="inspector-field-value">${block.start_s?.toFixed(2)}s - ${block.end_s?.toFixed(2)}s (${block.duration_s?.toFixed(2)}s)</div></div>`;
        content += `</div>`;

        if (block.text) {
            content += `<div class="inspector-section">`;
            content += `<div class="inspector-section-title">Speech</div>`;
            content += `<div class="inspector-field-value">${escapeHtml(block.text)}</div>`;
            content += `</div>`;
        }

        openInspector(`${block.track}: ${block.clip_name || block.id}`, content);
    });
}
