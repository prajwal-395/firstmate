// (wrapped in an IIFE: see the note by `const P` - this file and
// js/picture.js share one global scope.)
(function () {
'use strict';
// The page.  It draws; it decides nothing about the pipeline.
//
// Everything here is either a live Resolve reading handed over by
// main.js, or arithmetic on it - which item covers a frame, which second
// of the source that is.  Every judgement about the FOOTAGE goes to
// Python over the bridge, which is what keeps the panel's third property
// (logic testable without Resolve) rather than moving it into a second
// language.

const POLL_MS = 500;          // how often the playhead is read
const ITEM_REFRESH_MS = 8000; // how often the item LIST is re-read

let items = [];
let itemsAt = 0;
let ctx = null;
let beats = 0;
let mode = 'source';
let currentMediaPath = null;
let masterPath = null;

// ── What the plugin's own reads cost ─────────────────────────────────
//
// The Qt panel's first property is that it cannot freeze Resolve
// (AGENTS.md section 15), and the panel proved it with an observer
// process timing Resolve from outside.  That is still the check that
// matters to the captain, because it measures what THEY feel.  This is
// the other half of it, and it can only be taken here: how long each
// read costs the plugin, from inside the plugin host, over a sustained
// run rather than in a burst.
//
// Every sample is a real read that the loop below made anyway.  Nothing
// here issues an extra call to Resolve in order to measure it.
const reads = { context: [], items: [], errors: 0, started: Date.now() };

function percentile(sorted, q) {
    if (!sorted.length) return null;
    const i = Math.min(sorted.length - 1, Math.floor(q * sorted.length));
    return sorted[i];
}

function readStats(samples) {
    if (!samples.length) return { n: 0 };
    const s = samples.slice().sort((a, b) => a - b);
    const sum = s.reduce((a, b) => a + b, 0);
    return {
        n: s.length,
        min_ms: s[0],
        p50_ms: percentile(s, 0.5),
        p99_ms: percentile(s, 0.99),
        max_ms: s[s.length - 1],
        mean_ms: Number((sum / s.length).toFixed(3)),
    };
}

function readReport() {
    return {
        measured_from: 'inside the plugin host',
        seconds_running: Number(((Date.now() - reads.started) / 1000).toFixed(1)),
        poll_ms: POLL_MS,
        item_refresh_ms: ITEM_REFRESH_MS,
        errors: reads.errors,
        context_read: readStats(reads.context),
        item_list_read: readStats(reads.items),
    };
}

function drawReads() {
    const el = $('reads-out');
    if (!el) return;
    const r = readReport();
    const line = (label, st) => st.n
        ? `${label}: n=${st.n}  min ${st.min_ms}  p50 ${st.p50_ms}  p99 ${st.p99_ms}  max ${st.max_ms}  mean ${st.mean_ms} ms`
        : `${label}: no samples yet`;
    el.textContent = [
        `running ${r.seconds_running} s   |   playhead polled every ${POLL_MS} ms   |   item list every ${ITEM_REFRESH_MS} ms`,
        line('context read ', r.context_read),
        line('item list read', r.item_list_read),
        `errors: ${r.errors}`,
    ].join('\n');
}


const $ = (id) => document.getElementById(id);

// ── Tabs ─────────────────────────────────────────────────────────────
for (const tab of document.querySelectorAll('.tab')) {
    tab.addEventListener('click', () => {
        for (const t of document.querySelectorAll('.tab')) t.classList.remove('on');
        for (const p of document.querySelectorAll('.pane')) p.classList.remove('on');
        tab.classList.add('on');
        $(tab.dataset.pane).classList.add('on');
    });
}

// ── Which item is the picture ────────────────────────────────────────
//
// One implementation, in js/picture.js, shared with the Electron main
// process and with the test that checks it against Python's answer.
// Read through the namespace, NOT destructured into bare names: both
// files are classic scripts sharing one global scope, so `const
// isOverlay` here collides with picture.js's own top-level declaration
// and the whole page dies at load with a SyntaxError.
const P = window.vepPicture;
const isOverlay = (f) => P.isOverlay(f);
const pictureAt = (list, frame) => P.pictureAt(list, frame);
const overlaysAt = (list, frame) => P.overlaysAt(list, frame);
const sourceSeconds = (item, frame, fps) => P.sourceSeconds(item, frame, fps);

// ── Drawing ──────────────────────────────────────────────────────────
function rows(table, pairs) {
    const body = table.querySelector('tbody');
    body.innerHTML = '';
    for (const [k, v] of pairs) {
        const tr = document.createElement('tr');
        const th = document.createElement('th'); th.textContent = k;
        const td = document.createElement('td'); td.textContent = (v === null || v === undefined || v === '') ? '-' : String(v);
        tr.append(th, td); body.append(tr);
    }
}

function drawPlayhead() {
    if (!ctx) return;
    rows($('playhead-table'), [
        ['page', ctx.page],
        ['project', ctx.project],
        ['timeline', ctx.timeline],
        ['timecode', ctx.timecode],
        ['timeline frame', ctx.timeline_frame],
        ['timeline start frame', ctx.start_frame],
        ['fps', ctx.fps],
        ['drop frame', ctx.drop_frame ? 'yes' : 'no'],
        ['markers on the timeline', ctx.markers.length],
        ['error', ctx.error],
    ]);

    const picture = pictureAt(items, ctx.timeline_frame);
    const topmost = ctx.clip;
    const pairs = [];
    if (!picture) {
        pairs.push(['picture', items.length ? 'nothing on any footage track at this frame'
                                            : 'the item list has not been read yet']);
    } else {
        const secs = sourceSeconds(picture, ctx.timeline_frame, ctx.fps);
        pairs.push(
            ['name', picture.name],
            ['track', 'V' + picture.track],
            ['file', picture.file],
            ['source fps', picture.source_fps],
            ['timeline range', `${picture.start} - ${picture.end}`],
            ['left offset (source frames)', picture.left_offset],
            ['second of the source under the playhead', secs === null ? null : secs.toFixed(3)],
        );
    }
    if (topmost && picture && topmost.name !== picture.name) {
        pairs.push(['Resolve calls current', topmost.name + '  (an overlay, so not the picture)']);
    }
    rows($('clip-table'), pairs);

    const over = overlaysAt(items, ctx.timeline_frame);
    $('overlays').textContent = over.length
        ? over.map((o) => `V${o.track}  ${o.name}`).join('\n')
        : 'none at this frame';

    $('cost').textContent =
        `context read: ${ctx.ms} ms   |   item list: ${items.length} items` +
        (itemsAt ? `, last read ${((Date.now() - itemsAt) / 1000).toFixed(0)} s ago` : ', not read');
}

// ── Video ────────────────────────────────────────────────────────────
const player = $('player');

function setMode(next) {
    mode = next;
    $('mode-source').classList.toggle('on', mode === 'source');
    $('mode-master').classList.toggle('on', mode === 'master');
    currentMediaPath = null;   // force a re-point
    updateVideo(true);
}
$('mode-source').addEventListener('click', () => setMode('source'));
$('mode-master').addEventListener('click', () => setMode('master'));

// How far the player may drift from the playhead before it is re-seeked.
// Seeking on every tick makes the picture stutter; never seeking is not
// following.  Stated as a number rather than left implicit.
const RESEEK_SECONDS = 0.35;

// Which project is this?  Measured off a source file on the timeline, the
// way the panel measures it - the plugin is never told, because being told
// is the thing the browser dashboard already has to do.  So the question
// cannot be asked until at least one footage clip has been seen.
let masterReason = '';

async function findMaster() {
    if (masterPath !== null) return masterPath;
    const anyFootage = items.find((i) => i.file && !isOverlay(i.file));
    if (!anyFootage) {
        masterReason = 'no footage clip has been read off the timeline yet, so ' +
                       'which pipeline project this is has not been measured';
        return '';
    }
    const answer = await window.vep.bridge({
        op: 'render_output', source_file: anyFootage.file });
    masterPath = (answer && answer.ok && answer.path) ? answer.path : '';
    masterReason = (answer && answer.reason) || '';
    return masterPath;
}

async function updateVideo(force) {
    if (!ctx) return;
    const follow = $('follow').checked;

    if (mode === 'source') {
        const picture = pictureAt(items, ctx.timeline_frame);
        if (!picture || !picture.file) {
            $('video-what').textContent =
                'No footage clip under the playhead, so there is nothing to point the player at.';
            $('video-status').textContent = '-';
            return;
        }
        $('video-what').textContent =
            `${picture.file.split('/').pop()} - the source file on disk, ungraded, ` +
            `with no comps, captions or mix.`;
        if (picture.file !== currentMediaPath) {
            const st = await window.vep.stat(picture.file);
            if (!st.exists) {
                $('video-status').textContent = 'the source file is not on disk: ' + picture.file;
                return;
            }
            currentMediaPath = picture.file;
            player.src = window.vep.mediaUrl(picture.file);
            force = true;
        }
        const want = sourceSeconds(picture, ctx.timeline_frame, ctx.fps);
        if (want !== null && (force || (follow && Math.abs(player.currentTime - want) > RESEEK_SECONDS))) {
            if (player.readyState > 0) player.currentTime = want;
            else player.addEventListener('loadedmetadata', () => { player.currentTime = want; }, { once: true });
        }
        $('video-status').textContent =
            `playhead ${ctx.timecode} -> source ${want === null ? '-' : want.toFixed(3)} s   |   ` +
            `player at ${player.currentTime.toFixed(3)} s   |   ` +
            `drift ${want === null ? '-' : (player.currentTime - want).toFixed(3)} s`;
        return;
    }

    // Rendered master.
    const master = await findMaster();
    if (!master) {
        // An absence is SAID, with the reason the bridge gave, rather than
        // shown as a player that never starts.
        $('video-what').textContent = 'No master to play: ' +
            (masterReason || 'nothing recorded a render for this project.');
        $('video-status').textContent = '-';
        return;
    }
    $('video-what').textContent = master + ' - the pipeline’s own export: graded, captioned and mixed, ' +
        'and only as current as the last render.';
    if (master !== currentMediaPath) {
        const st = await window.vep.stat(master);
        if (!st.exists) {
            $('video-status').textContent = 'recorded, but not on disk: ' + master;
            return;
        }
        currentMediaPath = master;
        player.src = window.vep.mediaUrl(master);
        force = true;
    }
    // The master IS the timeline, so timeline time is the master's time -
    // minus the timeline's own start frame, which is not zero.
    const want = (ctx.timeline_frame - (ctx.start_frame || 0)) / ctx.fps;
    if (force || (follow && Math.abs(player.currentTime - want) > RESEEK_SECONDS)) {
        if (player.readyState > 0) player.currentTime = want;
        else player.addEventListener('loadedmetadata', () => { player.currentTime = want; }, { once: true });
    }
    $('video-status').textContent =
        `playhead ${ctx.timecode} -> master ${want.toFixed(3)} s   |   ` +
        `player at ${player.currentTime.toFixed(3)} s   |   ` +
        `drift ${(player.currentTime - want).toFixed(3)} s`;
}

// ── The bridge ───────────────────────────────────────────────────────
$('bridge-ping').addEventListener('click', async () => {
    const answer = await window.vep.bridge({ op: 'ping' });
    $('bridge-out').textContent = JSON.stringify(answer, null, 2);
});

$('bridge-bench').addEventListener('click', async () => {
    const times = [];
    for (let i = 0; i < 10; i++) {
        const answer = await window.vep.bridge({ op: 'ping' });
        times.push(answer.ms);
    }
    times.sort((a, b) => a - b);
    $('bridge-out').textContent =
        `10 round trips, ms: ${times.join(' ')}\n` +
        `min ${times[0]}   median ${times[5]}   max ${times[9]}`;
});

$('bridge-join').addEventListener('click', async () => {
    const picture = pictureAt(items, ctx ? ctx.timeline_frame : null);
    const answer = await window.vep.bridge({
        op: 'clip_facts',
        context: {
            page: ctx.page, project: ctx.project, timeline: ctx.timeline,
            timecode: ctx.timecode, timeline_frame: ctx.timeline_frame,
            fps: ctx.fps, clip: picture, markers: ctx.markers,
            overlays: overlaysAt(items, ctx.timeline_frame),
        },
    });
    $('bridge-out').textContent = JSON.stringify(answer, null, 2);
});

$('api-read').addEventListener('click', async () => {
    const report = await window.vep.apiSurface();
    const lines = [];
    lines.push('WorkflowIntegration module: ' + JSON.stringify(report.moduleInfo));
    lines.push('module keys: ' + (report.module || []).join(', '));
    for (const key of ['resolve', 'project', 'timeline', 'item']) {
        lines.push('');
        lines.push(`${key} (${(report[key] || []).length}):`);
        lines.push('  ' + (report[key] || []).join('\n  '));
    }
    if (report.error) lines.push('\nerror: ' + report.error);
    $('api-out').textContent = lines.join('\n');
});

// Write the two things that can only be established from inside the
// plugin host - what the API exposes, and what the reads cost - to a
// file, so they can be READ rather than transcribed off a screenshot.
$('diagnostics-write').addEventListener('click', async () => {
    const answer = await window.vep.diagnostics({
        api_surface: await window.vep.apiSurface(),
        reads: readReport(),
        context_now: ctx,
        item_count: items.length,
    });
    $('diagnostics-out').textContent = answer.ok
        ? `written: ${answer.path}  (${answer.bytes} bytes)`
        : `could not write: ${answer.error}`;
});

// ── The loop ─────────────────────────────────────────────────────────
//
// The heartbeat is the captain's own evidence that the plugin has not
// stuck, the same job it does in the Qt panel.  Every Resolve call is
// asynchronous over IPC, so nothing here can block Resolve's own UI
// thread waiting on us.
async function tick() {
    try {
        ctx = await window.vep.context();
        if (typeof ctx.ms === 'number') reads.context.push(ctx.ms);
        if (ctx.error) reads.errors += 1;
        if (!items.length || Date.now() - itemsAt > ITEM_REFRESH_MS) {
            const answer = await window.vep.videoItems();
            if (answer && typeof answer.ms === 'number') reads.items.push(answer.ms);
            if (answer && answer.error) reads.errors += 1;
            if (answer && answer.items && answer.items.length) {
                items = answer.items;
                itemsAt = Date.now();
            } else if (answer) {
                itemsAt = Date.now();
            }
        }
        drawPlayhead();
        drawReads();
        await updateVideo(false);
        beats += 1;
        // A fixture run SAYS it is one, on screen, in every screenshot.
        // A picture of this page that does not distinguish recorded data
        // from live Resolve is a picture that can be described wrongly.
        const source = ctx.fixture ? 'FIXTURE (no Resolve): ' + ctx.fixture.split('/').pop() : 'live Resolve';
        $('heartbeat').textContent =
            `beat ${beats}  -  ${source}  -  ${ctx.timecode || 'no timeline'}  -  ${ctx.ms} ms`;
        $('heartbeat').classList.toggle('fixture', Boolean(ctx.fixture));
        $('heartbeat').classList.toggle('bad', Boolean(ctx.error));
    } catch (err) {
        reads.errors += 1;
        $('heartbeat').textContent = 'error: ' + err;
        $('heartbeat').classList.add('bad');
    } finally {
        setTimeout(tick, POLL_MS);
    }
}
tick();

})();
