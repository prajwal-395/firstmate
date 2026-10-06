# resolve-control measured probe (2026-10-05)

Evidence for the native Resolve UI driver (`native/resolve-control`), measured
against DaVinci Resolve Studio 21 (PID 571) on a 2x Retina display. Raw probe
output is not committed; this file is the summary.

**Correction.** The first read-only probe ran with "Podcast (field test)" open.
That is the captain's own project, not a scratch project, and an earlier
version of this file called it one. Worse, the first action run (click, drag,
scroll, typing) ran in it; a read-only damage check afterwards found no
timeline changed. The driver now refuses every action unless the open project
reads back as "Ren Qualification" (README, Safety), and the action proof below
was redone inside `library/tools/qualification_project.borrowed()`. The AX and
OCR findings in the first two sections came from read-only calls and stand.

## AX tree is the macOS menu bar, not the Resolve UI

`get_accessibility_tree` (max_depth 12) returns 1616 nodes, identical on every
page. Role counts:

| Role | Count |
|---|---|
| AXApplication | 1 |
| AXMenuBar | 1 |
| AXMenuBarItem | 15 |
| AXMenu | 148 |
| AXMenuItem | 1448 |
| AXTextField | 1 |
| AXTable | 1 |
| AXColumn | 1 |

The tree is `AXApplication "DaVinci Resolve"` -> `AXMenuBar` -> 15 menu bar
items (Apple, DaVinci Resolve, File, Edit, Trim, Timeline, Clip, Mark, View,
Playback, Fusion, Color, Fairlight, Workspace, Help) -> menus -> menu items.
Every node carries `role` and most carry `title`; `value`, `description`,
`frame` are sparse (frames are 0x0 for hidden menu items). `actions` are
`AXCancel`/`AXPress`/`AXPick` on menu items.

**The Resolve UI is not in the AX tree.** Zero AXButton, AXWindow (Resolve's
windows), AXGroup, or custom roles. The page buttons, Inspector, Magic Mask
palette, node graph, and Fairlight mixer EQ/Dynamics are Qt-drawn and carry
no AX nodes. Per-page AX dumps are therefore identical - the tree does not
change with the page.

## Reachable by AX vs only by OCR

| Control | AX | OCR |
|---|---|---|
| Menu bar items (File, Edit, Workspace...) | yes (AXPress) | yes |
| Workspace > Switch to Page > [Media..Deliver] | yes (AXPress) | submenu hidden (0x0 frames) |
| Page buttons (Media/Cut/Edit/Fusion/Color/Fairlight/Deliver) | no | no (not OCR-readable) |
| Inspector | no | yes (text labels) |
| Magic Mask palette | no | yes (text labels) |
| Node graph | no | no (drawn) |
| Fairlight mixer EQ/Dynamics | no | partial (labels only) |
| Window title bar | no | yes (the project name) |

Page switching via AX press is possible through Workspace > Switch to Page
(verified: edit -> media, read back via `resolve.GetCurrentPage()`).

## Live proof (2026-10-05, Ren Qualification, inside `borrowed()`)

Run 2026-10-05 21:29 EDT under Ren's exclusive lease, through the production
broker, inside `library/tools/qualification_project.borrowed()`. Resolve stayed
in the background throughout (the captain was in another app); it was never
activated.

**The guard, first, with the captain's project open.** `click`, `key`,
`type_text` and an AX `press` on a menu item each returned
`PROJECT_NOT_QUALIFICATION` ("the open Resolve project is \"Podcast (field
test)\""); `activate_resolve` without `allow_foreground` returned
`FOREGROUND_NOT_ALLOWED`. Nothing reached Resolve.

**Inside the borrow.** `LoadProject("Ren Qualification")`, read back by name
through the scripting API before the first action; the main window title then
read "Ren Qualification".

| Action | Read back |
|---|---|
| AX press Workspace > Switch to Page > Media | `GetCurrentPage()` edit -> media |
| `read_text` (ScreenCaptureKit) | 56 labels; "Master" at x=21.98 y=96.66 w=46.15 h=10.99 pt |
| `read_text` (window list) | the same 56 labels and bounds |
| AX press menu bar Help | menu opened; its search field became reachable by AX |
| AX `set_value` "render" on that field | the field's AXValue reads "render" |

