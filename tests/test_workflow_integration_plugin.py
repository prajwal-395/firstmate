"""The DaVinci Resolve Workflow Integration, checked without Resolve.

The plugin is an Electron app, so most of it cannot be exercised here.
What CAN be checked without the application is the part that would fail
silently: the read-only contract, the absence of a credential, and - the
one that matters most - whether the plugin's JavaScript and this
repository's Python still agree about which clip is under the playhead.

AGENTS.md section 10.1 is why that last one is a test rather than a
comment.  `js/picture.js` is a deliberate second implementation of
`clip_context.picture_at`, and two surfaces answering the same question
differently is a defect nobody could see from either side.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from library.tools.panel import clip_context

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "com.videoeditingpilot.vep"
PLUGIN = REPO_ROOT / "resolve_workflow_integration" / PLUGIN_ID
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "workflow_integration" / "built_timeline.json"

NO_NODE = "node is not on PATH, so the plugin's JavaScript cannot be run here"


def _text(name: str) -> str:
    return (PLUGIN / name).read_text(encoding="utf-8")


# ── The shape Resolve requires ───────────────────────────────────────

def test_the_manifest_id_is_the_directory_name_and_the_id_main_js_uses():
    """Resolve keys the plugin's IPC channel on the manifest id, and
    `Initialize` is handed the same string; a disagreement is a plugin
    that loads and can never reach Resolve."""
    root = ET.parse(PLUGIN / "manifest.xml").getroot()
    declared = root.find("./Plugin/Id").text.strip()
    assert declared == PLUGIN_ID == PLUGIN.name
    assert "const PLUGIN_ID = '%s';" % PLUGIN_ID in _text("main.js")
    assert root.find("./Plugin/FilePath").text.strip() == "main.js"


def test_the_files_resolve_loads_are_all_present():
    for name in ("main.js", "preload.js", "index.html", "manifest.xml",
                 "package.json", "js/picture.js", "js/renderer.js",
                 "css/styles.css"):
        assert (PLUGIN / name).is_file(), name


def test_blackmagics_native_module_is_not_committed():
    """`WorkflowIntegration.node` is a 1.7 MB third-party binary.  The
    installer copies it out of the Resolve installation instead, which
    is what Blackmagic's own README asks for and what keeps an
    unlicensed binary out of this repository (AGENTS.md section 11)."""
    assert not (PLUGIN / "WorkflowIntegration.node").exists()


def test_the_sandboxed_model_is_the_one_used():
    """Resolve 19.0.2 and later enforce sandboxing and context isolation,
    and Blackmagic ships the legacy model only as a migration aid."""
    main = _text("main.js")
    assert "sandbox: true" in main
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main


# ── The three properties the Qt panel was built around ───────────────

# The calls that would move the captain's timeline, write into their
# project, or start work on their machine. Every one must be refused,
# and the refusal is EXERCISED rather than grepped for.
MUST_BE_REFUSED = [
    "OpenPage", "SetCurrentTimeline", "LoadProject", "CreateProject",
    "SaveProject", "AddRenderJob", "StartRendering", "DeleteTimelines",
    "AddMarker", "UpdateMarkerCustomData", "DeleteMarkerAtFrame",
    "SetSetting", "SetCurrentTimecode", "ImportTimelineFromFile",
    "AppendToTimeline", "SetProperty", "SetCDL", "Stabilize",
    # A still grab costs seconds and round-trips the GALLERY, which
    # writes into the captain's project. Phase 2's business, with their
    # knowledge - not a read-only panel's.
    "GrabStill", "ExportStills",
    # And the default is refusal, so a name nobody thought about is out.
    "SomeMethodNobodyHasThoughtOf", "",
]

# What the plugin genuinely needs, and would be broken without.
MUST_BE_PERMITTED = [
    "GetCurrentPage", "GetCurrentProject", "GetCurrentTimeline",
    "GetCurrentTimecode", "GetCurrentVideoItem", "GetItemListInTrack",
    "GetMediaPoolItem", "GetClipProperty", "GetLeftOffset", "GetMarkers",
    "GetStartFrame", "GetSetting",
]


def test_every_resolve_call_goes_through_the_guard():
    """`guard` is the one door, and it is the only place `main.js` may
    invoke a method by name."""
    main = _text("main.js")
    assert "const refused = readOnly.refusal(method);" in main
    assert "if (refused) throw new Error(refused);" in main
    # One dynamic invocation in the file, inside guard.
    assert main.count("obj[method](...args)") == 1
    # The enumeration lives in its own file so it can be RUN, not grepped
    # - a copy of it here would be a second opinion nobody tests.
    assert "READ_ONLY_CALLS = new Set" not in main
    assert "WITHHELD_CALLS = new Set" not in main


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_it_cannot_change_the_captains_session():
    """The read-only contract, EXERCISED - the captain is at the keyboard
    while this runs and Resolve is one shared instance."""
    script = """
