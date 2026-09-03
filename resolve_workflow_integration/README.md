# The Workflow Integration

`com.videoeditingpilot.vep` is a DaVinci Resolve **Workflow Integration** -
an Electron app that Resolve loads from **Workspace > Workflow
Integrations**, driven through Resolve's *JavaScript* API.

It is a **phase 1** plugin: it exists to settle whether this surface can
give the captain what the Script-surface panel cannot, and it deliberately
does not yet port the panel's Trace, Gates or Run views. What it does
today is read the playhead, play video, and reach this repository's
Python.

`resolve_scripts/VEP Pipeline Panel.py` - the Qt panel on the Script
surface - is untouched and keeps working. Nothing here replaces it yet.

## Install and remove

    scripts/install_workflow_integration.sh              # install
    scripts/install_workflow_integration.sh --dry-run    # say what it would do
    scripts/install_workflow_integration.sh --uninstall  # take it out again

It installs to a **machine-level** directory, which is world-writable on
macOS so no admin rights are needed:

    /Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.videoeditingpilot.vep

**Resolve scans that directory ON STARTUP ONLY.** A newly installed
plugin appears in the menu the next time Resolve is launched, and the
installer says so rather than restarting anything. That is measured, not
assumed - see "What was settled" below.

Two things the installer deliberately does not do:

- **It does not ship Blackmagic's `WorkflowIntegration.node`.** That
  1.7 MB native module is copied out of the Resolve installation on the
  machine, which is what Blackmagic's own README asks for and what keeps
  a third-party binary out of this repository (AGENTS.md section 11).
- **It does not restart Resolve.** Resolve is the captain's application.

## What it does

| Tab | What it shows |
|---|---|
| Playhead | Timeline, timecode, playhead frame, the clip under it, the overlays over it, and what each read cost |
| Video | The source clip under the playhead, or the rendered master, seeked to the playhead and optionally following it |
| Bridge | One round trip to this repository's Python, with its cost |
| API surface | What the JavaScript API really exposes, enumerated off the live objects; what the plugin's own reads cost; and a button that writes both to a file |

## The three properties, under this surface

The Qt panel was built around three measured properties (AGENTS.md
section 15). A new surface does not get to drop them silently.

**It cannot freeze Resolve.** Every Resolve call is asynchronous over
Electron's IPC, so nothing in the page can block waiting on Resolve, and
the per-tick read is cheap by construction: the expensive item-list read
is cached and refreshed every 8 seconds, while what changes twice a
second is only the frame. Measured with an independent observer process
timing Resolve on the wall clock, with the plugin really open and
polling: 1110 probes at p50 0.514 ms / p99 2.220 ms with 0 errors,
against an idle baseline of p50 0.491 ms / p99 4.289 ms. The numbers, and
the reads measured from inside the plugin itself, are in "After the
restart" below.

**It holds no credential.** There is no network client anywhere in the
plugin, and `tests/test_workflow_integration_plugin.py` fails if one
appears. The model is reached the way the panel reaches it - by shelling
out to an already-authenticated CLI - and that has not moved into
Electron.

**Its logic is testable without Resolve.** Two halves:

- *The judgements never left Python.* `library/tools/workflow_bridge.py`
  is the whole route in, and the catalog join it serves is
  `library/tools/panel/clip_context.py` unchanged - the same module the
  Qt panel uses, with the same tests, which never open the application.
  The JavaScript reads Resolve and draws; it decides nothing.
- *The JavaScript that remains is run in tests.* `js/picture.js` holds the
  plugin's only arithmetic and is loaded by the page, by the Electron
  main process and by a plain `node` in
  `tests/test_workflow_integration_plugin.py`, which runs it against
  `clip_context` over a recorded timeline **and** over constructed cases
  the recording cannot reach, and fails if the two ever disagree.

## Fixture mode: the plugin with no Resolve at all

    VEP_WFI_FIXTURE=tests/fixtures/workflow_integration/built_timeline.json \
      "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/.hidden/Electron.app/Contents/MacOS/Electron" \
      "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.videoeditingpilot.vep"

The three Resolve readers then answer from a **recording** of project
001's built timeline - its V1 clips, its V2 cutaways and the subtitle
cards above them - so the page, the playhead arithmetic and the video
player can be driven and seen with nothing running. A fixture run says
so in the header, in orange, on every frame: a picture of this page must
never be mistakable for a picture of live Resolve.

Note the Electron binary: Resolve ships its own (36.3.2) and the plugin
brings none, which is why there is no `node_modules/` here.

## What was settled, and what was not

**Settled by measurement:**

- Resolve Studio 21.0.0b.28 exposes the surface, and the plugin root is
  world-writable and had to be created.
- Blackmagic's README says a plugin "shows the plugin HTML page in a
  **separate window**". The plugin does not dock.
- **Video plays.** An HTML5 `<video>` over a custom `vepmedia://` scheme
  that answers Range requests - which plain `file://` does not, and
  without Range a player can start but cannot seek. Measured on the real
  files: `IMG_1822.MOV` decoded at 1920x1080, and the rendered master
  `Pipeline_Edit_2.mp4` at 1080x1920, seeked to the playhead with 0.000 s
  drift.
- **Playback follows the playhead by SEEKING, not by streaming.** The
  page polls the playhead and re-seeks past `RESEEK_SECONDS` of drift.
- **The bridge costs 32-45 ms of process spawn**, plus the work: a
  `ping` round trip measured min 32 / median 41 / max 82 ms from the page,
  and the full catalog join 86-117 ms of which 29-53 ms is Python loading
  a 4.85 MB state file. That is why the design polls Resolve for context
  and calls Python only on demand. It needs no ML stack - the join runs
  on stock system Python 3.9.

