"""Power Window recipes: op params in, API-verified receipt out.

A stored recipe (`RecipeStore`) carries static steps; a window create
cannot be static - the menu item depends on the op's shape and the clip
on its unique_id - so this module builds the `Script` from the op at
run time and verifies against live read-back the static format cannot
name (the node count before and after). The verb dispatches here from
`_run_recipe` beside the mask recipes; the qualifier and tracker
builders will land beside this module when they do.

Measured live 2026-10-06 on 'Ren Qualification' (Color page menu tree):
`Color > Nodes > Add Serial Node + CPW/LPW/PPW/PCW` creates a serial
node carrying a circular/linear/polygon/power-curve window. There is no
gradient item - a gradient create is refused with that reason, and
travels the panel route when it lands. `Delete Current Node` and
`Color > Tracker > Forward/Reverse/Stop` were measured on the same run
and belong to the delete/track builders, not this one.
"""

from __future__ import annotations

from library.tools.computer_use.scripts import (
    Anchor,
    Script,
    ScriptError,
    ScriptStep,
    run_script,
)

#: The recipe's name on the `script --recipe` verb.
RECIPE_NAME = "window-create"

#: Shape to the menu item that creates a node carrying it. Measured, not
#: invented: these four suffixes are the whole `Color > Nodes` list.
SHAPE_MENU = {
    "circle": "Add Serial Node + CPW",
    "linear": "Add Serial Node + LPW",
    "polygon": "Add Serial Node + PPW",
    "powercurve": "Add Serial Node + PCW",
}

MENU_ROOT = ["Color", "Nodes"]


def build_window_create_script(op: dict) -> Script:
    """The one press that creates the op's window, as a `Script`.

    Refused: a missing shape (no creative default - which window
    isolates the subject is the planner's choice), a shape with no menu
    item (gradient travels the panel route), a missing node index or
    clip. Geometry is recorded on the op and placed by the panel route;
    the menu item draws Resolve's default placement, which the receipt
    says plainly.
    """
    shape = op.get("shape")
    if shape not in SHAPE_MENU:
        raise ScriptError(
            f"window-create cannot draw shape {shape!r}",
            "the Color > Nodes menu carries CPW, LPW, PPW and PCW only",
            f"use one of {sorted(SHAPE_MENU)}; gradient travels the "
            f"panel route when it lands",
            code="WINDOW_SHAPE_UNMENUED",
        )
    if op.get("node_index") is None or op.get("unique_id") is None:
        raise ScriptError(
            "window-create needs its clip and node",
            "the receipt names what was created where",
            "pass unique_id and node_index with the shape",
            code="WINDOW_CREATE_UNADDRESSED",
        )
    return Script(
        goal=f"create a {shape} window on node {op['node_index']} "
             f"of {op['unique_id']!r}",
        steps=[
            ScriptStep(
                action="press",
                anchors=[
                    Anchor(via="ax_path",
                           params={"path": [*MENU_ROOT, SHAPE_MENU[shape]]}),
                ],
            ),
        ],
    )


def _node_count(item) -> int | None:
    """The clip's Color-page node count, or None where unreadable."""
    try:
        return int(item.GetNumNodes())
    except Exception:  # noqa: BLE001 - unreadable is a refusal, not a crash
        return None


def _find_item(project, unique_id: str, timeline_name=None):
    """The timeline item carrying unique_id, or (None, None). Scoped
    to the exact timeline name when given - never a prefix."""
    timelines = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            continue
        if timeline_name is not None and timeline.GetName() != timeline_name:
            continue
        timelines.append(timeline)
    for timeline in timelines:
        for track in ("video", "audio"):
            try:
                count = int(timeline.GetTrackCount(track) or 0)
            except Exception:  # noqa: BLE001 - next track
                continue
            for track_index in range(1, count + 1):
                for item in timeline.GetItemListInTrack(track, track_index) or []:
                    try:
                        if item.GetUniqueId() == unique_id:
                            return timeline, item
                    except Exception:  # noqa: BLE001 - next item
                        continue
    return None, None


