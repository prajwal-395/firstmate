/**
 * gate-controls.js - Review gate approve/reject/revise controls.
 *
 * Provides utility functions for gate interactions used by
 * other components. The modal itself is in index.html and app.js.
 */

// Gate controls are primarily handled in app.js (openGateModal, submitGateAction).
// This file provides additional helpers.

/**
 * Render inline gate controls for a step card or detail view.
 */
function renderGateControls(stepId, gateStatus) {
    if (gateStatus === 'pending') {
        return `
            <div style="display: flex; gap: 6px; align-items: center;">
                <span class="step-badge review">Review</span>
                <button class="btn btn-approve text-sm" onclick="event.stopPropagation(); quickGateAction('${stepId}', 'approve')">
                    &#10003; Approve
                </button>
                <button class="btn btn-ghost text-sm" onclick="event.stopPropagation(); openGateModal('${stepId}')">
                    &#9998; Review
                </button>
            </div>
        `;
    }

    if (gateStatus === 'approved') {
        return `<span class="step-badge" style="background: rgba(63,185,80,0.15); color: var(--color-completed);">Approved</span>`;
    }

    if (gateStatus === 'rejected') {
        return `<span class="step-badge" style="background: rgba(248,81,73,0.15); color: var(--color-failed);">Rejected</span>`;
    }

    if (gateStatus === 'revised') {
        return `<span class="step-badge" style="background: rgba(210,153,34,0.15); color: var(--color-gate-pending);">Revised</span>`;
    }

    return '';
}

/**
 * Quick approve without opening the modal.
 */
async function quickGateAction(stepId, action) {
    try {
        await apiPost(`/gates/${stepId}/action`, {
            action: action,
            feedback: '',
            revisions: {},
        });
        await refreshData();
    } catch (err) {
        alert(`Error: ${err.message}`);
    }
}
