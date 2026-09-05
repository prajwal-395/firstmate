/**
 * review-channel.js - anchored notes and the return channel.
 *
 * The two properties the captain's Lavish review pages have, brought onto
 * this dashboard (ruling 2026-08-17 - extend the dashboard, do not author a
 * fresh per-run page):
 *
 *   1. A note attaches to a SPECIFIC THING ON SCREEN. `computeAnchor` runs in
 *      the browser and measures the element: a CSS path (same shape Lavish
 *      computes - id short-circuit, :nth-of-type, at most five hops) plus the
 *      element's tag and visible text. The server stores what the browser
 *      measured and never computes one itself. `resolveAnchor` walks it back
 *      to a live element after the dashboard has re-rendered the view - the
 *      path first, then the tag+text it recorded - which is what makes a note
 *      survive a refresh instead of drifting onto whatever now sits there.
 *
 *   2. QUEUE, THEN ONE SEND. Notes pile up, one click sends the batch, and
 *      that send wakes an agent (POST /api/review/send -> the agent's
 *      /api/review/poll returns). The agent's reply comes back ONTO THIS
 *      SURFACE: threaded under the note in the panel and drawn next to the
 *      anchored element itself. Not a round trip through chat.
 *
 * Backend: library/dashboard/review_channel.py and the /api/review/* routes.
 */

// ── State ──────────────────────────────────────────────────────

const review = {
    active: false,           // annotation mode on/off
    panelOpen: false,
    notes: [],               // every note for this project, newest last
    hoverEl: null,           // element currently under the cursor
    cardCtx: null,           // anchor context of the open note card
    activeNoteId: null,      // note whose reply is drawn beside its element
    pollTimer: null,
    frameQueued: false,
};

const REVIEW_ANCHOR_TEXT_MAX = 240;
// Kept in step with .review-panel / .review-anchor-reply in styles.css: the
// reply is placed around the panel, so it has to know how wide both are.
const REVIEW_PANEL_WIDTH = 340;
const REVIEW_REPLY_WIDTH = 280;

// ── Anchoring (browser-computed) ───────────────────────────────

/**
 * The CSS path to an element. Stops at the first ancestor carrying an id and
 * gives up after five hops, so the path stays short enough to survive a
 * re-render of everything above it.
 */
function computeSelector(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
        let part = node.tagName.toLowerCase();
        if (node.id) {
            part += '#' + CSS.escape(node.id);
            parts.unshift(part);
            break;
        }
        const parent = node.parentElement;
        if (parent) {
            const same = [...parent.children].filter(x => x.tagName === node.tagName);
            if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
        }
        parts.unshift(part);
        node = parent;
    }
    return parts.join(' > ');
}

function elementText(el) {
    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    return text.slice(0, REVIEW_ANCHOR_TEXT_MAX);
}

/** Everything about the anchored element, measured here in the browser. */
function computeAnchor(el) {
    const stepCard = el.closest ? el.closest('[data-step-id]') : null;
    return {
        selector: computeSelector(el),
        tag: (el.tagName || '').toLowerCase(),
        text: elementText(el),
        label: reviewAnchorLabel(el),
        view: state.currentView || '',
        step_id: stepCard ? stepCard.getAttribute('data-step-id') : '',
    };
}

/** A short human name for the thing the note is on, for the note list. */
function reviewAnchorLabel(el) {
    const text = elementText(el);
    if (text) return text.length > 60 ? text.slice(0, 57) + '...' : text;
    return `<${(el.tagName || '?').toLowerCase()}>`;
}

/**
 * Find the anchored element again in the current DOM.
 * The recorded path first; if the view re-rendered into a different shape,
 * fall back to the tag and the text the anchor recorded, so the note lands on
 * the same thing rather than on whatever now occupies that position.
 */