const ro = require(%s);
const out = {refused: {}, permitted: {}};
for (const m of %s) out.refused[m] = ro.refusal(m);
for (const m of %s) out.permitted[m] = ro.permits(m);
console.log(JSON.stringify(out));
""" % (json.dumps(str(PLUGIN / "js" / "readonly.js")),
       json.dumps(MUST_BE_REFUSED), json.dumps(MUST_BE_PERMITTED))
    proc = subprocess.run(["node", "-e", script], capture_output=True,
                          encoding="utf-8", check=True)
    answer = json.loads(proc.stdout)

    allowed_anyway = [m for m, why in answer["refused"].items() if not why]
    assert not allowed_anyway, (
        "these would change the captain's session and were PERMITTED: %s"
        % allowed_anyway)

    broken = [m for m, ok in answer["permitted"].items() if not ok]
    assert not broken, (
        "the plugin needs these and they were refused: %s" % broken)


def test_it_holds_no_credential():
    """The panel reaches the model by shelling out to an
    already-authenticated CLI, and that has not moved into the Electron
    app.  There is no network client and no key here."""
    for name in ("main.js", "preload.js", "js/renderer.js", "js/picture.js"):
        source = _text(name)
        for smell in ("api_key", "apiKey", "ANTHROPIC", "Bearer",
                      "Authorization", "https://"):
            assert smell not in source, "%s in %s" % (smell, name)


# The preload is the whole of what the sandboxed page can reach, and it is
# split in two on purpose.  A name that is not in EITHER set is the plugin
# acquiring a capability by the one route the read-only guard does not
# cover, so the test asserts both sets exactly rather than a floor.
PRELOAD_READS = {"context", "videoItems", "apiSurface", "bridge", "stat",
                 "repoRoot", "mediaUrl"}

# The whole of what the page may cause to be WRITTEN, and where it lands.
# `diagnostics` writes the API enumeration and the read timings, which are
# the two things about this surface that can only be established from
# inside the plugin host and are too long to read off a screenshot without
# transcribing them.  It is on this list rather than in PRELOAD_READS
# because it really does write - the distinction is the point.
PRELOAD_WRITES = {"diagnostics": "os.tmpdir()"}


def test_the_page_can_only_ask_for_reads_and_one_declared_write():
    preload = _text("preload.js")
    body = preload.split("exposeInMainWorld('vep', {", 1)[1]
    exposed = set(re.findall(r"^\s{4}(\w+):", body, re.MULTILINE))
    assert exposed == PRELOAD_READS | set(PRELOAD_WRITES)


def test_the_one_write_reaches_neither_resolve_nor_the_project():
    """A write is allowed onto the preload only where it cannot touch the
    two things this plugin exists not to disturb: the captain's Resolve
    session and the captain's project tree.  The diagnostics file goes to
    the system temporary directory, and that is asserted off the writer's
    own source rather than promised in a comment."""
    main = _text("main.js")
    writer = main.split("function writeDiagnostics(", 1)[1].split("\nfunction ", 1)[0]
    # It writes exactly one file, and it composes its path from the system
    # temporary directory - not from REPO_ROOT and not from any path that
    # arrives from the page.
    assert writer.count("writeFileSync") == 1
    assert "os.tmpdir()" in writer
    assert "REPO_ROOT" not in writer.split("const record", 1)[0]
    # The destination is not taken from the caller, so the page cannot
    # aim it at the captain's project.
    assert "payload" not in writer.split("const file =", 1)[1].split("\n", 1)[0]
    for method in READ_ONLY_METHODS_MUST_NOT_APPEAR:
        assert method not in writer, method


# Resolve's own writing vocabulary.  None of it may appear in the one
# function on this plugin that writes.
READ_ONLY_METHODS_MUST_NOT_APPEAR = (
    "AddMarker", "SetSetting", "ImportTimeline", "DeleteTimeline",
    "GrabStill", "AppendToTimeline", "SetProperty",
)


def test_the_installer_stamps_the_checkout_in_and_can_remove_it():
    installer = (REPO_ROOT / "scripts" / "install_workflow_integration.sh"
                 ).read_text(encoding="utf-8")
    assert 'const REPO_ROOT = "";' in installer
    assert 'const REPO_ROOT = "";' in _text("main.js")
    assert "--uninstall" in installer and "--dry-run" in installer


def test_the_qt_panel_is_untouched():
    """The captain uses the Script surface today, and it keeps working
    until its replacement is real."""
    assert (REPO_ROOT / "resolve_scripts" / "VEP Pipeline Panel.py").is_file()
    assert (REPO_ROOT / "resolve_scripts" / "Capture Frame for Firstmate.py"
            ).is_file()


# ── The fixture, and the two implementations that must agree ─────────

def test_the_fixture_is_a_recording_of_a_real_built_timeline():
    """A fixture that made its numbers up would be a gate that cannot
    fail (AGENTS.md section 10.4)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["items"], "no items"
    for item in data["items"]:
        for key in ("name", "start", "end", "left_offset", "track", "file",
                    "source_fps"):
            assert key in item, key
        assert item["end"] > item["start"]
    assert data["context"]["timeline_frame"] is not None