**Not reachable, and stated rather than worked around:**

- **A live mirror of Resolve's own viewer.** There is **no stream** in
  the API. What plays here is a file on disk: the source clip (ungraded,
  no comps, no captions, no mix) or the rendered master (complete, and
  only as current as the last render). Neither is Resolve's program
  output.

  **Single frames ARE reachable, and that is not the same thing.**
  `Timeline.GrabStill()` returns the graded, conformed timeline frame -
  measured at 0.36-0.38/255 against a Deliver render of the same
  timecode (`marker_capture.py`) - and `GetCurrentClipThumbnailImage()`
  returns base64 RGB on the Color page. Neither is playback: a grab
  costs **seconds** per frame and round-trips the gallery, which writes
  into the captain's project. Neither is in `READ_ONLY_CALLS`, and a
  sequence of stills must never be described as playback.

**The one thing the plugin cannot do without Resolve being restarted:**

- `WorkflowIntegration.Initialize()` fails - `Failed to open IPC` - for
  **every** plugin id when Resolve did not launch the plugin itself,
  including Blackmagic's own `com.blackmagicdesign.resolve.sampleplugin`.
  So self-launching the Electron app gives the whole UI and no Resolve
  data. That is what fixture mode is for, and it is why the live half of
  this plugin is verified only after a Resolve restart.

## After the restart: what the live run settled

The captain restarted Resolve on 2026-08-31. Four things could only be
established with the plugin running inside it, and all four now are.
The pictures are in [`docs/workflow_integration/`](../docs/workflow_integration/README.md).

**It loads, and Resolve launches it.** Resolve's own menu bar enumerates
`Workspace > Workflow Integrations > VEP Pipeline`, and clicking that item
started the plugin. The proof that RESOLVE started it, rather than the app
self-launching, is the `argv` the process was given -
`--plugin-id=com.videoeditingpilot.vep`, under Resolve's own bundled
Electron - recorded in `docs/workflow_integration/live_api_surface.json`.
The renderer runs `--enable-sandbox`, so the sandboxed model is in force.

**It does NOT dock. It is a separate window, and that is structural.**
Blackmagic's README was right and the docking claim is wrong for this
mechanism. The window `VEP Pipeline` is owned by its own `Electron`
process, a DIFFERENT pid from Resolve's, and carries the exact geometry
`createWindow()` asks for. A window belonging to another process cannot be
docked into Resolve's own Qt workspace, so this is not a setting anybody
can change - it is what a Workflow Integration is. Settled by process
ownership rather than by eye, because two windows side by side look the
same whoever owns them.

**The JavaScript API is at parity with the Python one for everything this
project needs.** Enumerated off the live objects: 29 methods on `resolve`,
50 on `Project`, 63 on `Timeline`, 96 on `TimelineItem` - 238 in all, the
whole list in `live_api_surface.json`. Two readings matter:

- **The marker vocabulary is all there** - `GetMarkers`,
  `GetMarkerCustomData`, `UpdateMarkerCustomData`, `AddMarker`,
  `DeleteMarkerByCustomData` - so folding the capture button in (phase 2)
  is reachable from JavaScript and does not need the Python bridge.
- **Nothing in the 238 is a stream.** There is no transport control and no
  viewer mirror anywhere on any object; `Project.GetPlaybackSpeed` reads a
  speed and there is no `Play`. The "no stream in the API" claim above was
  an absence of documentation before; it is now an absence in the
  enumeration, which is a stronger statement.

**Resolve stays responsive, measured both ways.** An independent observer
process timing Resolve on the wall clock, 60 s each phase, one cheap
read-only call in a loop:

| phase | probes | p50 | p95 | p99 | max | errors |
|---|---|---|---|---|---|---|
| idle, plugin not running | 1110 | 0.491 ms | 1.482 ms | 4.289 ms | 32.461 ms | 0 |
| working, plugin open and polling | 1110 | 0.514 ms | 1.237 ms | 2.220 ms | 9.944 ms | 0 |

The plugin costs Resolve 0.023 ms at the median and its p99 and max are
LOWER than the idle baseline's, which is noise rather than an improvement.
From INSIDE the plugin, over its own reads and with no extra call made in
order to measure them: context read p50 12 ms / p99 67 ms over 488 reads,
item list read p50 42 ms / p99 89 ms over 36 reads, 0 errors. That gap -
12 ms for the frame against 42 ms for the item list - is the whole reason
the loop polls the playhead at 500 ms and re-reads the item list only
every 8 s.

## An open defect, found by the live run

**On the RENDERED MASTER only, the status line disagrees with the player.**
The picture and the player's own control both show the seek landed - the
control reads `0:53 / 0:56` and the frame drawn is the one at 53 s - while
the line under it reads `player at 0.000 s | drift -53.100 s`. It
reproduced across captures 8 s apart with the loop demonstrably live
(the beat counter advancing), so it is not one tick of staleness.

The SOURCE path is correct in the same run: `player at 0.500 s |
drift 0.000 s`. The `vepmedia://` Range handler was read and answers 206
with `Content-Range`, `Content-Length` and `Accept-Ranges`, so the obvious
cause is ruled out and the real one is NOT established. It is recorded
here undiagnosed rather than guessed at, because a diagnosis nobody
verified is worth less than a stated unknown. It is a defect in what the
page REPORTS about the master, not in whether the master plays.
