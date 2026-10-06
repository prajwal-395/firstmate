# resolve-control

Native macOS driver for DaVinci Resolve. Gives Ren hands and eyes inside Resolve
through the Accessibility API, Quartz CGEvent, window capture (`CGWindowListCreateImage`,
one window by id) and Vision OCR.

JSON-over-stdin request/response protocol. One request per line, one response per line.
Element references survive between calls within a session. All coordinates are in
window-relative points (top-left origin) unless noted; the window frame and backing
scale are reported so callers can convert to screen pixels.

## Why Rust

objc2 bindings cover all four macOS frameworks cleanly (spike 2026-10-05):
- `objc2-accessibility` 0.3 - AXUIElement tree inspection and action invocation
- `objc2-core-graphics` 0.3 - CGEvent keyboard/mouse injection
- `objc2-screen-capture-kit` 0.3 - window capture by window ID
- `objc2-vision` 0.3 - OCR with bounding boxes

All compile with cargo 1.99.0. No Swift runtime dependency, no bridging header,
and the crate joins the existing `native/` Cargo workspace.

## Protocol

### Transport

The binary reads one JSON object per line from stdin and writes one JSON object
per line to stdout. Each request gets exactly one response. Requests are processed
sequentially; the driver is single-threaded.

### Request

```json
{"id": <int>, "method": "<name>", "params": {...}}
```

### Response (success)

```json
{"id": <int>, "ok": true, "result": {...}}
```

### Response (error)

```json
{"id": <int>, "ok": false, "error": {"code": "<CODE>", "message": "<human readable>"}}
```

Error codes:
- `RESOLVE_NOT_RUNNING` - Resolve process not found
- `PERMISSION_DENIED` - Accessibility or Screen Recording permission missing
- `ELEMENT_NOT_FOUND` - find_element matched nothing
- `ELEMENT_STALE` - element reference no longer valid
- `ACTION_NOT_SUPPORTED` - element does not support the requested AX action
- `INVALID_PARAMS` - malformed request parameters
- `CAPTURE_FAILED` - window capture or PNG encoding failed
- `OCR_FAILED` - Vision OCR failed
- `OUTSIDE_RESOLVE` - target is outside Resolve's main window, or an input event
  was asked for while Resolve is not the frontmost app (refused)
- `PROJECT_NOT_QUALIFICATION` - an action was asked for while the open Resolve
  project is not the Ren Qualification project (refused; see Safety)
- `FOREGROUND_NOT_ALLOWED` - a call that takes the captain's screen, cursor or
  keyboard was made without `"allow_foreground": true`
- `SCREEN_LOCKED` - the screen is locked; a capture, OCR, `wait_for` or input
  event is refused, because the window server would answer with a blank image
  and deliver input to the lock screen
- `AMBIGUOUS_COMMAND` - a `run_command` path matched more than one element;
  refine the path until it names one item
- `INTERNAL` - unexpected driver error

### Element references

`find_element` and `get_accessibility_tree` return elements with a `"ref"` field
(an opaque string). Pass that ref as `"element"` in `press`, `set_value`, `focus`
to identify the target. Refs are stable within a session; they are invalidated
when the AX tree changes (window closed, page switched, etc.).

### Session state

The driver maintains state between calls:
- Element reference table
- Last captured window frame and backing scale
- Last accessibility tree (for `wait_for` polling)

There is no explicit session begin/end; the process lifetime is the session.
Killing the binary ends the session.

## Methods

### activate_resolve

Brings Resolve to the foreground. This takes the captain's screen and
keyboard focus, so it is refused with `FOREGROUND_NOT_ALLOWED` unless the
caller passes `"allow_foreground": true`.

Parameters:
- `allow_foreground` (bool, required to be true)

Result: `{"activated": true, "pid": <int>}`

### get_windows

Lists every window Resolve owns, hidden ones included. `is_main` marks the largest, which is the window every other method targets.

Parameters: none