**The captain's project afterwards**, read back after the restore: "Podcast
(field test)", 33 timelines, current timeline "GEO Podcast - Synced" with the
same unique id as before, Edit page (the page was put back explicitly;
`borrowed()` restores project and timeline, not the page).

**Resolve exited** between 21:30:07 (when that read-back succeeded) and
21:30:43 (the next session's first connect), with no crash report and nothing
in the unified log. The cause is unknown; other lanes were also running Resolve
work. The background mouse pass with the window id stamped on each event (see
below) had not started.

## Background input (the captain keeps his keyboard and mouse)

Measured in the run above, Resolve not frontmost (Comet was). For every row the
frontmost app was unchanged and the pointer never jumped to the target; the
pointer did move between rows, which was the captain's own hand.

| Path | Control | Reaches Resolve | Evidence |
|---|---|---|---|
| AX press | Menu bar > Workspace > Switch to Page > Media | yes | page edit -> media; 14% of window pixels changed |
| AX press | Menu bar > Help | yes | menu opened |
| AX set AXValue | Help menu search field | yes | AXValue reads back "render" |
| CGEventPostToPid key | Shortcut shift+4 / shift+6 (page) | no | page unchanged, 0 pixels changed |
| CGEventPostToPid key (type_text) | Help menu search field | no | AXValue not extended |
| CGEventPostToPid click | Qt page button (bottom bar) | no | page unchanged, 0 pixels changed |
| CGEventPostToPid click, drag, scroll | Qt panels (Media page) | no | 0 pixels changed |
| ScreenCaptureKit | Covered main window | yes | current pixels; OCR 56 labels |
| CGWindowListCreateImage | Covered main window | yes here; stale earlier | the same 56 labels here, but earlier the same day it returned the previous project's pixels |

So, in the background, **AX reaches what AX can see (the menu bar) and nothing
reaches the Qt-drawn UI**. The page buttons, panels and shortcuts need either
the menu bar (most commands are in it) or a foreground event the caller must
explicitly allow. Background mouse events stamped with the target window id
(`kCGMouseEventWindowUnderMousePointer`, fields 91/92) were measured later the
same day: Resolve ignored them all (0 pixels changed), as the table above
records.

## The AX route: what it reaches in the background (2026-10-06)

The background path on the Accessibility route is now a first-class part of the
driver: `list_commands` inventories every addressable menu item (read-only) and
`run_command` presses the one item a full menu path names (an action, gated by
the qualification-project rule like every other action). Measured live against
Resolve 21 (PID 86907) on this Mac, no lease and no scripting connection needed
for the inventory: the AX tree is 1635 nodes (1467 `AXMenuItem`, 148 `AXMenu`,
15 `AXMenuBarItem`, 1 `AXTextField`, 1 `AXTable`, 1 `AXColumn`), and
`list_commands` returns **1261 titled commands** across the 15 menus
(Timeline 219, Color 160, Mark 133, View 127, Workspace 97, Playback 88,
Clip 76, Edit 73, Trim 73, File 66, Apple 62, Fairlight 57, Fusion 12,
DaVinci Resolve 9, Help 9), zero disabled.

Per operation, against the captain's plan:

| Resolve operation | AX route reaches it | Evidence |
|---|---|---|
| Switch page (Media/Cut/Edit/Fusion/Color/Fairlight/Deliver) | **yes** | `Workspace > Switch to Page > [Media..Deliver]` in the inventory; the 2026-10-05 run read the page back through the scripting API (edit -> media) |
| Show/hide Inspector | **yes** | `Workspace > Show Panel in Workspace > Inspector` |
| Node graph: add serial/parallel/layer/outside node, delete, prev/next, enable/disable | **yes** | `Color > Nodes > [Add Serial Node, Add Parallel Node, Add Layer Node, Add Outside Node, Add Splitter Combiner Node, Delete Current Node, Previous Node, Next Node, Enable/Disable Selected Nodes, ...]` - 31 node commands. This is the plan's node-add case: the write is the menu command, the verification stays the Graph API |
| Node graph: reorder nodes | **no** | no reorder command in the menu; the 21.1 Graph API exposes no topology change either - a drag, which is a coordinate action |
| Fairlight: bus format/assign, patch I/O, monitoring, automation modes | **yes** | `Fairlight > [Bus Format, Bus Assign, Patch Input/Output, Monitoring, Automation > ...]` |
| Fairlight: track EQ, Dynamics, compressor, limiter, gate, track FX | **no** | zero EQ/Dynamics/Compressor/Limiter commands in the inventory; the mixer is Qt-drawn (probe: OCR reads labels only) |
| Magic Mask palette (Person, track forward/backward) | **no** | zero Magic Mask / Track Forward / Track Backward commands; the palette is Qt-drawn |
| Power Windows, qualifiers | **no** | zero Power Window / Qualifier commands |
| Retime controls | **yes** | `Clip > Clip Speed > Retime Controls` |
| Subtitles | **yes** | `Timeline > AI Tools > Create Subtitles from Audio`, `View > Show Subtitle Regions` |
| Buttons/fields inside the window (page buttons, viewer, node graph canvas, mixer) | **no** | zero AXButton / AXWindow / AXGroup roles in the whole tree; the only non-menu fields are one AXTextField (the Help menu search) and one AXTable |

So the AX route in the background reaches **the menu bar and everything the
menu bar commands** - which includes the two plan cases the Graph API cannot
do (node add/delete, Inspector) - and does not reach the Qt-drawn canvas
controls (mixer EQ/Dynamics, Magic Mask palette, power windows, node reorder).
Those need OCR + coordinates, which need the screen unlocked and the
foreground rule the caller must explicitly allow.

Delivery state of the two new methods:

- `list_commands` - measured live above; read-only, so it runs without a lease
  and without the scripting API.
- `run_command` - live proof 2026-10-06 (broker 90166, restarted on the
  watchdog fix; Resolve PID 86907; inside `borrowed()`, Ren Qualification
  read back by name, exclusive lease): `Workspace > Switch to Page >
  Media` pressed (`pressed: true`), page polled edit -> media;
  `Workspace > Switch to Page > Edit` pressed, page polled media ->
  edit; Resolve alive and the scripting API answering after every event;
  page held stable 10s before the borrow exited. Captain read back after
  the restore: "Podcast (field test)", 33 timelines, same timeline id,
  edit page - identical. Two things the run taught: the page switch lands
  seconds AFTER the press returns, so a caller polls `GetCurrentPage()`
  instead of asserting immediately; and the borrow must not exit while an
  event may still be in flight - the first attempt asserted at once, exited,
  and the late Media press flipped the captain's page to media
  post-restore (33 timelines and the timeline id untouched). Remediated at
  once with `OpenPage("edit")`, read back edit. Refusal direction measured
  the same day: with the captain's project open, `run_command` refuses with
  `PROJECT_NOT_QUALIFICATION` (nothing sent); `list_commands` stays
  read-only (1261 commands, no lease). A locked screen still refuses
  captures/OCR/input with `SCREEN_LOCKED`; the screen was unlocked for
  this run.

## Bugs found and fixed during the probe

1. `window_helper.m` emitted `inf` for non-finite window bounds (the
   "Accessibility" system window), producing invalid JSON; the whole window
   list parse failed and `get_windows` returned []. Fixed by formatting
   non-finite values as 0.
2. `get_window_list` dropped the `kCGWindowOwnerName` field while `get_windows`
   filtered by window *title*; Resolve's main window title is the project
   name, not "Resolve". Fixed by carrying `owner` through and filtering on it.
3. Use-after-free in `children()`: child AXUIElement pointers were borrowed
   from the AXChildren array, then the array was released before the children
   were used. Intermittent SIGSEGV/SIGTRAP. Fixed by retaining each child
   before releasing the array, with `Drop` on `AxElement` for ownership.
4. Use-after-free in `get_attr_actions`: action-name strings were converted
   after the action-names array was released. Fixed by converting before
   releasing.
5. `set_focused` passed `1 as CFTypeRef` (address 0x1) as the boolean value;
   AXUIElementSetAttributeValue crashed treating it as a CFBoolean. Fixed
   with `kCFBooleanTrue`.
6. Captures were raw BGRA bytes labelled PNG (30 MB of base64 per window). Now
   PNG-encoded by ImageIO (350 KB for the 3024x1898 main window).
7. The CGWindow capture options were C bitmasks transmuted into Rust enums
   (undefined behaviour; `read_text` panicked in debug builds). They are u32
   flags now, and the flag named NominalResolution (1<<1) was really
   ShouldBeOpaque.
8. `backing_scale` was always 1.0: `kCGWindowBackingScaleFactor` is not a
   CGWindow info key. Now read off the window's screen (2.0 here), and a
   capture's `scale` is measured as image pixels per window point.
9. OCR frames were image pixels while the protocol promises window points, so
   on this display every OCR bound was 2x off - a click at an OCR'd label
   would have missed. The earlier "Podcast (field test)" title bound
   (x=1327) was in pixels.
10. `capture_region` took raw screen coordinates and could capture any app.
    It is window-relative now, cropped from Resolve's own window capture.
11. `key` and `type_text` posted global CGEvents to whatever app had focus,
    and a click inside Resolve's frame lands on any window covering it. Input
    now goes to Resolve's process by default; a HID-tap event needs
    `allow_foreground` and Resolve frontmost.
12. Screen Recording permission was never checked. It is now, naming the
    binary to grant it to.

## A capture can be older than Resolve

With Resolve behind the terminal, `capture_window` returned the Ren
Qualification project's pixels after `borrowed()` had restored the captain's
project (scripting API and window title both read "Podcast (field test)"). The
window server holds the last frame Resolve drew, and an occluded Resolve does
not repaint. The project guard reads the window TITLE, which was current; a
caller that needs current pixels activates Resolve first.
13. `key` sent characters as their ASCII code (wrong virtual key; "4" sent
    something else) and any unknown name as key code 0, which is "a". It uses
    US ANSI key codes now and refuses unknown names; `type_text` sends Unicode.

## SkyLight background input (2026-10-06) - the canvas-app outcome

Measured live against Resolve 21 (PID 86907) on this Mac, inside
`qualification_project.borrowed()` with Ren Qualification read back by
name, Resolve occluded (frontmost ChatGPT), screen unlocked. Driver:
`Route::SkyLight` (`SLEventPostToPid` via dlopen/dlsym, nothing linked)
plus `skylight_focus` (`SLPSPostEventRecordTo` focus-without-raise, 248-byte
record per cua-driver: length at 0x04, 0x0d at 0x08, focus/defocus marker
at 0x8a, window id LE at 0x3c), both behind explicit per-call opt-in
(`allow_skylight: true` / the `skylight_focus` method). All SkyLight
symbols resolve on macOS 26, including the modern PSN lookup
(`SLSGetWindowOwner` + `SLSGetConnectionPSN`).

| Step | Result |
|---|---|
| `skylight_focus` | ok, 0.03-0.04s; frontmost pid unchanged (551 -> 551), cursor byte-identical |
| SkyLight click, down/up, aim verified on-capture (red-circle crop on the Media page button) | posted, no error, 0.02-0.05s round trip; 0 pixels changed above ambient drift; page still `edit` (scripting ground truth) |
| Same plus a leading `mouseMoved` (the canvas-app recipe) | identical: 0.000 changed fraction on a quiescent run, page still `edit` |
| Cursor / frontmost across all runs | unchanged every read (cursor moved only by the captain's own hand between reads) |
| Restore | page untouched so no restore needed; `borrowed()` exit re-read `Podcast (field test)` open, frontmost ChatGPT |

So Resolve's Qt event loop filters SkyLight-posted background clicks the
way it filters `CGEventPostToPid`: the channel delivers (no error, the
focus dance lands) and Qt drops the event. This is the canvas-app outcome
the background report predicted (Blender/Unity behave the same; Chrome
does not - cua-driver proves SkyLight reaches Chromium). Consequence: the
SkyLight route stays behind its flag for Chrome (PR 6 second half), and
Qt-UI clicks stay on the foreground route (`allow_foreground`, explicitly
allowed) or the AX menu bar. No-input drift (0.000-0.166 across runs) is
the viewer area animating; the button bar never changed in any run.

Drive-by fix in the same edit: `Route::Foreground` posted by calling
`post()` recursively - unbounded recursion that stack-overflowed the
driver on any foreground input. It posts at the HID tap now (pinned by
`input::tests::foreground_resolves_to_the_hid_tap`, no acting involved).

## SkyLight v2 (2026-10-06) - full recipe, still filtered

The captain pointed at trycua/cua (MIT licence, Cua AI 2025 - read as
reference, nothing vendored) and the negative above became a diff task:
close every field gap against cua-driver's proven recipe, re-measure.
cua's backgrounded click (`platform-macos/src/input/mouse.rs`,
`click_at_xy_inner` - the variant that drives native AppKit, no
Chromium primer) versus what we posted:

| Element | Ours (v1) | cua-driver | Ours (v2) |
|---|---|---|---|
| Event source | null | HIDSystemState | HIDSystemState |
| Stamps | f1 only on multi-click | f1/f3/f7/f40/f51/f58/f91/f92 + window location, one group id | all, via `SLEventSetIntegerValueField` + `CGEventSetWindowLocation` (resolved at run time, refused if missing) |
| Sequence | down/up back-to-back | move, 12ms, down, 28ms (tracking-loop poll), up | same |
| Focus settle | none | 50ms after the records | 50ms inside `skylight_focus` |
| Focus restore | none | defocus target + refocus previous key window | `skylight_restore` (driver remembers the dance) |
| Focus record bytes | 0x04/0x08/0x8a/0x3c | identical | unchanged (was already right) |

Re-measured live in Ren Qualification (already open - a sibling lane's
unrestored session, replied not-mine; no switching performed, page
restored afterwards): drift 0.000, stamped click 0.07s round trip,
0.000 changed, page still `edit` by scripting ground truth, cursor and
frontmost (Comet, pid 578) unchanged throughout, restore clean. The aim
was verified dead-center on-capture on the identical layout. The full
AppKit-proven recipe does not move Qt either: Resolve's Qt loop sits in
the canvas bucket with Blender/Unity (per-pid routes filtered no matter
the stamps; only a `cghidEventTap` leading-`mouseMoved` - i.e. briefly
going foreground - reaches them). The one avenue not yet recorded is a
listen-only event tap on Codex's own session, which needs the captain to
cue a Codex background task; cua-driver's source (which Codex's approach
mirrors) served as ground truth instead. Consequence stands: Qt clicks
stay on the foreground route or the AX menu bar; SkyLight stays flagged
for Chrome, where cua proves it works.

