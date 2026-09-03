// Which clip is the picture, and which second of it is under the playhead.
//
// This is the plugin's only arithmetic, and it is kept in ONE file that
// three things load: the page, the Electron main process, and a test run
// under plain node with no Resolve and no Electron anywhere near it.
//
// It is a deliberate second implementation of
// `library/tools/panel/clip_context.picture_at`, and AGENTS.md section
// 10.1 is exactly why that is dangerous: two surfaces answering "which
// clip is under the playhead" differently would be a defect nobody could
// see.  `tests/test_workflow_integration_plugin.py` therefore runs BOTH
// over the same recorded timeline and fails if they ever disagree.

// Everything the PIPELINE renders - subtitle cards, motion graphics,
// timed text - lands under `pipeline_output/`, and everything the captain
// SHOT is elsewhere (AGENTS.md section 8).  That is a fact about the
// project layout rather than a guess about a filename, and it is what
// tells a picture clip from one of the pipeline's own overlays.
const OUTPUT_MARKER = '/pipeline_output/';

function isOverlay(file) {
    return String(file || '').includes(OUTPUT_MARKER);
}

function covers(item, frame) {
    return Boolean(item) && item.start !== null && item.start !== undefined &&
        item.end !== null && item.end !== undefined &&
        frame >= item.start && frame < item.end;
}

// The picture is the highest video track carrying FOOTAGE - NOT the
// topmost item, which on a finished build is a subtitle card.
function pictureAt(items, frame) {
    if (frame === null || frame === undefined) return null;
    let best = null;
    for (const item of items) {
        if (!covers(item, frame)) continue;
        if (isOverlay(item.file)) continue;
        if (!best || (item.track || 0) > (best.track || 0)) best = item;
    }
    return best;
}

function overlaysAt(items, frame) {
    return items.filter((i) => covers(i, frame) && isOverlay(i.file));
}

// Where in the SOURCE file the playhead is.
//
// `left_offset` is an offset in SOURCE frames, so it is divided by the
// source's own frame rate; the elapsed part of the placement is TIMELINE
// frames over the timeline's rate.  When the two rates agree this
// collapses to the obvious form, and when they do not this is the only
// version that is right.
function sourceSeconds(item, frame, timelineFps) {
    if (!item || frame === null || frame === undefined) return null;
    const srcFps = item.source_fps || timelineFps;
    return (item.left_offset || 0) / srcFps + (frame - item.start) / timelineFps;
}

// Timecode to seconds.  Non-drop only; a drop-frame timecode uses ';'
// and is REPORTED rather than silently mis-converted, because a wrong
// number here would move the picture and say nothing.
function timecodeSeconds(tc, fps) {
    if (!tc) return null;
    const dropFrame = String(tc).includes(';');
    const parts = String(tc).replace(/;/g, ':').split(':');
    if (parts.length !== 4) return null;
    const [h, m, s, f] = parts.map(Number);
    if ([h, m, s, f].some(Number.isNaN)) return null;
    return { seconds: h * 3600 + m * 60 + s + f / fps, dropFrame };
}

const API = { OUTPUT_MARKER, isOverlay, covers, pictureAt, overlaysAt,
              sourceSeconds, timecodeSeconds };

// Loaded three ways: `require` in the Electron main process and in a
// node test, and a plain <script> tag in the sandboxed page, which has
// no module system at all.
if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (typeof window !== 'undefined') window.vepPicture = API;
