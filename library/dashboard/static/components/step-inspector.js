/**
 * step-inspector.js - Detailed step output viewer.
 *
 * Shows the step's markdown summary and raw JSON output with
 * tabs for switching between views. Also shows gate controls
 * for steps awaiting review.
 */

async function renderStepDetail(stepId) {
    const container = document.getElementById('step-detail-content');
    container.innerHTML = '<div class="empty-state"><div class="empty-state-text">Loading...</div></div>';

    try {
        const detail = await api(`/steps/${stepId}`);
        const annotations = await api(`/steps/${stepId}/annotations`).catch(() => []);
        const gateInfo = await api(`/gates/${stepId}`).catch(() => null);

        let html = '';

        // Header
        html += `<div class="step-detail-header">`;
        html += `<div>`;
        html += `<h2>${escapeHtml(detail.name)}</h2>`;
        html += `<div class="text-sm muted">${escapeHtml(detail.id)}`;
        if (detail.elapsed_s) html += ` - ${detail.elapsed_s.toFixed(1)}s`;
        if (detail.completed_at) html += ` - ${escapeHtml(detail.completed_at)}`;
        html += `</div>`;
        html += `</div>`;

        // Gate controls
        if (detail.status === 'gate_pending' || (gateInfo && gateInfo.status === 'pending')) {
            html += renderGateControls(stepId, 'pending');
        } else if (gateInfo && gateInfo.status !== 'none') {
            html += renderGateControls(stepId, gateInfo.status);
        }

        html += `</div>`;

        // Tabs
        html += `<div class="step-detail-tabs">`;
        html += `<button class="step-detail-tab active" onclick="showStepTab(this, 'summary-${stepId}')">Summary</button>`;
        html += `<button class="step-detail-tab" onclick="showStepTab(this, 'json-${stepId}')">Raw JSON</button>`;
        if (annotations.length) {
            html += `<button class="step-detail-tab" onclick="showStepTab(this, 'annotations-${stepId}')">Annotations (${annotations.length})</button>`;
        }
        if (gateInfo && gateInfo.feedback) {
            html += `<button class="step-detail-tab" onclick="showStepTab(this, 'feedback-${stepId}')">Feedback</button>`;
        }
        html += `</div>`;

        // Summary tab
        html += `<div id="summary-${stepId}" class="step-detail-body">`;
        if (detail.summary_md) {
            html += `<div class="inspector-md">${markdownToHtml(detail.summary_md)}</div>`;
        } else {
            html += `<div class="muted">No summary available for this step.</div>`;
        }
        html += `</div>`;

        // JSON tab (hidden by default)
        html += `<div id="json-${stepId}" class="step-detail-body hidden">`;
        html += `<div id="json-viewer-${stepId}"></div>`;
        html += `</div>`;

        // Annotations tab
        if (annotations.length) {
            html += `<div id="annotations-${stepId}" class="step-detail-body hidden">`;
            for (let i = 0; i < annotations.length; i++) {
                const ann = annotations[i];
                html += `<div class="inspector-field" style="position: relative; padding-right: 24px;">`;
                html += `<div class="inspector-field-label">${escapeHtml(ann.annotation_type)} - ${escapeHtml(ann.created_at || '')}</div>`;
                html += `<div class="inspector-field-value">${escapeHtml(ann.content)}</div>`;
                if (ann.tag) html += `<div class="clip-tag mt-2" style="display: inline-block;">${escapeHtml(ann.tag)}</div>`;
                if (ann.target_path) html += `<div class="clip-tag mt-2" style="display: inline-block; background: rgba(188,140,255,0.1); color: var(--accent-purple);">${escapeHtml(ann.target_path)}</div>`;
                html += `<button class="btn btn-ghost text-sm" style="position: absolute; top: 0; right: 0; color: var(--color-failed); padding: 2px 6px;" title="Delete" onclick="deleteAnnotation('${stepId}', ${i})">&#10005;</button>`;
                html += `</div>`;
            }
            html += `</div>`;
        }

        // Feedback tab
        if (gateInfo && gateInfo.feedback) {
            html += `<div id="feedback-${stepId}" class="step-detail-body hidden">`;
            html += `<div class="inspector-section">`;
            html += `<div class="inspector-section-title">Action</div>`;
            html += `<div class="inspector-field-value">${escapeHtml(gateInfo.feedback.action)}</div>`;
            html += `</div>`;
            if (gateInfo.feedback.feedback) {
                html += `<div class="inspector-section">`;
                html += `<div class="inspector-section-title">Feedback</div>`;
                html += `<div class="inspector-field-value">${escapeHtml(gateInfo.feedback.feedback)}</div>`;
                html += `</div>`;
            }
            if (gateInfo.feedback.revisions && Object.keys(gateInfo.feedback.revisions).length) {
                html += `<div class="inspector-section">`;
                html += `<div class="inspector-section-title">Revisions</div>`;
                html += `<div id="revisions-viewer-${stepId}"></div>`;
                html += `</div>`;
            }
            html += `</div>`;
        }

        // Add annotation form
        html += `
            <div class="mt-4">
                <div class="inspector-section-title">Add Annotation</div>
                <div style="display: flex; flex-direction: column; gap: 8px;">
                    <div style="display: flex; gap: 8px;">
                        <select id="new-annotation-type-${stepId}" class="btn" style="background: var(--bg-tertiary);">
                            <option value="comment">Comment</option>
                            <option value="highlight">Highlight</option>
                            <option value="cut">Cut</option>
                            <option value="must_include">Must Include</option>
                        </select>
                        <input type="text" id="new-annotation-tag-${stepId}" placeholder="Tag (optional)" class="btn" style="flex: 1; background: var(--bg-tertiary); cursor: text;">
                        <input type="text" id="new-annotation-target-${stepId}" placeholder="Target Path (optional)" class="btn" style="flex: 1; background: var(--bg-tertiary); cursor: text;">
                    </div>
                    <div style="display: flex; gap: 8px; align-items: flex-end;">
                        <div style="flex: 1;">
                            <textarea id="new-annotation-${stepId}" placeholder="Add a note, comment, or instruction for the LLM..."
                                style="width:100%; min-height:60px; background: var(--bg-tertiary); border: 1px solid var(--border-primary);
                                border-radius: 6px; padding: 8px; font-family: var(--font-sans); font-size: 13px;
                                color: var(--text-primary); resize: vertical;"></textarea>
                        </div>
                        <button class="btn btn-primary" onclick="submitAnnotation('${stepId}')">Add</button>
                    </div>
                </div>
            </div>
        `;

        container.innerHTML = html;

        // Setup JSON viewer
        const jsonContainer = document.getElementById(`json-viewer-${stepId}`);
        if (jsonContainer) {
            const structured = renderStructuredOutput(detail.output);
            if (structured) {
                jsonContainer.appendChild(structured);
                const toggleBtn = document.createElement('button');
                toggleBtn.className = 'btn btn-ghost text-sm mt-4 mb-2';
                toggleBtn.textContent = 'Show Raw JSON';
                jsonContainer.appendChild(toggleBtn);
                
                const rawContainer = document.createElement('div');
                rawContainer.style.display = 'none';
                rawContainer.appendChild(createJsonViewer(detail.output));
                jsonContainer.appendChild(rawContainer);
                
                toggleBtn.onclick = () => {
                    const isHidden = rawContainer.style.display === 'none';
                    rawContainer.style.display = isHidden ? 'block' : 'none';
                    toggleBtn.textContent = isHidden ? 'Hide Raw JSON' : 'Show Raw JSON';
                };
            } else {
                jsonContainer.appendChild(createJsonViewer(detail.output));
            }
        }

        const revContainer = document.getElementById(`revisions-viewer-${stepId}`);
        if (revContainer && gateInfo && gateInfo.feedback && gateInfo.feedback.revisions) {
            revContainer.appendChild(createJsonViewer(gateInfo.feedback.revisions));
        }
    } catch (err) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-text">Error loading step: ${escapeHtml(err.message)}</div>
            </div>
        `;
    }
}

function showStepTab(tabEl, targetId) {
    // Deactivate all tabs in this group
    const tabs = tabEl.parentElement;
    tabs.querySelectorAll('.step-detail-tab').forEach(t => t.classList.remove('active'));
    tabEl.classList.add('active');

    // Hide all tab bodies (siblings of the tab bar)
    let sibling = tabs.nextElementSibling;
    while (sibling) {
        if (sibling.classList.contains('step-detail-body')) {
            sibling.classList.add('hidden');
        }
        sibling = sibling.nextElementSibling;
    }

    // Show target
    const target = document.getElementById(targetId);
    if (target) target.classList.remove('hidden');
}

async function submitAnnotation(stepId) {
    const textarea = document.getElementById(`new-annotation-${stepId}`);
    const typeSelect = document.getElementById(`new-annotation-type-${stepId}`);
    const tagInput = document.getElementById(`new-annotation-tag-${stepId}`);
    const targetInput = document.getElementById(`new-annotation-target-${stepId}`);

    const content = textarea.value.trim();
    if (!content) return;

    try {
        await apiPost(`/steps/${stepId}/annotations`, {
            annotations: [{
                annotation_type: typeSelect ? typeSelect.value : 'comment',
                content: content,
                tag: tagInput ? tagInput.value.trim() : '',
                target_path: targetInput ? targetInput.value.trim() : ''
            }],
        });
        await renderStepDetail(stepId);
    } catch (err) {
        alert(`Error saving annotation: ${err.message}`);
    }
}

async function deleteAnnotation(stepId, annIdx) {
    if (!confirm('Delete this annotation?')) return;
    try {
        await api(`/steps/${stepId}/annotations/${annIdx}`, { method: 'DELETE' });
        await renderStepDetail(stepId);
    } catch (err) {
        alert(`Error deleting annotation: ${err.message}`);
    }
}

function renderStructuredOutput(output) {
    if (!output) return null;
    
    // 1. Clip catalog
    if (output.clip_catalog) {
        let clips = output.clip_catalog;
        if (!Array.isArray(clips)) clips = Object.values(clips);
        
        let html = '<div style="display:flex; flex-direction:column; gap:16px;">';
        clips.forEach(clip => {
            const filename = escapeHtml(clip.filename || clip.clip_id || '');
            const dur = parseFloat(clip.duration || clip.duration_s || 0).toFixed(2);
            const res = escapeHtml(clip.resolution || '');
            const fps = escapeHtml(clip.fps || '');
            const thumb = `/thumbnails/${encodeURIComponent(clip.clip_id || filename)}`;
            
            html += `
            <div style="display:flex; gap:16px; background:var(--bg-tertiary); padding:12px; border-radius:8px;">
                <img src="${thumb}" onerror="this.style.display='none'" style="width:120px; height:auto; object-fit:contain; background:#000; border-radius:4px;">
                <div>
                    <div style="font-weight:bold; margin-bottom:8px;">${filename}</div>
                    <table style="font-size:12px; color:var(--text-secondary);">
                        <tr><td style="padding-right:16px;">Duration:</td><td>${dur}s</td></tr>
                        <tr><td style="padding-right:16px;">Resolution:</td><td>${res}</td></tr>
                        <tr><td style="padding-right:16px;">FPS:</td><td>${fps}</td></tr>
                    </table>
                </div>
            </div>`;
        });
        html += '</div>';
        const div = document.createElement('div');
        div.innerHTML = html;
        return div;
    }
    
    // 2. Speech sequence
    if (output.speech_sequence || output.sequence) {
        let seq = output.speech_sequence || output.sequence;
        if (!Array.isArray(seq)) seq = Object.values(seq);
        
        let html = '<div style="display:flex; flex-direction:column; gap:8px;">';
        seq.forEach((item) => {
            const start = parseFloat(item.start || item.timeline_start || 0).toFixed(2);
            const end = parseFloat(item.end || item.timeline_end || 0).toFixed(2);
            const text = escapeHtml(item.text || '');
            html += `
            <div style="background:var(--bg-tertiary); padding:12px; border-radius:8px; border-left: 4px solid var(--accent-cyan);">
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:4px;">${start}s - ${end}s</div>
                <div>${text}</div>
            </div>`;
        });
        html += '</div>';
        const div = document.createElement('div');
        div.innerHTML = html;
        return div;
    }
    
    // 3. Timeline / A-roll / B-roll
    if (output.a_roll_assignments || output.b_roll_assignments || output.timeline) {
        let items = output.a_roll_assignments || output.b_roll_assignments || output.timeline;
        if (!Array.isArray(items)) items = Object.values(items);
        
        let html = '<table style="width:100%; border-collapse:collapse; text-align:left; font-size:13px;">';
        html += `<tr style="border-bottom:1px solid var(--border-primary); color:var(--text-muted);">
            <th style="padding:8px;">Clip</th>
            <th style="padding:8px;">Start</th>
            <th style="padding:8px;">End</th>
            <th style="padding:8px;">Text/Notes</th>
        </tr>`;
        
        items.forEach(item => {
            const clip = escapeHtml(item.clip_id || item.filename || '');
            const start = parseFloat(item.timeline_start || item.start || 0).toFixed(2);
            const end = parseFloat(item.timeline_end || item.end || 0).toFixed(2);
            const text = escapeHtml(item.text || item.notes || '');
            
            html += `<tr style="border-bottom:1px solid var(--border-primary);">
                <td style="padding:8px; color:var(--accent-cyan);">${clip}</td>
                <td style="padding:8px;">${start}s</td>
                <td style="padding:8px;">${end}s</td>
                <td style="padding:8px;">${text}</td>
            </tr>`;
        });
        html += '</table>';
        const div = document.createElement('div');
        div.innerHTML = html;
        return div;
    }
    
    return null;
}