Result:
```json
{
  "windows": [
    {
      "id": <int>,
      "title": "<string>",
      "frame": {"x": <float>, "y": <float>, "width": <float>, "height": <float>},
      "backing_scale": <float>,
      "is_main": <bool>
    }
  ]
}
```

Coordinates are in screen points (global origin, top-left).

### get_accessibility_tree

Dumps the AX tree for a window (or the whole app).

Parameters:
- `max_depth` (int, optional, default 12): maximum tree depth.

Result:
```json
{
  "tree": {
    "ref": "<string>",
    "role": "<AXRole>",
    "title": "<string or null>",
    "description": "<string or null>",
    "value": "<string or number or null>",
    "frame": {"x": <float>, "y": <float>, "width": <float>, "height": <float>} | null,
    "actions": ["<AXActionName>"],
    "enabled": <bool>,
    "focused": <bool>,
    "children": [...]
  }
}
```

Frames are window-relative points (top-left origin).

### find_element

Searches the AX tree for elements matching criteria.

Parameters (all optional, combined with AND):
- `role` (string): AX role (e.g. "AXButton", "AXMenuItem")
- `title` (string): exact title match
- `title_contains` (string): substring match on title
- `label` (string): exact label/description match
- `path` (array of strings, optional): titles of the menu bar item and menu
  items above the element, outermost first, e.g. `["Workspace", "Switch to Page"]`.
  A title alone is ambiguous: Resolve has a "Color" item under Mark > Keyframe
  Timeline Mode, View > Bypass Color and Fusion and Workspace > Switch to Page.
  Press a menu item only once `find_element` returned exactly one match.
- `value` (string): exact value match

Result:
```json
{
  "elements": [
    {
      "ref": "<string>",
      "role": "<AXRole>",
      "title": "<string>",
      "frame": {"x": <float>, "y": <float>, "width": <float>, "height": <float>},
      "actions": ["<AXActionName>"],
      "path": ["<menu title>", ...]
    }
  ]
}
```

### list_commands

Lists every command the Accessibility route can run: each addressable menu
item with its full menu path, outermost menu-bar-item title first, ending at
the item itself. This is the inventory an executor plans deterministic AX
scripts from - a script is a list of these paths, run with `run_command`.

Read-only: it only reads the AX tree, so it is not gated by the project rule.

Parameters:
- `menu` (string, optional): only commands whose first path title equals this
  menu bar item (e.g. "Workspace").

Result:
```json
{
  "count": <int>,
  "commands": [
    {
      "path": ["Workspace", "Switch to Page", "Media"],
      "actions": ["AXCancel", "AXPress", "AXPick"],
      "enabled": true
    }
  ]
}
```

`enabled` is the item's AXEnabled attribute; it is `null` when the attribute
is absent (no evidence either way), never a default. In Resolve the tree is
the macOS menu bar only - the Qt-drawn UI (page buttons, Inspector, node
graph, Fairlight mixer) carries no AX nodes, so it appears here not at all.
What that means, measured, is in `docs/RESOLVE_CONTROL_PROBE.md`.

### run_command

Presses the one menu item named by a full menu path, in a single call. The
path is exactly what `list_commands` returned; it is also the `path` (all but
the last title) plus `title` (the last one) pair `find_element` takes.

This is an action: it runs only while the open project reads back as the Ren
Qualification project, like every other action method.

Parameters:
- `path` (array of strings, required): full menu path, outermost first.

Result: `{"pressed": true, "path": [...]}`

Errors: `ELEMENT_NOT_FOUND` when no item matches the path,
`AMBIGUOUS_COMMAND` when more than one does.

### press

Invokes the default AX action on an element (e.g. AXPress for buttons).

Parameters:
- `element` (string): element ref from find_element

Result: `{"pressed": true}`

### set_value

Sets an element's value (AXValueAttribute).

Parameters:
- `element` (string): element ref
- `value` (string): new value

Result: `{"set": true}`

### focus

Sets keyboard focus to an element (AXFocusedAttribute).

