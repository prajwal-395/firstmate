/**
 * pipeline-view.js - Pipeline overview showing all steps with status.
 *
 * Groups steps by phase and renders them as clickable cards with
 * status indicators and gate controls.
 */

const PHASES = [
    // The pipeline's own analysis stage is the PREFLIGHT stage, never
    // "phase 1" - docs/PIPELINE_PLAN.md uses Phase 0/1/2 for the
    // quality-work programme and the two collided. `scan` belongs here
    // too: it enumerates the footage, and it is what the preflight
    // ledger records. See library/tools/step_ledger.py.
    { id: '0', label: 'Setup', steps: ['validate_sfx_library'] },
    { id: 'preflight', label: 'Preflight - Ingest & Analysis', steps: ['scan', 'catalog', 'semantic_analysis', 'temporal_index', 'prosody_analysis'] },
    { id: '2', label: 'Phase 2 - Creative Planning', steps: ['creative_direction', 'speech_sequence', 'music_selection', 'music_analysis', 'mesh_spine'] },
    { id: '3', label: 'Phase 3 - Assembly', steps: ['assign_aroll', 'select_broll', 'review_rough_cut'] },
    { id: '4', label: 'Phase 4 - Post-Production', steps: ['plan_subtitles', 'plan_transitions', 'plan_vfx', 'plan_sfx', 'render_subtitles', 'render_motion_graphics'] },
    { id: '5', label: 'Phase 5 - Finishing', steps: ['color_grade', 'audio_mix', 'creative_cohesion', 'compile_manifest'] },
    { id: '6', label: 'Phase 6 - Render & Validate', steps: ['render', 'validate'] },
];

function getPhaseForStep(stepId) {
    for (const phase of PHASES) {
        if (phase.steps && phase.steps.includes(stepId)) return phase;
        if (phase.prefix && stepId.startsWith(phase.prefix)) return phase;
    }
    return { id: '?', label: 'Other' };
}

async function renderPipelineView() {
    const container = document.getElementById('pipeline-content');
    const steps = state.steps;

    if (!steps.length) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">&#9654;</div>
                <div class="empty-state-text">
                    No pipeline data yet. Run the pipeline to see steps here.
                </div>
            </div>
        `;
        return;
    }

    // Group by phase
    const phaseMap = new Map();
    for (const phase of PHASES) {
        phaseMap.set(phase.id, { ...phase, items: [] });
    }

    for (const step of steps) {
        const phase = getPhaseForStep(step.id);
        const bucket = phaseMap.get(phase.id);
        if (bucket) {
            bucket.items.push(step);
        } else {
            // Fallback
            if (!phaseMap.has('?')) {
                phaseMap.set('?', { id: '?', label: 'Other', items: [] });
            }
            phaseMap.get('?').items.push(step);
        }
    }

    let html = '';
    for (const [_, phase] of phaseMap) {
        if (!phase.items.length) continue;
        html += `<div class="pipeline-phase">`;
        html += `<div class="pipeline-phase-header">${escapeHtml(phase.label)}</div>`;
        for (const step of phase.items) {
            html += renderStepCard(step);
        }
        html += `</div>`;
    }

    container.innerHTML = html;
}

function renderStepCard(step) {
    const statusIcon = getStatusIcon(step.status);
    const statusClass = step.status === 'gate_pending' ? 'gate-pending' :
                        step.status === 'completed' ? 'completed' :
                        step.status === 'failed' ? 'failed' : '';
    const iconClass = step.status === 'gate_pending' ? 'gate-pending' :
                      step.status === 'completed' ? 'completed' :
                      step.status === 'failed' ? 'failed' : 'pending';

    let meta = '';
    if (step.elapsed_s) meta += `${step.elapsed_s.toFixed(1)}s`;
    if (step.output_keys.length) {
        if (meta) meta += ' - ';
        meta += `${step.output_keys.length} outputs`;
    }

    let actions = '';
    if (step.status === 'completed' || step.status === 'gate_pending') {
        actions += `<button class="btn btn-ghost text-sm" onclick="event.stopPropagation(); openStep('${step.id}')">Inspect</button>`;
    }
    
    // We will need to query the actual gate status. In pipeline-view, step.status tells us if it's gate_pending.
    if (step.status === 'gate_pending') {
        actions += renderGateControls(step.id, 'pending');
    }

    return `
        <div class="step-card ${statusClass}" data-step-id="${step.id}" onclick="openStep('${step.id}')">
            <div class="step-status-icon ${iconClass}">${statusIcon}</div>
            <div class="step-info">
                <div class="step-name">${escapeHtml(step.name)}</div>
                <div class="step-meta">${escapeHtml(step.id)}${meta ? ` - ${meta}` : ''}</div>
            </div>
            <div class="step-actions">${actions}</div>
        </div>
    `;
}

function getStatusIcon(status) {
    switch (status) {
        case 'completed': return '&#10003;';
        case 'gate_pending': return '&#9888;';
        case 'failed': return '&#10007;';
        case 'running': return '&#9654;';
        default: return '&#8226;';
    }
}