def run_window_create(client, resolve, params: dict) -> dict:
    """Create the window params names; the receipt, as plain data.

    Establishes (qualification project open and read back by name, its
    timeline current, Color page read back), presses the shape's menu
    item, and claims the window by the node count going up by exactly
    one. Anything else is a refusal with what read back instead.
    """
    from library.tools.qualification_project import QUALIFICATION_PROJECT

    op = dict(params)
    script = build_window_create_script(op)
    manager = resolve.GetProjectManager()
    from library.tools.qualification_project import (
        QualificationError,
        switch_project,
    )
    try:
        project = switch_project(manager, QUALIFICATION_PROJECT)
    except QualificationError as exc:
        raise ScriptError(
            f"window-create could not open {QUALIFICATION_PROJECT!r}: {exc}",
            "the recipe runs on the qualification project only",
            "open Ren Qualification by hand, then retry",
            code="WINDOW_CREATE_NO_PROJECT",
        ) from exc
    timeline, item = _find_item(project, op["unique_id"],
                                  op.get("timeline"))
    if item is None:
        raise ScriptError(
            f"window-create found no clip {op['unique_id']!r}" + (
                f" on timeline {op['timeline']!r}"
                if op.get("timeline") else ""),
            "the op names a clip the project does not hold",
            "re-plan against the qualification project's clips",
            code="WINDOW_CREATE_NO_CLIP",
        )
    from library.tools.resolve_lock import assert_current_timeline

    assert_current_timeline(project, timeline)
    try:
        from library.tools.qualification_project import FPS

        start = int(item.GetStart())
        mid = start + int(item.GetDuration()) // 2
        fps = int(FPS)
        ff = mid % fps
        ss = (mid // fps) % 60
        mm = (mid // (fps * 60)) % 60
        hh = mid // (fps * 3600)
        timeline.SetCurrentTimecode(f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}")
        current = timeline.GetCurrentVideoItem()
        current_uid = current.GetUniqueId() if current else None
    except Exception as exc:  # noqa: BLE001 - unestablished is a refusal
        raise ScriptError(
            f"window-create could not put the playhead on "
            f"{op['unique_id']!r}: {exc}",
            "the Color page grades the clip under the playhead",
            "verify by hand",
            code="WINDOW_CREATE_UNESTABLISHED",
        ) from exc
    if current_uid != op["unique_id"]:
        raise ScriptError(
            f"window-create reads current clip {current_uid!r}, not "
            f"{op['unique_id']!r}",
            "the menu would window the wrong clip (measured: it did)",
            "verify by hand",
            code="WINDOW_CREATE_NOT_CURRENT",
        )
    if params.get("foreground"):
        client.call("activate_resolve", {"allow_foreground": True})
    if not resolve.OpenPage("color"):
        raise ScriptError(
            "window-create could not open the Color page",
            "the menu route lives there",
            "retry; a stuck page switch is a Resolve state to clear by hand",
            code="WINDOW_CREATE_NO_PAGE",
        )
    try:
        import time as _time

        labels: set = set()
        for _attempt in range(3):
            _time.sleep(4)
            try:
                labels = {e.get("text", "") for e in
                          client.call("read_text").get("elements", [])}
            except Exception:  # noqa: BLE001 - retry covers it
                labels = set()
            if "Scopes" in labels:
                break
    except Exception:  # noqa: BLE001 - unreadable labels refuse below
        labels = set()
    if "Scopes" not in labels:
        raise ScriptError(
            "window-create reads no Color page (no Scopes label)",
            "the API switch alone does not repaint the page in "
            "background - the menu would press against the wrong page",
            "re-run with foreground true after announcing focus per the "
            "2026-10-06 protocol",
            code="WINDOW_CREATE_WRONG_PAGE",
        )
    before = _node_count(item)
    if before is None:
        raise ScriptError(
            f"window-create could not read the node count of "
            f"{op['unique_id']!r}",
            "an unverified create would commit a lie",
            "verify by hand",
            code="WINDOW_CREATE_UNREADABLE",
        )
    if int(op["node_index"]) != before + 1:
        raise ScriptError(
            f"window-create targets node {op['node_index']} but "
            f"{op['unique_id']!r} holds {before}",
            "the menu route appends after the current node, so it lands "
            "exactly one past the count - any other index needs the "
            "panel route",
            f"name node {before + 1}, or wait for the panel route",
            code="WINDOW_CREATE_NOT_APPEND",
        )
    result = run_script(client, script)
    if result.outcome != "done":
        raise ScriptError(
            "window-create pressed nothing",
            f"the menu step ended {result.outcome}",
            "retry; a moved menu is a recipe to re-measure",
            code="WINDOW_CREATE_UNPRESSED",
        )
    import time as _time2

    after = _node_count(item)
    for _poll in range(5):
        # Node creation lands asynchronously (measured: the count read
        # back unchanged immediately after the press, and +1 minutes
        # later) - poll before refusing.
        if after == before + 1:
            break
        _time2.sleep(3)
        after = _node_count(item)
    if after != before + 1:
        raise ScriptError(
            f"window-create pressed but the nodes read {before} -> {after}",
            "True plus a gained node claims the window, anything else is "
            "a refusal with the measured reason",
            "verify by hand",
            code="WINDOW_CREATE_UNVERIFIED",
        )
    return {
        "op": "color.window.create",
        "unique_id": op["unique_id"],
        "node_index": int(op["node_index"]),
        "shape": op["shape"],
        "geometry": op.get("geometry"),
        "menu_item": SHAPE_MENU[op["shape"]],
        "nodes_before": before,
        "nodes_after": after,
        "placement": "Resolve default - the geometry rides the panel route",
    }


def run_recipe_verb(client, args) -> int:
    """The `script --recipe window-create` verb tail: flag-shaped op in,
    printed receipt out. Called from `_run_recipe`'s dispatch inside the
    verb's `ui_take` - the runner establishes the rest itself (project,
    clip, page) and refuses each miss by name."""
    import json as _json
    import sys as _sys

    from library.tools.computer_use.take import ui_take

    if not args.timeline:
        print("ren: window-create needs --timeline, the Ren "
              "Qualification timeline by exact name", file=_sys.stderr)
        return 2
    if not args.clip:
        print("ren: window-create needs --clip, the clip's timeline "
              "unique id", file=_sys.stderr)
        return 2
    if not args.node:
        print("ren: window-create needs --node, the node index the "
              "window lands on", file=_sys.stderr)
        return 2
    if args.shape not in SHAPE_MENU:
        print(f"ren: window-create cannot draw shape {args.shape!r}; one "
              f"of {sorted(SHAPE_MENU)}", file=_sys.stderr)
        return 2
    for mask_only in ("seed", "mode", "direction", "playhead_timecode"):
        if getattr(args, mask_only, None) not in (None, "BI", "forward"):
            print(f"ren: --{mask_only.replace('_', '-')} belongs to the "
                  f"mask recipes, not window-create", file=_sys.stderr)
            return 2
    op = {
        "unique_id": args.clip,
        "node_index": args.node,
        "shape": args.shape,
        "timeline": args.timeline,
        "foreground": bool(args.allow_foreground),
    }
    with ui_take("computer-use script recipe window-create") as take:
        resolve = take.resolve
        try:
            receipt = run_window_create(client, resolve, op)
        except ScriptError as exc:
            print(exc.render(), file=_sys.stderr)
            return 1
    print(_json.dumps(receipt, indent=2, sort_keys=True))
    return 0
