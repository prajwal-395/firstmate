// The VEP Pipeline Workflow Integration - Electron main process.
//
// This is the ONLY file in the plugin that touches Resolve, and the only
// one that starts a child process.  The renderer is a sandboxed page with
// no Node at all; everything it can ask for is enumerated in preload.js.
//
// Three rules this file is built around, carried over from the Qt panel
// (AGENTS.md section 15) because a new surface does not get to drop them:
//
//   1. It cannot disturb the captain's session.  `js/readonly.js` is the
//      complete list of Resolve methods this plugin may call, `guard`
//      below is the one door, and the default is REFUSAL.  Nothing here
//      opens a page, switches a timeline, renders, or writes a marker.
//   2. It holds no credential.  There is no network client in this file.
//      The model is reached the way the panel reaches it - by shelling
//      out to an already-authenticated CLI - and that has not moved here.
//   3. Its logic is testable without Resolve.  That is why this file is
//      thin: it READS Resolve and hands plain JSON to Python.  Every
//      judgement - the catalog join, the trace, the run preview - stays
//      in `library/tools/panel/`, which has tests that never open the
//      application.  See bridge() below.

const { app, BrowserWindow, ipcMain, protocol, net, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { execFile } = require('child_process');
const WorkflowIntegration = require('./WorkflowIntegration.node');
const picture = require('./js/picture.js');

const PLUGIN_ID = 'com.videoeditingpilot.vep';

// The checkout this plugin belongs to.  Stamped in by
// scripts/install_workflow_integration.sh, exactly the way the Script
// surface's entry points are stamped, so the installed copy reads the
// repository rather than a frozen copy of it.
const REPO_ROOT = "";

let mainWindow = null;
let resolveObj = null;

// ── Fixture mode ─────────────────────────────────────────────────────
//
// `VEP_WFI_FIXTURE=<path to json>` makes the three Resolve readers below
// answer from a RECORDING instead of from the application.  This is the
// JavaScript side's answer to the Qt panel's third property (AGENTS.md
// section 15): the panel keeps its logic in five Resolve-free Python
// modules, and the equivalent here is that the page, the playhead
// arithmetic and the video player can all be driven and SEEN with no
// Resolve running at all.
//
// It is a recording, not a simulation: `tests/fixtures/workflow_integration/`
// is generated from a real built timeline's own assembly manifest, so the
// frames, the source offsets and the file paths are the ones that shipped.
// A fixture that made numbers up would be a gate that cannot fail.
const FIXTURE_PATH = process.env.VEP_WFI_FIXTURE || '';
let fixture = null;
if (FIXTURE_PATH) {
    try {
        fixture = JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8'));
        console.log('VEP: fixture mode, reading ' + FIXTURE_PATH);
    } catch (err) {
        console.log('VEP: fixture unreadable: ' + err);
    }
}

// ── The read-only contract ───────────────────────────────────────────
//
// The enumeration is `js/readonly.js`, in its own file so it can be
// TESTED under plain node rather than grepped for.  Every Resolve call
// this file makes goes through `guard`, and a name the enumeration does
// not permit throws instead of being called - so a later edit cannot
// quietly acquire the ability to change the captain's timeline.
const readOnly = require('./js/readonly.js');

async function guard(obj, method, ...args) {
    const refused = readOnly.refusal(method);
    if (refused) throw new Error(refused);
    if (!obj || typeof obj[method] !== 'function') return null;
    return await obj[method](...args);
}

// ── Resolve ──────────────────────────────────────────────────────────

async function getResolve() {
    if (resolveObj) return resolveObj;
    const ok = await WorkflowIntegration.Initialize(PLUGIN_ID);
    if (!ok) return null;
    resolveObj = await WorkflowIntegration.GetResolve();
    return resolveObj;
}

async function itemDict(item, track) {
    // One timeline item, in exactly the shape
    // library/tools/panel/clip_context.ResolveContext.clip already reads.
    // The vocabulary is deliberately unchanged across the two surfaces.
    if (!item) return null;
    try {
        const pool = await guard(item, 'GetMediaPoolItem');
        let file = null, sourceFps = null;
        if (pool) {
            file = await guard(pool, 'GetClipProperty', 'File Path');
            const fps = await guard(pool, 'GetClipProperty', 'FPS');
            const parsed = parseFloat(fps);
            if (!Number.isNaN(parsed) && parsed > 0) sourceFps = parsed;
        }
        return {
            name: await guard(item, 'GetName'),
            start: await guard(item, 'GetStart'),
            end: await guard(item, 'GetEnd'),
            duration: await guard(item, 'GetDuration'),
            left_offset: await guard(item, 'GetLeftOffset'),
            track: track === undefined ? null : track,
            file: file,
            source_fps: sourceFps,
        };
    } catch (err) {
        return null;
    }
}

// The live facts, once per tick.  Cheap calls only: the item LIST is read
// separately and cached, because it is a hundred-odd scripting calls and
// what changes twice a second is the frame, not the edit.
async function readContext() {
    const t0 = Date.now();
    if (fixture) {
        const out = Object.assign({}, fixture.context);
        out.ms = Date.now() - t0;
        out.fixture = FIXTURE_PATH;
        return out;
    }
    const out = {
        page: '', project: '', timeline: '', timecode: '', timeline_frame: null,
        fps: 30.0, start_frame: null, clip: null, markers: [], error: '',
        drop_frame: false, ms: 0, calls: 0,
    };
    try {
        const resolve = await getResolve();
        if (!resolve) { out.error = 'Resolve interface not initialized'; return out; }
        out.page = (await guard(resolve, 'GetCurrentPage')) || '';
        const pm = await guard(resolve, 'GetProjectManager');
        const project = pm ? await guard(pm, 'GetCurrentProject') : null;
        if (!project) { out.error = 'no project open'; return out; }
        out.project = (await guard(project, 'GetName')) || '';
        const timeline = await guard(project, 'GetCurrentTimeline');
        if (!timeline) { out.error = 'no timeline open'; return out; }
        out.timeline = (await guard(timeline, 'GetName')) || '';
        out.timecode = (await guard(timeline, 'GetCurrentTimecode')) || '';
        const rate = await guard(project, 'GetSetting', 'timelineFrameRate');
        const fps = parseFloat(rate);
        out.fps = (!Number.isNaN(fps) && fps > 0) ? fps : 30.0;
        out.start_frame = await guard(timeline, 'GetStartFrame');
        const tc = picture.timecodeSeconds(out.timecode, out.fps);
        if (tc) {
            out.timeline_frame = Math.round(tc.seconds * out.fps);
            out.drop_frame = tc.dropFrame;
        }
        out.clip = await itemDict(await guard(timeline, 'GetCurrentVideoItem'));
        const markers = (await guard(timeline, 'GetMarkers')) || {};
        // Both `name` and `note` are typed text and both are read
        // (AGENTS.md section 15); reading only `note` loses the field the
        // cursor lands in.
        for (const frame of Object.keys(markers).sort((a, b) => a - b)) {
            const m = markers[frame];
            out.markers.push({
                frame: Number(frame), name: m.name || '', note: m.note || '',
                color: m.color || '', custom: m.customData || '',
            });
        }
    } catch (err) {
        out.error = String(err && err.message ? err.message : err);
    }
    out.ms = Date.now() - t0;
    return out;
}

// Every video item on the timeline, once.  The renderer decides which one
// covers a frame; that is arithmetic and needs no Resolve.
async function readVideoItems(event, trackCount) {
    const t0 = Date.now();
    const items = [];
    let calls = 0;
    if (fixture) return { items: fixture.items || [], ms: Date.now() - t0, fixture: FIXTURE_PATH };
    try {
        const resolve = await getResolve();
        if (!resolve) return { items, ms: 0, error: 'not initialized' };
        const pm = await guard(resolve, 'GetProjectManager');
        const project = pm ? await guard(pm, 'GetCurrentProject') : null;
        const timeline = project ? await guard(project, 'GetCurrentTimeline') : null;
        if (!timeline) return { items, ms: Date.now() - t0, error: 'no timeline' };
        const tracks = trackCount || (await guard(timeline, 'GetTrackCount', 'video')) || 8;
        for (let track = 1; track <= tracks; track++) {
            let found;
            try {
                found = (await guard(timeline, 'GetItemListInTrack', 'video', track)) || [];
            } catch (err) { continue; }
            for (const item of found) {
                const entry = await itemDict(item, track);
                calls += 8;
                if (entry) items.push(entry);
            }
        }
    } catch (err) {
        return { items, ms: Date.now() - t0, error: String(err) };
    }
    return { items, ms: Date.now() - t0, calls };
}

// What the JavaScript API actually exposes.  Enumerated rather than
// assumed: the Python API is the one this project knows, and a gap here
// is the kind of thing that invalidates a whole surface, so the plugin
// reports the real key list instead of anybody guessing at parity.
async function apiSurface() {
    const report = { resolve: [], project: [], timeline: [], item: [], error: '' };
    if (fixture) return Object.assign({ fixture: FIXTURE_PATH }, fixture.apiSurface || {});
    try {
        const resolve = await getResolve();
        if (!resolve) { report.error = 'not initialized'; return report; }
        const names = (o) => {
            const out = new Set();
            for (let p = o; p && p !== Object.prototype; p = Object.getPrototypeOf(p)) {
                for (const k of Object.getOwnPropertyNames(p)) out.add(k);
            }
            return [...out].sort();
        };
        report.resolve = names(resolve);
        const pm = await guard(resolve, 'GetProjectManager');
        const project = pm ? await guard(pm, 'GetCurrentProject') : null;
        if (project) report.project = names(project);
        const timeline = project ? await guard(project, 'GetCurrentTimeline') : null;
        if (timeline) {
            report.timeline = names(timeline);
            const item = await guard(timeline, 'GetCurrentVideoItem');
            if (item) report.item = names(item);
        }
        report.module = Object.keys(WorkflowIntegration);
        report.moduleInfo = WorkflowIntegration.GetInfo ? WorkflowIntegration.GetInfo() : null;
    } catch (err) {
        report.error = String(err);
    }
    return report;
}

// ── The bridge to our Python ─────────────────────────────────────────
//
// The smallest working form: one short-lived child process per request,
// speaking JSON on stdin and stdout.  It is deliberately the smallest
// thing that works, and its cost is MEASURED and returned on every call
// (`ms`) rather than assumed, because a per-request process spawn is the
// obvious thing to be wrong about.
//
// The reason the bridge is worth its cost is the third property above:
// every judgement stays in Python, where it already has tests that never
// open Resolve.  The JavaScript side reads Resolve and draws; it decides
// nothing.
function pythonExecutable() {
    // The panel's rule (AGENTS.md section 15), and it applies here for
    // the same reason: Electron is launched by Resolve, so there is no
    // useful interpreter on PATH.  The checkout's own venv or nothing.
    const venv = path.join(REPO_ROOT, '.venv', 'bin', 'python3');
    if (REPO_ROOT && fs.existsSync(venv)) return venv;
    return '/usr/bin/python3';
}

function bridge(event, request) {
    const t0 = Date.now();
    return new Promise((resolve) => {
        if (!REPO_ROOT) {
            resolve({ ok: false, error: 'REPO_ROOT was not stamped in; run scripts/install_workflow_integration.sh', ms: 0 });
            return;
        }
        const child = execFile(
            pythonExecutable(),
            ['-m', 'library.tools.workflow_bridge'],
            { cwd: REPO_ROOT, maxBuffer: 64 * 1024 * 1024, encoding: 'utf-8' },
            (err, stdout, stderr) => {
                const ms = Date.now() - t0;
                if (err) {
                    resolve({ ok: false, error: String(err), stderr: String(stderr || ''), ms });
                    return;
                }
                try {
                    const parsed = JSON.parse(stdout);
                    parsed.ms = ms;
                    resolve(parsed);
                } catch (parseErr) {
                    resolve({ ok: false, error: 'bridge did not return JSON: ' + parseErr,
                              stdout: String(stdout).slice(0, 4000), stderr: String(stderr || ''), ms });
                }
            });
        child.stdin.write(JSON.stringify(request));
        child.stdin.end();
    });
}

// ── Media ────────────────────────────────────────────────────────────
//
// A custom scheme rather than file://, so the page keeps a real origin
// and so RANGE requests are answered - without them a <video> can be
// played from the start and cannot be SEEKED, which is the whole of
// following a playhead.
const MEDIA_SCHEME = 'vepmedia';

function registerMediaProtocol() {
    protocol.handle(MEDIA_SCHEME, async (request) => {
        const url = new URL(request.url);
        const filePath = decodeURIComponent(url.pathname);
        let stat;
        try {
            stat = fs.statSync(filePath);
        } catch (err) {
            return new Response('not found: ' + filePath, { status: 404 });
        }
        const type = mimeFor(filePath);
        const range = request.headers.get('Range');
        if (!range) {
            return new Response(fs.createReadStream(filePath), {
                status: 200,
                headers: {
                    'Content-Type': type,
                    'Content-Length': String(stat.size),
                    'Accept-Ranges': 'bytes',
                },
            });
        }
        const match = /bytes=(\d*)-(\d*)/.exec(range);
        const start = match && match[1] ? parseInt(match[1], 10) : 0;
        const end = match && match[2] ? parseInt(match[2], 10) : stat.size - 1;
        const stream = fs.createReadStream(filePath, { start, end });
        return new Response(stream, {
            status: 206,
            headers: {
                'Content-Type': type,
                'Content-Length': String(end - start + 1),
                'Content-Range': `bytes ${start}-${end}/${stat.size}`,
                'Accept-Ranges': 'bytes',
            },
        });
    });
}

function mimeFor(p) {
    const ext = path.extname(p).toLowerCase();
    if (ext === '.mov') return 'video/quicktime';
    if (ext === '.mp4' || ext === '.m4v') return 'video/mp4';
    if (ext === '.webm') return 'video/webm';
    if (ext === '.png') return 'image/png';
    if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
    if (ext === '.wav') return 'audio/wav';
    return 'application/octet-stream';
}

// Whether a file exists and how big it is - asked before a <video> is
// pointed at it, so a missing master is SAID rather than showing as a
// player that never starts.
function statFile(event, filePath) {
    try {
        const s = fs.statSync(filePath);
        return { exists: true, size: s.size, mtime: s.mtimeMs };
    } catch (err) {
        return { exists: false, error: String(err && err.code || err) };
    }
}

// ── The plugin's own account of itself ───────────────────────────────
//
// The API enumeration and the read timings are the two things about this
// surface that can only be established from INSIDE the plugin host, and
// both are lists too long to read off a screenshot without transcribing
// them.  So the page can ask for them to be written down.
//
// It writes ONE json file to the system temporary directory and returns
// the path.  Nothing under the captain's project is touched, and nothing
// is written unless the page asks - a plugin that wrote diagnostics on
// its own would be a plugin that writes.
function writeDiagnostics(event, payload) {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const file = path.join(os.tmpdir(), `vep-wfi-diagnostics-${stamp}.json`);
    const record = Object.assign({
        plugin: PLUGIN_ID,
        written_at: new Date().toISOString(),
        // What the plugin is actually running inside.  Resolve ships its
        // own Electron and the plugin brings none, so these are Resolve's
        // numbers, not ours - which is the point of recording them.
        host: {
            electron: process.versions.electron,
            chrome: process.versions.chrome,
            node: process.versions.node,
            v8: process.versions.v8,
            platform: process.platform,
            arch: process.arch,
            resolve_pid: process.ppid,
            argv: process.argv,
        },
        fixture: FIXTURE_PATH || null,
        repo_root: REPO_ROOT,
    }, payload || {});
    try {
        fs.writeFileSync(file, JSON.stringify(record, null, 2), 'utf-8');
        return { ok: true, path: file, bytes: fs.statSync(file).size };
    } catch (err) {
        return { ok: false, error: String(err) };
    }
}

protocol.registerSchemesAsPrivileged([{
    scheme: MEDIA_SCHEME,
    privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, bypassCSP: true },
}]);

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1180,
        height: 900,
        useContentSize: true,
        backgroundColor: '#1b1b1e',
        title: 'VEP Pipeline',
        // Fixture mode never maps a window: the page, the playhead
        // arithmetic and the loop all run identically hidden, so a
        // test drive (or a stray env var) can never pop the panel
        // onto the captain's desktop and steal focus. A hidden
        // window is still a LOADED page - DOM, preload bridge and
        // timers all run; only compositing to screen is skipped.
        show: !FIXTURE_PATH,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            sandbox: true,
            contextIsolation: true,
            nodeIntegration: false,
        },
    });
    mainWindow.setMenu(null);
    mainWindow.on('close', () => { app.quit(); });
    mainWindow.loadFile('index.html');
    if (process.env.VEP_WFI_DEVTOOLS) mainWindow.webContents.openDevTools({ mode: 'detach' });
}

app.whenReady().then(() => {
    registerMediaProtocol();
    ipcMain.handle('vep:context', readContext);
    ipcMain.handle('vep:videoItems', readVideoItems);
    ipcMain.handle('vep:apiSurface', apiSurface);
    ipcMain.handle('vep:bridge', bridge);
    ipcMain.handle('vep:stat', statFile);
    ipcMain.handle('vep:diagnostics', writeDiagnostics);
    ipcMain.handle('vep:repoRoot', () => REPO_ROOT);
    createWindow();
});

app.on('window-all-closed', () => {
    try { WorkflowIntegration.CleanUp(); } catch (err) { /* leaving anyway */ }
    app.quit();
});