function resolveAnchor(anchor) {
    if (!anchor || !anchor.selector) return null;
    let el = null;
    try {
        el = document.querySelector(anchor.selector);
    } catch (err) {
        el = null;
    }
    if (el && (!anchor.text || elementText(el) === anchor.text)) return el;

    if (anchor.text && anchor.tag) {
        const candidates = document.querySelectorAll(anchor.tag);
        for (const candidate of candidates) {
            if (candidate.closest('[data-review-ui]')) continue;
            if (elementText(candidate) === anchor.text) return candidate;
        }
    }
    return el;
}

// ── UI scaffolding ─────────────────────────────────────────────

function reviewIsOwnUi(el) {
    return !!(el && el.closest && el.closest('[data-review-ui]'));
}

function ensureReviewUi() {
    if (document.getElementById('review-layer')) return;

    const layer = document.createElement('div');
    layer.id = 'review-layer';
    layer.setAttribute('data-review-ui', '1');
    layer.innerHTML = '<div id="review-hover-box" class="review-hover-box hidden"></div>';
    document.body.appendChild(layer);

    const panel = document.createElement('aside');
    panel.id = 'review-panel';
    panel.className = 'review-panel hidden';
    panel.setAttribute('data-review-ui', '1');
    panel.innerHTML = `
        <div class="review-panel-header">
            <h3>Review notes</h3>
            <button class="btn btn-ghost" onclick="toggleReviewPanel(false)">&#10005;</button>
        </div>
        <div id="review-queue" class="review-queue"></div>
        <div class="review-send-row">
            <button id="review-send-btn" class="btn btn-primary" onclick="sendReviewBatch()" disabled>
                Send 0 notes
            </button>
        </div>
        <div id="review-thread" class="review-thread"></div>
    `;
    document.body.appendChild(panel);

    const actions = document.querySelector('.topbar-actions');
    if (actions) {
        const toggle = document.createElement('button');
        toggle.id = 'review-toggle-btn';
        toggle.className = 'btn btn-ghost';
        toggle.setAttribute('data-review-ui', '1');
        toggle.setAttribute('aria-pressed', 'false');
        toggle.innerHTML = '&#9998; Annotate';
        toggle.onclick = () => setAnnotationMode(!review.active);
        actions.insertBefore(toggle, actions.firstChild);

        const notesBtn = document.createElement('button');
        notesBtn.id = 'review-notes-btn';
        notesBtn.className = 'btn btn-ghost';
        notesBtn.setAttribute('data-review-ui', '1');
        notesBtn.innerHTML = 'Notes <span id="review-notes-badge" class="nav-badge">0</span>';
        notesBtn.onclick = () => toggleReviewPanel(!review.panelOpen);
        actions.insertBefore(notesBtn, toggle.nextSibling);
    }
}

// ── Annotation mode ────────────────────────────────────────────

function setAnnotationMode(on) {
    review.active = !!on;
    document.body.classList.toggle('review-annotating', review.active);
    const toggle = document.getElementById('review-toggle-btn');
    if (toggle) {
        toggle.setAttribute('aria-pressed', String(review.active));
        toggle.classList.toggle('btn-primary', review.active);
        toggle.classList.toggle('btn-ghost', !review.active);
    }
    if (!review.active) {
        clearHoverBox();
        closeNoteCard();
    } else {
        toggleReviewPanel(true);
    }
}

function clearHoverBox() {
    const box = document.getElementById('review-hover-box');
    if (box) box.classList.add('hidden');
    review.hoverEl = null;
}

function positionBox(box, el) {
    const rect = el.getBoundingClientRect();
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
    box.classList.remove('hidden');
}

function onReviewMouseMove(event) {
    if (!review.active) return;
    const el = event.target;
    if (!el || el.nodeType !== 1 || reviewIsOwnUi(el)) return clearHoverBox();
    if (el === document.body || el === document.documentElement) return clearHoverBox();
    review.hoverEl = el;
    positionBox(document.getElementById('review-hover-box'), el);
}

function onReviewClick(event) {
    if (!review.active) return;
    const el = event.target;
    if (reviewIsOwnUi(el)) return;
    // In annotation mode a click means "annotate this", never "activate this",
    // so the dashboard's own handlers must not also fire.
    event.preventDefault();
    event.stopPropagation();
    openNoteCard(el);
}

