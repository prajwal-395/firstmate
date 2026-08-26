/**
 * app.js - Core SPA logic for the review dashboard.
 *
 * Manages navigation, data fetching, and view coordination.
 * Components are loaded from /static/components/*.js and register
 * themselves by adding render functions to the global `views` object.
 */

// ── State ──────────────────────────────────────────────────────

const state = {
    project: null,
    steps: [],
    currentView: 'pipeline',
    currentStepId: null,
    gateModalStepId: null,
};

// ── API Helpers ────────────────────────────────────────────────

async function api(path, options = {}) {
    const response = await fetch(`/api${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    if (!response.ok) {
        const text = await response.text();
        console.error(`API error ${response.status}: ${text}`);
        // Carry the server's own words up to the caller. Swallowing them
        // is how a launch that died on an import error read as "started".
        let detail = text;
        try { detail = JSON.parse(text).detail ?? text; } catch (e) { /* plain text */ }
        throw new Error(`API error ${response.status}: ${detail}`);
    }
    return response.json();
}

async function apiPost(path, body) {
    return api(path, {
        method: 'POST',
        body: JSON.stringify(body),
    });
}

// ── Navigation ─────────────────────────────────────────────────

function navigate(viewName, context) {
    // Hide all views
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

    // Update nav items
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navBtn = document.querySelector(`.nav-item[data-view="${viewName}"]`);
    if (navBtn) navBtn.classList.add('active');

    // Show target view
    const viewEl = document.getElementById(`view-${viewName}`);
    if (viewEl) {
        viewEl.classList.add('active');
    }

    state.currentView = viewName;

    // Update breadcrumb
    const breadcrumb = document.getElementById('breadcrumb');
    const labels = {
        pipeline: 'Pipeline Overview',
        messages: 'Messages',
        footage: 'Footage Library',
        'footage-search': 'Footage Search',
        transcript: 'Transcript',
        timeline: 'Timeline',
        'step-detail': context?.stepName || 'Step Detail',
    };
    breadcrumb.textContent = labels[viewName] || viewName;

    // Render the view
    renderView(viewName, context);
}

async function renderView(viewName, context) {
    try {
        switch (viewName) {
            case 'pipeline':
                await renderPipelineView();
                break;
            case 'messages':
                await renderMessagesView();
                break;
            case 'footage':
                await renderFootageLibrary();
                break;
            case 'footage-search':
                await renderFootageSearch();
                break;
            case 'transcript':
                await renderTranscriptView();
                break;
            case 'timeline':
                await renderTimelineView();
                break;
            case 'step-detail':
                if (context?.stepId) {
                    await renderStepDetail(context.stepId);
                }
                break;
        }
    } catch (err) {
        console.error(`Error rendering ${viewName}:`, err);
    }
}

// ── Step Detail Navigation ─────────────────────────────────────

function openStep(stepId) {
    const step = state.steps.find(s => s.id === stepId);
    navigate('step-detail', { stepId, stepName: step?.name || stepId });
}

// ── Inspector ──────────────────────────────────────────────────

function openInspector(title, content) {
    const inspector = document.getElementById('inspector');
    const inspTitle = document.getElementById('inspector-title');
    const inspContent = document.getElementById('inspector-content');

    inspTitle.textContent = title;
    inspContent.innerHTML = content;
    inspector.classList.remove('hidden');
}

function closeInspector() {
    document.getElementById('inspector').classList.add('hidden');
}

// ── Gate Modal ─────────────────────────────────────────────────

function openGateModal(stepId) {
    state.gateModalStepId = stepId;
    const step = state.steps.find(s => s.id === stepId);

    document.getElementById('gate-modal-title').textContent =
        `Review: ${step?.name || stepId}`;
    document.getElementById('gate-feedback').value = '';
    document.getElementById('gate-revisions-json').value = '';
    document.getElementById('gate-revisions').classList.add('hidden');

    // Load step output into modal body
    api(`/steps/${stepId}`).then(detail => {
        const body = document.getElementById('gate-modal-body');
        if (detail.summary_md) {
            body.innerHTML = `<div class="inspector-md">${markdownToHtml(detail.summary_md)}</div>`;
        } else {
            body.innerHTML = '';
            body.appendChild(createJsonViewer(detail.output));
        }
    });

    document.getElementById('gate-modal').classList.remove('hidden');
}

function closeGateModal() {
    document.getElementById('gate-modal').classList.add('hidden');
    state.gateModalStepId = null;
}

async function submitGateAction(action) {
    const stepId = state.gateModalStepId;
    if (!stepId) return;

    const feedback = document.getElementById('gate-feedback').value;
    let revisions = {};

    if (action === 'revise') {
        const revisionsJson = document.getElementById('gate-revisions-json').value;
        if (revisionsJson.trim()) {
            try {
                revisions = JSON.parse(revisionsJson);
            } catch (e) {
                alert('Invalid JSON in revisions field');
                return;
            }
        }
    }

    try {
        await apiPost(`/gates/${stepId}/action`, {
            action,
            feedback,
            revisions,
        });
        closeGateModal();
        await refreshData();
    } catch (err) {
        alert(`Error: ${err.message}`);
    }
}

// Show revisions field when "Revise" is hovered
document.addEventListener('DOMContentLoaded', () => {
    const reviseBtn = document.querySelector('.btn-revise');
    if (reviseBtn) {
        reviseBtn.addEventListener('mouseenter', () => {
            document.getElementById('gate-revisions').classList.remove('hidden');
        });
    }
});

// ── Data Loading ───────────────────────────────────────────────

async function loadProjectInfo() {
    try {
        state.project = await api('/project');
        const select = document.getElementById('project-select');
        if (select && state.project) {
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].value === state.project.project_root) {
                    select.selectedIndex = i;
                    break;
                }
            }
        }
    } catch (err) {
        console.error('No project loaded', err);
    }
}

async function loadProjectsList() {
    try {
        const allProjects = await api('/projects');
        const select = document.getElementById('project-select');
        select.innerHTML = '';
        allProjects.forEach(p => {
            const hasData = p.steps_completed > 0 ? 'has data' : 'empty';
            const text = `${p.name} (${p.slug}) - ${hasData}`;
            const option = document.createElement('option');
            option.value = p.project_root;
            option.textContent = text;
            if (state.project && p.project_root === state.project.project_root) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    } catch (err) {
        console.error("Failed to load projects list", err);
    }
}

async function switchProject(projectDir) {
    if (!projectDir) return;
    try {
        await apiPost('/projects/select', { project_dir: projectDir });
        await refreshData();
    } catch (err) {
        alert("Failed to switch project: " + err.message);
    }
}

async function loadSteps() {
    try {
        state.steps = await api('/steps');
        updateStatusCounts();
    } catch (err) {
        state.steps = [];
    }
}

function updateStatusCounts() {
    const completed = state.steps.filter(s => s.status === 'completed').length;
    const failed = state.steps.filter(s => s.status === 'failed').length;
    const gatePending = state.steps.filter(s => s.status === 'gate_pending').length;
    const pending = state.steps.filter(s => s.status === 'pending').length;

    document.getElementById('completed-count').textContent = completed;
    document.getElementById('gate-count').textContent = gatePending;
    document.getElementById('pending-count').textContent = pending;
    const failedEl = document.getElementById('failed-count');
    if (failedEl) failedEl.textContent = failed;
}

async function refreshData() {
    await Promise.all([loadProjectInfo(), loadSteps()]);
    renderView(state.currentView);
    if (typeof renderMessagesView === 'function' && state.currentView !== 'messages') {
        renderMessagesView(); // to update the badge
    }
}

// ── Markdown Renderer (simple) ─────────────────────────────────

function markdownToHtml(md) {
    if (!md) return '';
    let html = md
        // Code blocks
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
        // Inline code
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        // Headers
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        // Bold
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        // Italic
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // Blockquote
        .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
        // Unordered list
        .replace(/^- (.+)$/gm, '<li>$1</li>')
        // Tables
        .replace(/(?:^|\n)((?:\|[^\n]+\|\n?)+)/g, (match, p1) => {
            const lines = p1.trim().split('\n');
            let rows = '';
            lines.forEach((line, i) => {
                const cells = line.split('|').slice(1, -1);
                if (cells.every(c => /^[\s-:]+$/.test(c))) return; // separator row
                const tag = i === 0 ? 'th' : 'td';
                rows += '<tr>' + cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join('') + '</tr>';
            });
            return '\n<table>' + rows + '</table>\n';
        })
        // Paragraphs (lines not yet wrapped)
        .replace(/^(?!<(?:h[1-6]|ul|ol|li|blockquote|p|table|tr|td|th|thead|tbody|pre|div|!--)\b)(.*\S.*)$/gm, '<p>$1</p>');

    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li>[\s\S]*?<\/li>\s*)+)/g, '<ul>$1</ul>');

    // Clean up empty paragraphs
    html = html.replace(/<p>\s*<\/p>/g, '');

    return html;
}

// ── Pipeline Controls ──────────────────────────────────────────
//
// Four controls, and every one of them reports what the server actually
// did rather than what the button is called.  A launch that returned 500
// says so; a handbrake that was armed with nothing running says that too.

let pipelinePollInterval = null;
let lastRunStatus = null;

/** The launch options the reviewer has selected in the topbar. */
function runOptions() {
    const mode = document.getElementById('run-mode-select')?.value ?? 'agy';
    const review = document.getElementById('run-review-check')?.checked ?? false;
    return {
        full_auto: mode || null,   // '' means manual LLM handoff
        review_mode: review,
    };
}

function announce(text) {
    const detail = document.getElementById('run-state-detail');
    if (detail) detail.textContent = text;
}

async function startPipeline() {
    try {
        const res = await apiPost('/pipeline/run', runOptions());
        announce(`started pid ${res.pid} - ${res.mode}`);
        startPolling();
    } catch (err) {
        alert(err.message);
    } finally {
        await refreshRunStatus();
    }
}

async function stepPipeline() {
    try {
        const res = await apiPost('/pipeline/step', runOptions());
        announce(`stepping ${res.step_id} (${res.step_name}) - pid ${res.pid}`);
        startPolling();
    } catch (err) {
        alert(err.message);
    } finally {
        await refreshRunStatus();
    }
}

async function pausePipeline() {
    // The handbrake. It does not kill the run: the step in flight finishes
    // and writes its state, and the runner stops at the next boundary.
    try {
        const res = await apiPost('/pipeline/pause', { reason: 'dashboard handbrake' });
        announce(res.effect + (res.holding_after_step
            ? ` (after ${res.holding_after_step})` : ''));
        startPolling();
    } catch (err) {
        alert(err.message);
    } finally {
        await refreshRunStatus();
    }
}

async function resumePipeline() {
    try {
        const res = await apiPost('/pipeline/resume', runOptions());
        announce(`resumed at ${res.next_step || 'next pending step'} - pid ${res.pid}`);
        startPolling();
    } catch (err) {
        alert(err.message);
    } finally {
        await refreshRunStatus();
    }
}

/** Paint the run status strip from what the runner wrote to disk. */
function renderRunStatus(status) {
    lastRunStatus = status;

    let state = status.run_state || 'idle';
    if (status.is_running) state = 'running';
    if (status.hold_requested && status.is_running) state = 'holding';

    const dot = document.getElementById('run-state-dot');
    const label = document.getElementById('run-state-label');
    const detail = document.getElementById('run-state-detail');
    const mode = document.getElementById('run-state-mode');
    if (!dot || !label) return;

    dot.className = 'run-state-dot ' +
        (state === 'holding' ? 'running' : state);
    label.textContent = state;

    const bits = [];
    if (status.is_running && status.current_step) {
        bits.push(`running ${status.current_step}` +
            (status.current_step_name ? ` (${status.current_step_name})` : ''));
    }
    if (status.hold_requested) {
        bits.push(status.is_running
            ? 'handbrake engaged - will hold after this step'
            : `handbrake engaged since ${status.hold_requested_at || 'unknown'}`);
    }
    if (status.held_before_step && !status.is_running) {
        bits.push(`held before ${status.held_before_step}`);
    }
    if (status.last_completed_step) {
        bits.push(`last done ${status.last_completed_step}`);
    }
    if (status.next_step) bits.push(`next ${status.next_step}`);
    if (status.pending_gates?.length) {
        bits.push(`gates pending: ${status.pending_gates.join(', ')}`);
    }
    detail.textContent = bits.join('  ·  ');
    mode.textContent = status.mode || '';

    // A control that cannot do anything should not look pressable.
    const busy = status.is_running;
    const setDisabled = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.disabled = v;
    };
    setDisabled('pipeline-start-btn', busy);
    setDisabled('pipeline-step-btn', busy);
    setDisabled('pipeline-resume-btn', busy);
    setDisabled('pipeline-pause-btn', !busy && status.hold_requested);
}

async function refreshRunStatus() {
    try {
        const status = await api('/pipeline/status');
        renderRunStatus(status);
        return status;
    } catch (err) {
        console.error('run status unavailable', err);
        return null;
    }
}

function startPolling() {
    if (pipelinePollInterval) return;
    pipelinePollInterval = setInterval(async () => {
        const status = await refreshRunStatus();
        await refreshData();
        // Keep polling while a hold is engaged: the run is still moving
        // towards its stopping point and the strip should show it land.
        if (status && !status.is_running && !status.hold_requested) {
            stopPolling();
        }
    }, 2000);
}

function stopPolling() {
    if (pipelinePollInterval) {
        clearInterval(pipelinePollInterval);
        pipelinePollInterval = null;
    }
}

// ── Escaping ───────────────────────────────────────────────────

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ── Initialization ─────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    await loadProjectsList();
    await refreshData();
    navigate('pipeline');
    const status = await refreshRunStatus();
    // A run started from the terminal is still a run: pick it up.
    if (status && (status.is_running || status.hold_requested)) startPolling();
});
