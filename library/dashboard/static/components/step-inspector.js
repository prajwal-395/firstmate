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
            html += `<button class="btn btn-primary" onclick="openGateModal('${stepId}')">&#9888; Review & Approve</button>`;
        } else if (gateInfo && gateInfo.status !== 'none') {
            html += `<span class="step-badge" style="background: rgba(63,185,80,0.15); color: var(--color-completed);">${escapeHtml(gateInfo.status)}</span>`;
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
        html += `<div class="json-viewer">${escapeHtml(JSON.stringify(detail.output, null, 2))}</div>`;
        html += `</div>`;

        // Annotations tab
        if (annotations.length) {
            html += `<div id="annotations-${stepId}" class="step-detail-body hidden">`;
            for (const ann of annotations) {
                html += `<div class="inspector-field">`;
                html += `<div class="inspector-field-label">${escapeHtml(ann.annotation_type)} - ${escapeHtml(ann.created_at || '')}</div>`;
                html += `<div class="inspector-field-value">${escapeHtml(ann.content)}</div>`;
                if (ann.tag) html += `<div class="clip-tag mt-2">${escapeHtml(ann.tag)}</div>`;
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
                html += `<div class="json-viewer">${escapeHtml(JSON.stringify(gateInfo.feedback.revisions, null, 2))}</div>`;
                html += `</div>`;
            }
            html += `</div>`;
        }

        // Add annotation form
        html += `
            <div class="mt-4">
                <div class="inspector-section-title">Add Annotation</div>
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
        `;

        container.innerHTML = html;
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
    const content = textarea.value.trim();
    if (!content) return;

    try {
        await apiPost(`/steps/${stepId}/annotations`, {
            annotations: [{
                annotation_type: 'comment',
                content: content,
            }],
        });
        textarea.value = '';
        // Re-render to show new annotation
        await renderStepDetail(stepId);
    } catch (err) {
        alert(`Error saving annotation: ${err.message}`);
    }
}