## Chrome SkyLight input (2026-10-06) - posted clean, renderer drops it

Driver: `"target": "chrome"` with pid pinning (`owner_pid` off the
window list; several Chromes and no pin is refused), `Route::SkyLight`
with the Chromium recipe (primer, f0 phases, screen-space window
location, 15/1/100/1/0 timings), `click` + `skylight_focus` only -
everything else with a chrome target is refused by name. Plus a real
bug found by the new target: `Frame::contains` compared window-relative
points against the screen-space frame and passed only because Resolve
sits at x=0; `contains_rel` fixes it for click/drag/scroll (pinned by
`containment_is_window_relative`).

Measured live against a throwaway-profile Chrome (backgrounded `open
-g`, about:blank, temp profile, killed afterwards; the captain's Chrome
with its Lavish window was pid-pinned away from every call and verified
intact after): focus-without-raise lands with frontmost/cursor
unchanged; the new-tab (+) click posts clean (0.07-0.19s round trips)
at tap-verified coordinates, yet the tab count stays 1 across four
recipe variants (AppKit stamps, +primer, no-f0, cua-exact). A
listen-only annotated tap (type 2 - the plain session tap never sees
these posts) shows our events entering the stream with the full stamp
set, f40=target pid, f51/f91/f92=window, one shared f58, phases 1/2/3 -
a superset of a genuine HID click captured the same way (which carries
sequential f0, unique f58, no routing fields). So the bytes match or
exceed both references and Chromium 154 still drops them. Open
hypotheses, in order: the renderer filter changed since cua's tested
Chrome 153, or the trust bit includes the poster's code signature
(cua-driver and Codex ship signed; this binary is ad-hoc). Decisive
next step reserved: run cua-driver itself against the same window -
needs an install decision, not taken here.