// ── The note card ──────────────────────────────────────────────

function closeNoteCard() {
    const card = document.getElementById('review-card');
    if (card) card.remove();
    review.cardCtx = null;
}

function openNoteCard(el) {
    closeNoteCard();
    const anchor = computeAnchor(el);
    review.cardCtx = anchor;

    const card = document.createElement('div');
    card.id = 'review-card';
    card.className = 'review-card';
    card.setAttribute('data-review-ui', '1');
    const meta = anchor.step_id
        ? `&lt;${escapeHtml(anchor.tag)}&gt; in ${escapeHtml(anchor.step_id)}`
        : `&lt;${escapeHtml(anchor.tag)}&gt;`;
    const modifier = /Mac|iP(hone|ad|od)/.test(navigator.platform) ? '&#8984;' : 'Ctrl';
    card.innerHTML = `
        <div class="review-card-heading">Note on ${meta}</div>
        <div class="review-card-target">${escapeHtml(anchor.label)}</div>
        <textarea id="review-card-text" placeholder="Tell the agent what to change about this..."></textarea>
        <div class="review-card-hint">Enter to queue &middot; ${modifier}+Enter to send now &middot; Esc to cancel</div>
        <div class="review-card-row">
            <button class="btn btn-ghost" onclick="closeNoteCard()">Cancel</button>
            <button class="btn btn-primary" onclick="queueNoteFromCard(false)">Queue</button>
        </div>
    `;
    document.body.appendChild(card);

    const rect = el.getBoundingClientRect();
    const left = Math.min(Math.max(12, rect.left), window.innerWidth - card.offsetWidth - 12);
    const top = Math.min(Math.max(12, rect.bottom + 8), window.innerHeight - card.offsetHeight - 12);
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;

    const textarea = document.getElementById('review-card-text');
    textarea.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') return closeNoteCard();
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            queueNoteFromCard(event.metaKey || event.ctrlKey);
        }
    });
    setTimeout(() => textarea.focus(), 0);
}

async function queueNoteFromCard(sendNow) {
    const textarea = document.getElementById('review-card-text');
    const anchor = review.cardCtx;
    if (!textarea || !anchor) return;
    const text = textarea.value.trim();
    if (!text) return;

    closeNoteCard();
    await apiPost('/review/notes', { text, anchor });
    await loadReviewNotes();
    if (sendNow) await sendReviewBatch();
}

// ── Queue and send ─────────────────────────────────────────────

async function loadReviewNotes() {
    try {
        review.notes = await api('/review/notes');
    } catch (err) {
        review.notes = [];
    }
    renderReviewPanel();
    renderReviewPins();
    syncReviewPolling();
}

async function removeReviewNote(noteId) {
    await api(`/review/notes/${noteId}`, { method: 'DELETE' });
    await loadReviewNotes();
}

/** One send carries every queued note. This is what wakes the agent. */
async function sendReviewBatch() {
    const queued = review.notes.filter(n => n.status === 'queued');
    if (!queued.length) return;
    await apiPost('/review/send', { note_ids: queued.map(n => n.id) });
    await loadReviewNotes();
}

/**
 * While a sent note is still unanswered, watch for the agent's reply so it
 * appears here rather than in whatever terminal the agent is running in.
 */
function syncReviewPolling() {
    const waiting = review.notes.some(n => n.status === 'sent');
    if (waiting && !review.pollTimer) {
        review.pollTimer = setInterval(loadReviewNotes, 2000);
    } else if (!waiting && review.pollTimer) {
        clearInterval(review.pollTimer);
        review.pollTimer = null;
    }
}

// ── Rendering ──────────────────────────────────────────────────

function toggleReviewPanel(open) {
    review.panelOpen = open === undefined ? !review.panelOpen : !!open;
    const panel = document.getElementById('review-panel');
    if (panel) panel.classList.toggle('hidden', !review.panelOpen);
    document.body.classList.toggle('review-panel-open', review.panelOpen);
    if (review.panelOpen) loadReviewNotes();
}

