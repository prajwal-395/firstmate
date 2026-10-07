"""Window deletion: press the delete, claim it by the node count.

`Color > Nodes > Delete Current Node` removes the current node
(measured live 2026-10-06 on the Color page menu tree). The runner
establishes like the create runner (qualification project read back,
clip under the playhead read back, Color page by its Scopes label),
presses delete, and claims it by the count going down by exactly one -
polled, since creation lands asynchronously and deletion may too.

One honest limitation, stated: the menu deletes whichever node is
current and no API reads which that is. The runner targets the LAST
node and refuses anything else; a create-append selects its new node,
so create-then-delete on a scratch clip is deterministic. Deleting any
other node needs the panel route, which names its target.
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
RECIPE_NAME = "window-delete"

MENU_PATH = ["Color", "Nodes", "Delete Current Node"]


def build_window_delete_script() -> Script:
    """The one press that deletes the current node, as a `Script`."""
    return Script(
        goal="delete the current node",
        steps=[
            ScriptStep(
                action="press",
                anchors=[Anchor(via="ax_path",
                                params={"path": list(MENU_PATH)})],
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


def run_window_delete(client, resolve, params: dict) -> dict:
    """Delete the window params names; the receipt, as plain data."""
    import time as _time

    from library.tools.qualification_project import QUALIFICATION_PROJECT

    unique_id = params.get("unique_id")
    if not unique_id:
        raise ScriptError(
            "window-delete needs its clip",
            "the receipt names what was deleted where",
            "pass unique_id with the node index",
            code="WINDOW_DELETE_UNADDRESSED",
        )
    try:
        node_index = int(params.get("node_index"))
    except (TypeError, ValueError):
        raise ScriptError(
            f"window-delete names no node: {params.get('node_index')!r}",
            "the menu deletes one node and the receipt names it",
            "pass node_index",
            code="WINDOW_DELETE_UNADDRESSED",
        ) from None
    script = build_window_delete_script()
    manager = resolve.GetProjectManager()
    project = manager.LoadProject(QUALIFICATION_PROJECT)
    if project is None or project.GetName() != QUALIFICATION_PROJECT:
        raise ScriptError(
            f"window-delete could not open {QUALIFICATION_PROJECT!r}",
            "the recipe runs on the qualification project only",
            "open Ren Qualification by hand, then retry",
            code="WINDOW_DELETE_NO_PROJECT",
        )
    timeline, item = _find_item(project, unique_id, params.get("timeline"))
    if item is None:
        raise ScriptError(
            f"window-delete found no clip {unique_id!r}" + (
                f" on timeline {params['timeline']!r}"
                if params.get("timeline") else ""),
            "the op names a clip the project does not hold",
            "re-plan against the qualification project's clips",
            code="WINDOW_DELETE_NO_CLIP",
        )
    project.SetCurrentTimeline(timeline)
    _establish_playhead(timeline, item, unique_id)
    if params.get("foreground"):
        client.call("activate_resolve", {"allow_foreground": True})
    if not resolve.OpenPage("color"):
        raise ScriptError(
            "window-delete could not open the Color page",
            "the menu route lives there",
            "retry; a stuck page switch is a Resolve state to clear by hand",
            code="WINDOW_DELETE_NO_PAGE",
        )
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
    if "Scopes" not in labels:
        raise ScriptError(
            "window-delete reads no Color page (no Scopes label)",
            "the API switch alone does not repaint the page in background",
            "re-run with foreground true after announcing focus per the "
            "2026-10-06 protocol",
            code="WINDOW_DELETE_WRONG_PAGE",
        )
    before = _node_count(item)
    if before is None:
        raise ScriptError(
            f"window-delete could not read the node count of {unique_id!r}",
            "an unverified delete would commit a lie",
            "verify by hand",
            code="WINDOW_DELETE_UNREADABLE",
        )
    if before < 2:
        raise ScriptError(
            f"window-delete reads {before} node(s) on {unique_id!r}",
            "deleting the last node is not a window removal anyone "
            "measured - the window node rides above a base grade",
            "create the window first, then delete it",
            code="WINDOW_DELETE_NO_WINDOW",
        )
    if node_index != before:
        raise ScriptError(
            f"window-delete targets node {node_index} but {unique_id!r} "
            f"holds {before}",
            "the menu deletes whichever node is current and no API reads "
            "which that is - only the last node is safe, where a "
            "create-append selected it",
            f"name node {before}, or wait for the panel route",
            code="WINDOW_DELETE_NOT_LAST",
        )
    result = run_script(client, script)
    if result.outcome != "done":
        raise ScriptError(
            "window-delete pressed nothing",
            f"the menu step ended {result.outcome}",
            "retry; a moved menu is a recipe to re-measure",
            code="WINDOW_DELETE_UNPRESSED",
        )
    after = _node_count(item)
    for _poll in range(5):
        if after == before - 1:
            break
        _time.sleep(3)
        after = _node_count(item)
    if after != before - 1:
        raise ScriptError(
            f"window-delete pressed but the nodes read {before} -> {after}",
            "one fewer node claims the delete, anything else is a "
            "refusal with the measured reason",
            "verify by hand",
            code="WINDOW_DELETE_UNVERIFIED",
        )
    return {
        "op": "color.window.delete",
        "unique_id": unique_id,
        "node_index": node_index,
        "menu_item": "Color > Nodes > Delete Current Node",
        "nodes_before": before,
        "nodes_after": after,
    }


def _establish_playhead(timeline, item, unique_id: str) -> None:
    """Put the playhead on the item and read the current clip back."""
    from library.tools.qualification_project import FPS

    try:
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
            f"window-delete could not put the playhead on {unique_id!r}: "
            f"{exc}",
            "the menu acts on the clip under the playhead",
            "verify by hand",
            code="WINDOW_DELETE_UNESTABLISHED",
        ) from exc
    if current_uid != unique_id:
        raise ScriptError(
            f"window-delete reads current clip {current_uid!r}, not "
            f"{unique_id!r}",
            "the menu would delete from the wrong clip",
            "verify by hand",
            code="WINDOW_DELETE_NOT_CURRENT",
        )


def run_recipe_verb(client, args) -> int:
    """The `script --recipe window-delete` verb tail: flag-shaped op in,
    printed receipt out."""
    import json as _json
    import sys as _sys

    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import resolve_lease

    if not args.timeline:
        print("ren: window-delete needs --timeline, the Ren "
              "Qualification timeline by exact name", file=_sys.stderr)
        return 2
    if not args.clip:
        print("ren: window-delete needs --clip, the clip's timeline "
              "unique id", file=_sys.stderr)
        return 2
    if not args.node:
        print("ren: window-delete needs --node, the node index the "
              "window is on", file=_sys.stderr)
        return 2
    for mask_only in ("seed", "mode", "direction", "playhead_timecode",
                      "shape"):
        if getattr(args, mask_only, None) not in (None, "BI", "forward"):
            print(f"ren: --{mask_only.replace('_', '-')} belongs to "
                  f"another recipe, not window-delete", file=_sys.stderr)
            return 2
    op = {
        "unique_id": args.clip,
        "node_index": args.node,
        "timeline": args.timeline,
        "foreground": bool(args.allow_foreground),
    }
    with resolve_lease("computer-use script"):
        resolve = connect_resolve()
        with borrowed(resolve):
            try:
                receipt = run_window_delete(client, resolve, op)
            except ScriptError as exc:
                print(exc.render(), file=_sys.stderr)
                return 1
    print(_json.dumps(receipt, indent=2, sort_keys=True))
    return 0
