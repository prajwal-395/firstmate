/**
 * transcript-view.js - Descript-style transcript review interface.
 *
 * Displays the combined transcript across all clips as selectable,
 * annotatable text regions. Users can highlight (must include),
 * strikethrough (cut), and comment on regions.
 */

async function renderTranscriptView() {
    const container = document.getElementById('transcript-content');
    container.innerHTML = '<div class="empty-state"><div class="empty-state-text">Loading transcript...</div></div>';

    try {
        const transcript = await api('/transcript');
        const annotations = await api('/steps/temporal_index/annotations').catch(() => []);

        if (!transcript.regions || !transcript.regions.length) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#128196;</div>
                    <div class="empty-state-text">
                        No transcript available yet. Run preflight (temporal index) to generate transcripts.
                    </div>
                </div>
            `;
            return;
        }

        // Build annotation lookup by clip_id + start
        const annMap = {};
        for (const ann of annotations) {
            const key = ann.target_path || '';
            if (key) {
                const lastUnderscore = key.lastIndexOf('_');
                if (lastUnderscore > 0) {
                    const clipId = key.substring(0, lastUnderscore);
                    const start = parseFloat(key.substring(lastUnderscore + 1)).toFixed(3);
                    const normKey = `${clipId}_${start}`;
                    if (!annMap[normKey]) annMap[normKey] = [];
                    annMap[normKey].push(ann);
                }
            }
        }

        let html = '';

        // Header
        html += `<div class="transcript-header">`;
        html += `<h2>Transcript</h2>`;
        html += `<div class="transcript-stats">`;
        html += `${transcript.clip_count} clips - `;
        html += `${transcript.regions.length} regions - `;
        html += `${transcript.total_duration_s.toFixed(1)}s total`;
        html += `</div>`;
        html += `</div>`;

        // Color palette for clips
        const clipColors = {};
        const palette = [
            '#58a6ff', '#bc8cff', '#39d2c0', '#d29922',
            '#f0883e', '#3fb950', '#f85149', '#a5d6ff',
        ];
        let colorIdx = 0;

        // Render regions
        for (let i = 0; i < transcript.regions.length; i++) {
            const region = transcript.regions[i];
            const normStart = parseFloat(region.start).toFixed(3);
            const regionKey = `${region.clip_id}_${normStart}`;

            // Assign clip color
            if (!clipColors[region.clip_id]) {
                clipColors[region.clip_id] = palette[colorIdx % palette.length];
                colorIdx++;
            }
            const color = clipColors[region.clip_id];

            // Check for annotations
            const anns = annMap[regionKey] || [];
            const isHighlighted = anns.some(a => a.tag === 'must_include' || a.annotation_type === 'highlight');
            const isStruck = anns.some(a => a.tag === 'cut' || a.annotation_type === 'strikethrough');

            const classes = ['transcript-region'];
            if (isHighlighted) classes.push('highlighted');
            if (isStruck) classes.push('struck');

            html += `<div class="${classes.join(' ')}" style="border-left-color: ${color}" data-region-idx="${i}">`;

            // Clip label and timecode
            html += `<div class="transcript-clip-label">`;
            html += `<span style="color: ${color}">${escapeHtml(region.clip_id)}</span>`;
            html += `<span class="transcript-timecode">${formatTimecode(region.start)} - ${formatTimecode(region.end)}</span>`;
            html += `</div>`;

            // Text
            html += `<div class="transcript-text">${escapeHtml(region.text)}</div>`;

            // Hover actions
            html += `<div class="transcript-actions">`;
            html += `<button class="transcript-action-btn highlight" title="Must include"
                        onclick="event.stopPropagation(); annotateRegion(${i}, 'highlight', 'must_include')">&#10003;</button>`;
            html += `<button class="transcript-action-btn strike" title="Cut this"
                        onclick="event.stopPropagation(); annotateRegion(${i}, 'strikethrough', 'cut')">&#10007;</button>`;
            html += `<button class="transcript-action-btn" title="Add comment"
                        onclick="event.stopPropagation(); showCommentInput(${i})">&#128172;</button>`;
            html += `</div>`;

            // Inline comment input
            html += `<div id="comment-input-${i}" style="display: none; margin-top: 8px;" onclick="event.stopPropagation();">
                        <input type="text" id="comment-text-${i}" placeholder="Add a comment..." style="width: 100%; padding: 4px; margin-bottom: 4px; background: var(--bg-tertiary); border: 1px solid var(--border-primary); color: var(--text-primary); border-radius: 4px;" onkeypress="if(event.key === 'Enter') submitRegionComment(${i})">
                        <div style="display: flex; gap: 4px;">
                            <button class="btn btn-primary text-sm" onclick="submitRegionComment(${i})">Save</button>
                            <button class="btn btn-ghost text-sm" onclick="document.getElementById('comment-input-${i}').style.display = 'none'">Cancel</button>
                        </div>
                     </div>`;

            // Show existing annotations if any
            if (anns.length > 0) {
                html += `<div style="margin-top: 8px; border-top: 1px solid var(--border-primary); padding-top: 4px;">`;
                for (const ann of anns) {
                    const tagHtml = ann.tag ? `<span class="clip-tag">${escapeHtml(ann.tag)}</span> ` : '';
                    html += `<div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 2px;">
                        <strong>${escapeHtml(ann.annotation_type)}</strong>: ${tagHtml}${escapeHtml(ann.content)}
                    </div>`;
                }
                html += `</div>`;
            }

            html += `</div>`;
        }

        container.innerHTML = html;

    } catch (err) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-text">Error loading transcript: ${escapeHtml(err.message)}</div>
            </div>
        `;
    }
}

async function annotateRegion(regionIdx, annotationType, tag) {
    try {
        const transcript = await api('/transcript');
        const region = transcript.regions[regionIdx];
        if (!region) return;

        await apiPost('/steps/temporal_index/annotations', {
            annotations: [{
                target_path: `${region.clip_id}_${region.start}`,
                annotation_type: annotationType,
                tag: tag,
                content: `${annotationType}: ${region.text.substring(0, 50)}...`,
            }],
        });

        // Re-render
        await renderTranscriptView();
    } catch (err) {
        console.error('Error annotating region:', err);
    }
}

function showCommentInput(regionIdx) {
    const div = document.getElementById(`comment-input-${regionIdx}`);
    if (div) {
        div.style.display = 'block';
        const input = document.getElementById(`comment-text-${regionIdx}`);
        if (input) input.focus();
    }
}

function submitRegionComment(regionIdx) {
    const input = document.getElementById(`comment-text-${regionIdx}`);
    if (!input) return;
    const comment = input.value.trim();
    if (!comment) return;

    api('/transcript').then(async transcript => {
        const region = transcript.regions[regionIdx];
        if (!region) return;

        await apiPost('/steps/temporal_index/annotations', {
            annotations: [{
                target_path: `${region.clip_id}_${region.start}`,
                annotation_type: 'comment',
                content: comment,
            }],
        });

        await renderTranscriptView();
    });
}

function formatTimecode(seconds) {
    if (!seconds && seconds !== 0) return '--:--';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    const ms = Math.floor((seconds % 1) * 100);
    return `${m}:${String(s).padStart(2, '0')}.${String(ms).padStart(2, '0')}`;
}