function renderReviewNoteCard(note, index) {
    const anchor = note.anchor || {};
    const where = [anchor.view, anchor.step_id].filter(Boolean).join(' / ');
    const replies = (note.replies || []).map(reply => `
        <div class="review-reply">
            <div class="review-reply-author">${escapeHtml(reply.author || 'agent')} &middot; ${escapeHtml(reply.created_at || '')}</div>
            <div class="review-reply-text">${escapeHtml(reply.text)}</div>
        </div>
    `).join('');
    const remove = note.status === 'queued'
        ? `<button class="btn btn-ghost review-note-remove" onclick="removeReviewNote('${note.id}')">&#10005;</button>`
        : '';
    // A note the captain did not write says so, in the same feed as the
    // ones they did. Automation that is indistinguishable from the
    // captain's own notes is the failure mode this badge exists against.
    // Notes written before the field existed carry no origin and are the
    // captain's - which is what they were.
    const machine = (note.origin && note.origin !== 'captain')
        ? `<span class="review-note-origin" title="written by the ${escapeHtml(note.origin)} layer, not by you">${escapeHtml(note.origin)}</span>`
        : '';

    return `
        <div class="review-note review-note-${escapeHtml(note.status)}"
             data-review-note-id="${note.id}"
             onmouseenter="highlightReviewAnchor('${note.id}')"
             onmouseleave="clearReviewAnchorHighlight()">
            <div class="review-note-head">
                <span class="review-pin-number">${index + 1}</span>
                <span class="review-note-anchor" title="${escapeHtml(anchor.selector || '')}">
                    &lt;${escapeHtml(anchor.tag || '?')}&gt; ${escapeHtml(anchor.label || '')}
                </span>
                ${machine}
                ${remove}
            </div>
            ${where ? `<div class="review-note-where">${escapeHtml(where)}</div>` : ''}
            <div class="review-note-text">${escapeHtml(note.text)}</div>
            <div class="review-note-status">${escapeHtml(note.status)}</div>
            ${replies}
        </div>
    `;
}

function renderReviewPanel() {
    const queueEl = document.getElementById('review-queue');
    const threadEl = document.getElementById('review-thread');
    const sendBtn = document.getElementById('review-send-btn');
    if (!queueEl || !threadEl || !sendBtn) return;

    const queued = review.notes.filter(n => n.status === 'queued');
    const sent = review.notes.filter(n => n.status !== 'queued');

    queueEl.innerHTML = queued.length
        ? queued.map(n => renderReviewNoteCard(n, reviewIndexOf(n))).join('')
        : '<div class="review-empty">Turn on Annotate, then click anything on screen to attach a note to it.</div>';

    sendBtn.disabled = queued.length === 0;
    sendBtn.textContent = `Send ${queued.length} note${queued.length === 1 ? '' : 's'}`;

    const waiting = review.notes.some(n => n.status === 'sent');
    threadEl.innerHTML = (waiting ? '<div class="review-waiting">Waiting for the agent...</div>' : '')
        + sent.slice().reverse().map(n => renderReviewNoteCard(n, reviewIndexOf(n))).join('');

    const badge = document.getElementById('review-notes-badge');
    if (badge) badge.textContent = String(review.notes.length);
}

function reviewIndexOf(note) {
    return review.notes.findIndex(n => n.id === note.id);
}

/**
 * The pins. Each note draws a numbered marker on its own element, and once
 * the agent has replied the reply is drawn right there beside the anchor -
 * the captain reads the answer where they wrote the note.
 */
