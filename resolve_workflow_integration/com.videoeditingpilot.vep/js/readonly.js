// What this plugin is allowed to ask DaVinci Resolve.
//
// The captain is at the keyboard while this runs, and Resolve is one
// shared instance. So the plugin READS, and the rule is an enumeration
// rather than a habit: `permits` answers for one method name, `main.js`
// routes every single Resolve call through it, and a name that is not
// on the list throws instead of being called.
//
// It is in its own file, loadable by plain node, so the rule can be
// TESTED rather than grepped for - see
// `tests/test_workflow_integration_plugin.py`.

// Every Resolve method the plugin may call. All reads.
const READ_ONLY_CALLS = new Set([
    'GetCurrentPage', 'GetProjectManager', 'GetCurrentProject', 'GetName',
    'GetCurrentTimeline', 'GetCurrentTimecode', 'GetSetting', 'GetStartFrame',
    'GetEndFrame', 'GetCurrentVideoItem', 'GetMarkers', 'GetItemListInTrack',
    'GetTrackCount', 'GetMediaPoolItem', 'GetClipProperty', 'GetStart',
    'GetEnd', 'GetDuration', 'GetLeftOffset', 'GetProductName',
    'GetVersionString', 'GetTrackName',
]);

// Methods that would change what the captain is looking at, or write
// into their project. Named so a refusal can say WHICH kind of call was
// attempted rather than only that it was not on the list.
//
// `GrabStill` and `ExportStills` are here deliberately and are not an
// oversight: a still grab is how the Script surface captures the graded
// frame (`library/tools/marker_capture.py`), it costs seconds, and it
// round-trips the gallery - which writes into the captain's project.
// That is phase 2's business, with the captain's knowledge, not a
// read-only panel's.
const WITHHELD_CALLS = new Set([
    'OpenPage', 'SetCurrentTimeline', 'LoadProject', 'CreateProject',
    'SaveProject', 'AddRenderJob', 'StartRendering', 'DeleteTimelines',
    'AddMarker', 'UpdateMarkerCustomData', 'DeleteMarkerAtFrame',
    'SetSetting', 'ImportTimelineFromFile', 'AppendToTimeline',
    'SetCurrentTimecode', 'GrabStill', 'ExportStills',
    'AddSubFolder', 'DeleteFolders', 'AddItemListToMediaPool',
    'CreateTimelineFromClips', 'SetProperty', 'SetCDL', 'Stabilize',
]);

// Why a call is refused, or the empty string when it is allowed.
//
// The default is REFUSAL: an unknown name is not permitted just because
// nobody thought to withhold it, which is what keeps the list complete
// rather than merely long.
function refusal(method) {
    if (WITHHELD_CALLS.has(method)) {
        return method + " would change the captain's session; this plugin " +
               'is read-only (see js/readonly.js)';
    }
    if (!READ_ONLY_CALLS.has(method)) {
        return method + ' is not in READ_ONLY_CALLS';
    }
    return '';
}

const permits = (method) => refusal(method) === '';

const API = { READ_ONLY_CALLS, WITHHELD_CALLS, refusal, permits };
if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (typeof window !== 'undefined') window.vepReadOnly = API;
