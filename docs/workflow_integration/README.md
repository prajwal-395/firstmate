# The Workflow Integration, seen

Pictures of `com.videoeditingpilot.vep`, in two sets. The first three are
**fixture** runs taken from the plugin's own `webContents.capturePage()`; the
last three are **live** runs taken with `screencapture -l<windowid>`, which
photographs the one window rather than the screen, so nothing behind it and
nothing of the captain's desktop is in the frame.

**Every one was opened and read before it was committed**, which is the check
`docs/panel/README.md` records - nothing else tells a picture of the plugin
from a picture of something else.

All three are **fixture runs**: `VEP_WFI_FIXTURE` drives the page off a
recording of project 001's built timeline with no DaVinci Resolve running.
That is why the header is orange and says `FIXTURE (no Resolve)`. A picture of
this page must never be mistakable for a picture of live Resolve, and the
header is how it says so.

| | What it shows |
|---|---|
| `1_playhead.png` | The playhead, and the clip under it. Note the two rows that matter: Resolve calls `sub_block_3.mov` current, and the plugin says it is **an overlay, so not the picture** - the picture is `IMG_1822.MOV` on V1, 34.900 s into its source. |
| `2_video_source.png` | The source clip under the playhead, playing. `IMG_1822.MOV`, decoded at 1920x1080, seeked to 34.900 s and running on past it. Ungraded, no comps, no captions, no mix. |
| `3_video_master.png` | The rendered master, seeked to timeline time. `Pipeline_Edit_2.mp4` at 1080x1920 with the pipeline's own burned-in captions, **drift 0.000 s**. The black above and below the picture is the master's own letterbox - 001 declares `framing_intent: 0.0` - and not the player. |

## Live, after the restart

The captain restarted Resolve on 2026-08-31, which is what let the plugin be
launched from the menu for the first time. These three are **live** - the
header is white and says `live Resolve`, against the fixture runs' orange
`FIXTURE (no Resolve)`.

| | What it shows |
|---|---|
| `4_live_playhead.png` | The plugin reading the captain's own running Resolve: project and timeline `Pipeline_Edit_2`, timecode `00:00:53:03`, frame 1593, 2 markers. The clip under the playhead is `IMG_1813.MOV` on **V2** - a cutaway - which is the `picture_at` rule working on real data, not a fixture arranged to show it. |
| `5_live_api_surface.png` | The JavaScript API enumerated off the live objects. `WorkflowIntegration` reports version 2.0.0. The full list is longer than the window, which is why `live_api_surface.json` is beside it. |
| `6_live_video_source.png` | Video playing, live, from the real file on disk: `IMG_1813.MOV`, seeked to the playhead - `playhead 00:00:53:03 -> source 0.500 s | player at 0.500 s | drift 0.000 s`. |

`live_api_surface.json` is the plugin's own diagnostics file, written by the
button on the API surface tab. It carries the whole enumeration as TEXT -
238 method names across four objects - the in-plugin read timings, and the
`argv` Resolve launched the plugin with, which is what proves Resolve started
it rather than the plugin self-launching:

    "/Applications/DaVinci Resolve/.../Electron",
    ".../Workflow Integration Plugins/com.videoeditingpilot.vep/main.js",
    "--plugin-id=com.videoeditingpilot.vep"

## What is still not here, and why

**A picture of the menu itself, open.** The registration is not in doubt -
Resolve's own menu bar enumerates `Workspace > Workflow Integrations >
VEP Pipeline`, and clicking that item is what produced the processes and the
pictures above. But photographing the open menu needs Resolve foregrounded,
and Resolve sits on a macOS Space that is not the active one; programmatic
activation reports success without switching Spaces, so the only way to get
the picture is to take over the captain's screen. That is not worth a
screenshot, so it was not done.