def _js_answers(items, frames, fps):
    """What `js/picture.js` says, run under plain node."""
    script = """
const picture = require(%s);
const input = %s;
const out = [];
for (const frame of input.frames) {
    const p = picture.pictureAt(input.items, frame);
    out.push([frame, p ? p.name + '@V' + p.track : null,
              p ? Number(picture.sourceSeconds(p, frame, input.fps).toFixed(6)) : null,
              picture.overlaysAt(input.items, frame).length]);
}
console.log(JSON.stringify(out));
""" % (json.dumps(str(PLUGIN / "js" / "picture.js")),
       json.dumps({"items": items, "frames": frames, "fps": fps}))
    proc = subprocess.run(["node", "-e", script], capture_output=True,
                          encoding="utf-8", check=True)
    return {row[0]: (row[1], row[2], row[3]) for row in json.loads(proc.stdout)}


def _python_answers(items, frames, fps):
    """What `library/tools/panel/clip_context.py` says, over the same rows."""
    out = {}
    for frame in frames:
        chosen = clip_context.picture_at(items, frame)
        name = (None if chosen is None
                else "%s@V%s" % (chosen["name"], chosen["track"]))
        seconds = None
        if chosen is not None:
            src_fps = chosen.get("source_fps") or fps
            seconds = round(chosen["left_offset"] / src_fps
                            + (frame - chosen["start"]) / fps, 6)
        out[frame] = (name, seconds, len(clip_context.overlays_at(items, frame)))
    return out