Parameters:
- `element` (string): element ref

Result: `{"focused": true}`

### type_text

Types text into Resolve's focused element as Unicode key events, so the
result does not depend on the keyboard layout. Routed like every input event
(see Input routing).

Parameters:
- `text` (string): text to type
- `allow_foreground` (bool, optional): see Input routing

Result: `{"typed": <int>}` (characters typed)

### key

Sends a keystroke with optional modifiers.

Parameters:
- `key` (string): key name. Special keys: "return", "tab", "escape", "space",
  "delete", "arrow_up", "arrow_down", "arrow_left", "arrow_right",
  "home", "end", "page_up", "page_down", "f1".."f12".
  Lowercase letters, digits and the punctuation keys `` ` - = [ ] \ ; ' , . / ``
  as the character itself, sent as the
  US ANSI virtual key code (what Resolve's shortcuts bind). Any other name
  is refused with `INVALID_PARAMS`.
- `modifiers` (array of strings, optional): "cmd", "shift", "option", "ctrl", "fn"
- `allow_foreground` (bool, optional): see Input routing

Result: `{"sent": true}`

### click

Clicks at a coordinate. Routed like every input event (see Input routing).

Parameters:
- `x` (float): window-relative x in points
- `y` (float): window-relative y in points
- `clicks` (int, optional, default 1): click count (2 = double-click)
- `allow_foreground` (bool, optional): see Input routing

Result: `{"clicked": true}`

### drag

Drags from one coordinate to another.

Parameters:
- `start_x` (float): window-relative start x
- `start_y` (float): window-relative start y
- `end_x` (float): window-relative end x
- `end_y` (float): window-relative end y
- `allow_foreground` (bool, optional): see Input routing

Result: `{"dragged": true}`

### scroll

Scrolls at a coordinate.

Parameters:
- `x` (float): window-relative x
- `y` (float): window-relative y
- `delta_x` (float, optional, default 0): horizontal scroll delta
- `delta_y` (float, optional, default 0): vertical scroll delta
- `allow_foreground` (bool, optional): see Input routing

Result: `{"scrolled": true}`

### capture_window

Captures Resolve's main window alone, by window id, at its backing resolution.

Parameters:
- `source` (string, optional, default "screencapturekit"): "screencapturekit"
  has the window server composite the window alone, so a window covered by
  other apps still comes back without being raised; "window_list"
  (`CGWindowListCreateImage`) returns the window's last backing store.
  `capture_region`, `read_text` and `wait_for`'s `text_visible` take the same
  parameter.

Result:
```json
{
  "image": "<base64-encoded PNG>",
  "width": <int>,
  "height": <int>,
  "scale": <float>
}
```

`scale` is image pixels per window point, measured from the image (2.0 on a
Retina display). The pixels are the window server's copy of the window: while
Resolve is covered by another app it may not repaint, so a capture can show a
frame older than what Resolve holds (measured 2026-10-05: a capture showed the
previous project after the project had changed). Call `activate_resolve` and
let it draw before a capture that must be current.

### capture_region

Captures a region of Resolve's main window, cropped from that window's own
capture, so nothing outside the window can reach the image. A region that does
not lie wholly inside the window is refused with `OUTSIDE_RESOLVE`.

Parameters:
- `x` (float): window-relative x in points
- `y` (float): window-relative y in points
- `width` (float): width in points
- `height` (float): height in points

Result: same as capture_window.

### wait_for

Polls until a condition is met or timeout.

Parameters:
- `predicate` (object): condition to wait for
  - `type`: "element_visible" - wait for an element matching criteria
    - `role`, `title`, `title_contains` (same as find_element)
  - `type`: "text_visible" - wait for OCR text to appear
    - `text` (string): substring to find
    - `region` (object, optional): {"x", "y", "width", "height"} window-relative
  - `type`: "seconds" - wait a fixed duration
    - `duration` (float): seconds to wait
- `timeout` (float, optional, default 10.0): maximum seconds to wait
- `poll_interval` (float, optional, default 0.5): seconds between polls

Result:
```json
{
  "met": <bool>,
  "elapsed": <float>
}
```

### read_text

OCR a region of a window using Vision.

Parameters:
- `region` (object, optional): {"x", "y", "width", "height"} window-relative.
  Omit for full window.

Result:
```json
{
  "text": "<full recognized text>",
  "elements": [
    {
      "text": "<recognized string>",
      "confidence": <float>,
      "frame": {"x": <float>, "y": <float>, "width": <float>, "height": <float>}
    }
  ]
}
```

Frames are window-relative points (top-left origin).

### get_pointer_state

Returns the current mouse position and button state.

Parameters: none

Result:
```json
{
  "x": <float>,
  "y": <float>,
  "button": "<none|left|right|other>"
}
```

Coordinates are screen points (global origin, top-left).

### get_frontmost_app

Returns the bundle identifier of the frontmost application.

Parameters: none

Result:
```json
{
  "bundle_id": "<string>",
  "name": "<string>"
}
```

## Input routing

The captain keeps using his Mac while Ren works, so input never takes his
cursor or keyboard unless the caller says so:

- **Default (background):** `click`, `drag`, `scroll`, `key` and `type_text`
  are delivered to Resolve's process alone with `CGEventPostToPid`, the
  event stamped with the main window's id. The real cursor does not move and
  keyboard focus stays where it is.
- **`"allow_foreground": true`:** the event is posted at the HID tap, as if
  done by hand. It moves the real cursor and reaches whatever is frontmost, so
  it is refused with `OUTSIDE_RESOLVE` unless Resolve is the frontmost app;
  `activate_resolve` (which also needs `allow_foreground`) brings it there.
- **AX methods** (`press`, `set_value`, `focus`, `run_command`) act on Resolve
  while it stays in the background. In Resolve they reach only the menu bar
  (the rest of the UI is Qt-drawn), which is what
  `docs/RESOLVE_CONTROL_PROBE.md` measured. `list_commands` is the read-only
  inventory of that route: every menu path `run_command` can press.

What each path reaches in Resolve, measured, is in
`docs/RESOLVE_CONTROL_PROBE.md` ("Background input").

## Safety

The driver drives the captain's real Resolve on his real screen, so the scoping
is mechanical, checked in `driver.rs` before any method runs
(`action_gate`, `input_gate`, unit-tested there):

- **Actions run only in the Ren Qualification project.** Read-only methods are
  a whitelist (`READ_ONLY_METHODS`); every other method - `set_value`, `focus`,
  `type_text`, `key`, `click`, `drag`, `scroll`, `run_command`, and `press` on
  anything but a menu-bar item - reads the open project's name back at the
  moment of the call and refuses with `PROJECT_NOT_QUALIFICATION` unless it is
  `Ren Qualification`
  (`QUALIFICATION_PROJECT` in `library/tools/resolved/jobs.py`). The read-back
  is the main window's title, which Resolve sets to the project name; a title
  that cannot be read refuses. Pressing a menu-bar item only opens its menu, so
  it stays a read; pressing a menu ITEM runs a command and is an action. A
  caller opens the project inside `library/tools/qualification_project.borrowed()`.
- **Input reaches Resolve only, and leaves the captain's cursor and keyboard
  alone** unless the caller passes `allow_foreground` (Input routing, above).
- **Coordinates stay inside Resolve's main window.** `click`, `drag`, `scroll`
  and `capture_region` refuse points outside it; captures are of that window
  alone, by window id.
- **Missing permissions are reported, never worked around.** Accessibility (all
  AX and input methods) and Screen Recording (captures, OCR, the project
  read-back) each return `PERMISSION_DENIED` naming the permission and the
  binary path to grant it to.

## Building

```sh
scripts/build_resolve_control.sh
```

The binary is `native/target/release/resolve-control` (the one builder;
`cargo build --release -p resolve-control` in `native/` says the same).
Never commit binaries.