function renderReviewPins() {
    const layer = document.getElementById('review-layer');
    if (!layer) return;
    for (const old of layer.querySelectorAll('.review-pin, .review-anchor-box, .review-anchor-reply')) {
        old.remove();
    }

    review.notes.forEach((note, index) => {
        const el = resolveAnchor(note.anchor);
        if (!el) return;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return;
        if (rect.bottom < 0 || rect.top > window.innerHeight) return;

        const box = document.createElement('div');
        box.className = `review-anchor-box review-anchor-${note.status}`;
        box.style.left = `${rect.left}px`;
        box.style.top = `${rect.top}px`;
        box.style.width = `${rect.width}px`;
        box.style.height = `${rect.height}px`;
        layer.appendChild(box);

        const pin = document.createElement('button');
        pin.className = `review-pin review-pin-${note.status}`;
        pin.setAttribute('data-review-ui', '1');
        pin.setAttribute('data-review-pin-for', note.id);
        pin.textContent = String(index + 1);
        pin.title = note.text;
        pin.onclick = (event) => {
            event.stopPropagation();
            toggleReviewPanel(true);
            setActiveReviewNote(review.activeNoteId === note.id ? null : note.id);
            const card = document.querySelector(`[data-review-note-id="${note.id}"]`);
            if (card) card.scrollIntoView({ block: 'center' });
        };
        pin.style.left = `${Math.max(4, rect.left - 12)}px`;
        pin.style.top = `${Math.max(4, rect.top - 10)}px`;
        layer.appendChild(pin);

        // The reply is drawn at the element for the note being looked at.
        // Two anchors a line apart would otherwise stack their answers on top
        // of each other and on the thing they are about.
        const latest = (note.replies || [])[note.replies.length - 1];
        if (latest && review.activeNoteId === note.id) {
            const bubble = document.createElement('div');
            bubble.className = 'review-anchor-reply';
            bubble.setAttribute('data-review-ui', '1');
            bubble.setAttribute('data-review-reply-for', note.id);
            bubble.innerHTML = `<span class="review-anchor-reply-author">${escapeHtml(latest.author || 'agent')}</span>${escapeHtml(latest.text)}`;
            // Beside the element when there is room in the content column, and
            // below it when there is not - the panel must never sit on top of
            // the reply, or the answer is not where the note was written.
            const contentRight = (review.panelOpen ? window.innerWidth - REVIEW_PANEL_WIDTH : window.innerWidth) - 12;
            if (rect.right + 10 + REVIEW_REPLY_WIDTH <= contentRight) {
                bubble.style.left = `${rect.right + 10}px`;
                bubble.style.top = `${rect.top}px`;
            } else {
                bubble.style.left = `${Math.max(12, Math.min(rect.left, contentRight - REVIEW_REPLY_WIDTH))}px`;
                bubble.style.top = `${rect.bottom + 6}px`;
            }
            layer.appendChild(bubble);
        }
    });
}

/** Reading a note in the panel points at its element and shows its reply there. */
function highlightReviewAnchor(noteId) {
    const note = review.notes.find(n => n.id === noteId);
    if (!note) return;
    const el = resolveAnchor(note.anchor);
    if (el) positionBox(document.getElementById('review-hover-box'), el);
    setActiveReviewNote(noteId);
}

function clearReviewAnchorHighlight() {
    if (!review.active) clearHoverBox();
    setActiveReviewNote(null);
}

function setActiveReviewNote(noteId) {
    if (review.activeNoteId === noteId) return;
    review.activeNoteId = noteId;
    renderReviewPins();
}

/** Pins are positioned from live rects, so anything that moves them redraws. */
function scheduleReviewRedraw() {
    if (review.frameQueued) return;
    review.frameQueued = true;
    requestAnimationFrame(() => {
        review.frameQueued = false;
        renderReviewPins();
    });
}

// ── Wiring ─────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    ensureReviewUi();
    document.addEventListener('mousemove', onReviewMouseMove, true);
    document.addEventListener('click', onReviewClick, true);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && review.active) setAnnotationMode(false);
    });
    window.addEventListener('scroll', scheduleReviewRedraw, true);
    window.addEventListener('resize', scheduleReviewRedraw);

    // The dashboard re-renders whole views in place; the anchors are recomputed
    // from the new DOM rather than assuming the old elements survived.
    const main = document.getElementById('main-content');
    if (main) new MutationObserver(scheduleReviewRedraw).observe(main, {
        childList: true,
        subtree: true,
    });

    loadReviewNotes();
});