def _assert_they_agree(items, frames, fps, what):
    js, py = _js_answers(items, frames, fps), _python_answers(items, frames, fps)
    disagreements = [(f, js[f], py[f]) for f in frames if js[f] != py[f]]
    assert not disagreements, (
        "js/picture.js and clip_context disagree about %s at %d of %d frames; "
        "first three: %s" % (what, len(disagreements), len(frames),
                             disagreements[:3]))


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_they_agree_over_the_recorded_timeline():
    """Every frame of a real built timeline, both ends included, so a
    disagreement at a boundary cannot hide between samples."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items = data["items"]
    frames = list(range(0, max(i["end"] for i in items) + 2))
    _assert_they_agree(items, frames, data["context"]["fps"],
                       "the recorded timeline")


# The recording cannot exercise everything, and saying so is the point.
# On project 001 the V2 cutaways sit in the GAPS between V1 clips and
# never over them, and every clip's source rate equals the timeline's -
# so a recording alone leaves the track preference and the two-frame-rate
# arithmetic untested, which is a gate that cannot fail (AGENTS.md 10.4).
# These rows are CONSTRUCTED, and each one names the rule it exercises.
CONSTRUCTED = {
    "a cutaway placed OVER the A-roll, which is the higher track": [
        {"name": "aroll.MOV", "start": 0, "end": 100, "left_offset": 30,
         "track": 1, "file": "/proj/raw/aroll.MOV", "source_fps": 30.0},
        {"name": "cutaway.MOV", "start": 40, "end": 60, "left_offset": 300,
         "track": 2, "file": "/proj/raw/cutaway.MOV", "source_fps": 30.0},
    ],
    "a caption over both, which is an overlay and never the picture": [
        {"name": "aroll.MOV", "start": 0, "end": 100, "left_offset": 0,
         "track": 1, "file": "/proj/raw/aroll.MOV", "source_fps": 30.0},
        {"name": "cutaway.MOV", "start": 40, "end": 60, "left_offset": 0,
         "track": 2, "file": "/proj/raw/cutaway.MOV", "source_fps": 30.0},
        {"name": "sub_0.mov", "start": 10, "end": 90, "left_offset": 15,
         "track": 3,
         "file": "/proj/pipeline_output/steps/4_05_render_subtitles/sub_0.mov",
         "source_fps": 30.0},
    ],
    "a 60 fps source on a 30 fps timeline, where left_offset is in "
    "SOURCE frames": [
        {"name": "slowmo.MOV", "start": 0, "end": 100, "left_offset": 600,
         "track": 1, "file": "/proj/raw/slowmo.MOV", "source_fps": 60.0},
    ],
    "a frame no clip covers at all": [
        {"name": "aroll.MOV", "start": 50, "end": 60, "left_offset": 0,
         "track": 1, "file": "/proj/raw/aroll.MOV", "source_fps": 30.0},
    ],
}


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
@pytest.mark.parametrize("what", sorted(CONSTRUCTED))
def test_they_agree_on_the_cases_the_recording_cannot_reach(what):
    items = CONSTRUCTED[what]
    frames = list(range(0, max(i["end"] for i in items) + 2))
    _assert_they_agree(items, frames, 30.0, what)


# ── The page really loads, and its loop really runs ──────────────────
#
# The defect this exists for: `js/picture.js` and `js/renderer.js` are
# classic scripts sharing ONE global scope, so a `const isOverlay` in
# both is `Uncaught SyntaxError: Identifier 'isOverlay' has already been
# declared` - the whole page dead at load, with a window that still
# draws its header and tabs.  Nothing else here could see it: both files
# pass `node --check` on their own, and the Python half is untouched.

ELECTRON = Path("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents"
                "/Applications/.hidden/Electron.app/Contents/MacOS/Electron")
NATIVE_MODULE = Path(
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer"
    "/Workflow Integrations/Examples/SamplePlugin/WorkflowIntegration.node")

# The probe is APPENDED to a copy of main.js.  It only adds a timer that
# reports and exits; the thing under test - whether the page's scripts
# survive being loaded together - happens before it runs.
PROBE = """
app.whenReady().then(() => {
    const errors = [];
    const win = () => BrowserWindow.getAllWindows()[0];
    setTimeout(() => {
        const w = win();
        if (!w) { console.log('VEPTEST ' + JSON.stringify({error: 'no window'})); app.exit(0); return; }
        w.webContents.on('console-message', (e, level, message) => {
            if (level >= 2) errors.push(message);
        });
    }, 100);
    setTimeout(async () => {
        try {
            const w = win();
            const seen = await w.webContents.executeJavaScript(
                "({beat: document.getElementById('heartbeat').textContent,"
                + " timeline: document.querySelector('#playhead-table tbody')"
                + "   ? document.querySelector('#playhead-table tbody').textContent : ''})");
            console.log('VEPTEST ' + JSON.stringify({errors, seen}));
        } catch (err) {
            console.log('VEPTEST ' + JSON.stringify({errors, error: String(err)}));
        }
        app.exit(0);
    }, 6000);
});
"""


@pytest.mark.skipif(
    not ELECTRON.is_file() or not NATIVE_MODULE.is_file(),
    reason="DaVinci Resolve Studio's bundled Electron and Workflow "
           "Integration SDK are not installed here")
def test_the_page_loads_and_its_loop_runs(tmp_path):
    """Driven in FIXTURE mode, so no Resolve is opened or needed."""
    staged = tmp_path / PLUGIN_ID
    shutil.copytree(PLUGIN, staged)
    shutil.copy(NATIVE_MODULE, staged / "WorkflowIntegration.node")
    main = staged / "main.js"
    main.write_text(main.read_text(encoding="utf-8") + PROBE, encoding="utf-8")

    env = dict(os.environ, VEP_WFI_FIXTURE=str(FIXTURE))
    proc = subprocess.run([str(ELECTRON), str(staged)], capture_output=True,
                          encoding="utf-8", env=env, timeout=120)
    reports = [line for line in proc.stdout.splitlines()
               if line.startswith("VEPTEST ")]
    assert reports, ("the plugin produced no report; stdout=%r stderr=%r"
                     % (proc.stdout[-2000:], proc.stderr[-2000:]))
    report = json.loads(reports[-1][len("VEPTEST "):])

    assert not report.get("error"), report["error"]
    assert not report["errors"], (
        "the page logged errors at load: %s" % report["errors"])

    beat = report["seen"]["beat"]
    assert "FIXTURE" in beat, (
        "a fixture run must SAY so on screen, so no screenshot of it can be "
        "described as live Resolve; the header read %r" % beat)
    assert "starting" not in beat, (
        "the heartbeat never advanced past its initial text, so the page's "
        "loop never ran: %r" % beat)
    # The recording's own timeline reached the page.
    assert "Pipeline_Edit_2" in report["seen"]["timeline"]
