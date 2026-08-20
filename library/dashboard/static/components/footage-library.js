/**
 * footage-library.js - Clip browser with thumbnails and metadata.
 *
 * Displays all clips from the catalog as visual cards with thumbnails,
 * duration, mood tags, and interest scores. Supports annotation tagging.
 */

let footageFilters = { search: '', mood: '', object: '' };
let currentClips = [];

async function renderFootageLibrary() {
    const container = document.getElementById('footage-content');
    container.innerHTML = '<div class="empty-state"><div class="empty-state-text">Loading clips...</div></div>';

    try {
        currentClips = await api('/clips');

        if (!currentClips.length) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#127909;</div>
                    <div class="empty-state-text">
                        No clips cataloged yet. Run preflight (catalog footage) to see clips here.
                    </div>
                </div>
            `;
            return;
        }

        const moods = new Set();
        const objects = new Set();
        for (const c of currentClips) {
            (c.mood_tags || []).forEach(m => moods.add(m));
            (c.detected_objects || []).forEach(o => objects.add(o));
        }

        const moodOpts = Array.from(moods).sort().map(m => `<option value="${escapeHtml(m)}" ${footageFilters.mood === m ? 'selected' : ''}>${escapeHtml(m)}</option>`).join('');
        const objOpts = Array.from(objects).sort().map(o => `<option value="${escapeHtml(o)}" ${footageFilters.object === o ? 'selected' : ''}>${escapeHtml(o)}</option>`).join('');

        let html = `
            <div style="margin-bottom: 16px; display: flex; gap: 8px;">
                <input type="text" id="footage-search" placeholder="Search clips (ID, tags, transcript)..." style="flex: 1; padding: 6px 12px; border-radius: 6px; background: var(--bg-tertiary); border: 1px solid var(--border-primary); color: var(--text-primary);" value="${escapeHtml(footageFilters.search)}" oninput="footageFilters.search = this.value; filterFootage()">
                <select id="footage-mood" class="btn" style="background: var(--bg-tertiary);" onchange="footageFilters.mood = this.value; filterFootage()">
                    <option value="">All Moods</option>
                    ${moodOpts}
                </select>
                <select id="footage-object" class="btn" style="background: var(--bg-tertiary);" onchange="footageFilters.object = this.value; filterFootage()">
                    <option value="">All Objects</option>
                    ${objOpts}
                </select>
            </div>
            <div id="footage-grid-content" class="footage-grid"></div>
        `;

        container.innerHTML = html;
        filterFootage();

    } catch (err) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-text">Error loading clips: ${escapeHtml(err.message)}</div>
            </div>
        `;
    }
}

function filterFootage() {
    const container = document.getElementById('footage-grid-content');
    if (!container) return;

    const s = footageFilters.search.toLowerCase();
    const m = footageFilters.mood;
    const o = footageFilters.object;

    const filtered = currentClips.filter(c => {
        if (m && !(c.mood_tags || []).includes(m)) return false;
        if (o && !(c.detected_objects || []).includes(o)) return false;
        if (s) {
            const text = `${c.clip_id} ${(c.mood_tags || []).join(' ')} ${(c.detected_objects || []).join(' ')} ${c.transcript_excerpt || ''}`.toLowerCase();
            if (!text.includes(s)) return false;
        }
        return true;
    });

    const sorted = [...filtered].sort((a, b) => (b.interest_score || 0) - (a.interest_score || 0));
    
    if (!sorted.length) {
        container.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: var(--text-muted); padding: 40px;">No clips match the filters.</div>';
        return;
    }

    container.innerHTML = sorted.map(renderClipCard).join('');
}

function renderClipCard(clip) {
    const thumbnail = clip.thumbnail_url
        ? `<img class="clip-thumbnail" src="${escapeHtml(clip.thumbnail_url)}" alt="${escapeHtml(clip.clip_id)}" loading="lazy">`
        : `<div class="clip-thumbnail-placeholder">&#127909;</div>`;

    const duration = clip.duration_s
        ? `${clip.duration_s.toFixed(1)}s`
        : '';

    const tags = (clip.mood_tags || []).map(t =>
        `<span class="clip-tag">${escapeHtml(t)}</span>`
    ).join('');

    const objects = (clip.detected_objects || []).slice(0, 5).map(o =>
        `<span class="clip-tag" style="background: rgba(188,140,255,0.1); color: var(--accent-purple);">${escapeHtml(o)}</span>`
    ).join('');

    const score = clip.interest_score
        ? `<div class="clip-score">Interest: ${clip.interest_score.toFixed(2)}</div>`
        : '';

    const excerpt = clip.transcript_excerpt
        ? `<div class="clip-excerpt">${escapeHtml(clip.transcript_excerpt)}</div>`
        : '';

    return `
        <div class="clip-card" onclick="showClipDetail('${escapeHtml(clip.clip_id)}')">
            ${thumbnail}
            <div class="clip-info">
                <div class="clip-id">${escapeHtml(clip.clip_id)}</div>
                <div class="clip-duration">${duration}${clip.resolution ? ` - ${clip.resolution}` : ''}${clip.fps ? ` @ ${clip.fps}fps` : ''}</div>
                <div class="clip-tags">${tags}${objects}</div>
                ${excerpt}
                ${score}
            </div>
        </div>
    `;
}

function showClipDetail(clipId) {
    // Open inspector with clip details
    api('/clips').then(clips => {
        const clip = clips.find(c => c.clip_id === clipId);
        if (!clip) return;

        let content = '';

        // Thumbnail
        if (clip.thumbnail_url) {
            content += `<img src="${escapeHtml(clip.thumbnail_url)}" style="width: 100%; border-radius: 6px; margin-bottom: 12px;">`;
        }

        // Metadata
        content += `<div class="inspector-section">`;
        content += `<div class="inspector-section-title">Metadata</div>`;
        content += field('Clip ID', clip.clip_id);
        content += field('Duration', `${clip.duration_s?.toFixed(1) || '?'}s`);
        content += field('Resolution', clip.resolution);
        content += field('FPS', clip.fps);
        content += field('Interest Score', clip.interest_score?.toFixed(2));
        content += `</div>`;

        // Mood
        if (clip.mood_tags?.length) {
            content += `<div class="inspector-section">`;
            content += `<div class="inspector-section-title">Mood / Energy</div>`;
            content += `<div class="clip-tags">${clip.mood_tags.map(t =>
                `<span class="clip-tag">${escapeHtml(t)}</span>`
            ).join('')}</div>`;
            content += `</div>`;
        }

        // Objects
        if (clip.detected_objects?.length) {
            content += `<div class="inspector-section">`;
            content += `<div class="inspector-section-title">Detected Objects</div>`;
            content += `<div class="clip-tags">${clip.detected_objects.map(o =>
                `<span class="clip-tag">${escapeHtml(o)}</span>`
            ).join('')}</div>`;
            content += `</div>`;
        }

        // Transcript
        if (clip.transcript_excerpt) {
            content += `<div class="inspector-section">`;
            content += `<div class="inspector-section-title">Transcript Excerpt</div>`;
            content += `<div class="inspector-field-value">${escapeHtml(clip.transcript_excerpt)}</div>`;
            content += `</div>`;
        }

        openInspector(clipId, content);
    });
}

function field(label, value) {
    if (!value && value !== 0) return '';
    return `
        <div class="inspector-field">
            <div class="inspector-field-label">${escapeHtml(label)}</div>
            <div class="inspector-field-value">${escapeHtml(String(value))}</div>
        </div>
    `;
}
