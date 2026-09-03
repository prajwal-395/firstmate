// The whole of what the page can ask for.
//
// The renderer is sandboxed with context isolation on (Resolve 19.0.2 and
// later enforce it), so it has no Node, no `require`, and no filesystem.
// This bridge is the complete surface, and every entry is a READ.  There
// is deliberately no call here that changes anything in Resolve - see
// READ_ONLY_CALLS in main.js.

const { contextBridge, ipcRenderer } = require('electron/renderer');

contextBridge.exposeInMainWorld('vep', {
    // Live Resolve, cheap: page, project, timeline, timecode, playhead
    // frame, the clip Resolve calls current, and every marker.
    context: () => ipcRenderer.invoke('vep:context'),

    // Every video item on the timeline.  Expensive; read once and cached
    // by the caller, because what changes twice a second is the frame.
    videoItems: (trackCount) => ipcRenderer.invoke('vep:videoItems', trackCount),

    // What the JavaScript API really exposes, enumerated off the live
    // objects rather than assumed to match the Python API.
    apiSurface: () => ipcRenderer.invoke('vep:apiSurface'),

    // One request to the repository's Python.  Returns the answer plus
    // the round trip in `ms`, always measured, never estimated.
    bridge: (request) => ipcRenderer.invoke('vep:bridge', request),

    // Does this file exist, and how big is it?  Asked before pointing a
    // <video> at it so an absence is stated rather than shown as a
    // player that never starts.
    stat: (filePath) => ipcRenderer.invoke('vep:stat', filePath),

    // The plugin's own account of itself, written to a file so the
    // enumeration and the timings can be READ rather than transcribed
    // off a screenshot.  It writes to the system temporary directory and
    // returns the path; nothing under the captain's project is touched.
    diagnostics: (payload) => ipcRenderer.invoke('vep:diagnostics', payload),

    repoRoot: () => ipcRenderer.invoke('vep:repoRoot'),

    // The scheme main.js serves local media on, with Range support so a
    // <video> can be seeked.  Building the URL is the page's job; only
    // the scheme name comes from here.
    mediaUrl: (filePath) => 'vepmedia://local' + encodeURI(filePath).replace(/#/g, '%23'),
});
