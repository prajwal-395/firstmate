/**
 * footage-library.js - Clip browser with thumbnails and metadata.
 *
 * Displays all clips from the catalog as visual cards with thumbnails,
 * duration, mood tags, and interest scores. Supports annotation tagging.
 */

async function renderFootageLibrary() {
    const container = document.getElementById('footage-content');
    container.innerHTML = '<div class="empty-state"><div class="empty-state-text">Loading clips...</div></div>';

    try {
        const clips = await api('/clips');

        if (!clips.length) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">&#127909;</div>
                    <div class="empty-state-text">
                        No clips cataloged yet. Run Phase 1 (catalog footage) to see clips here.
                    </div>
                </div>
            `;
            return;
        }

        let html = '';

        // Sort by interest score (highest first)
        const sorted = [...clips].sort((a, b) => (b.interest_score || 0) - (a.interest_score || 0));

        for (const clip of sorted) {
            html += renderClipCard(clip);
        }

        container.innerHTML = html;

    } catch (err) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-text">Error loading clips: ${escapeHtml(err.message)}</div>
            </div>
        `;
    }
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
