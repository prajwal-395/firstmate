"""Window modification: set the Window panel fields by label.

The Window palette's Transform/Softness controls are Qt (no AX), but
every label AND every value is OCR-readable with frames (measured live
2026-10-07 on the Color page: the value sits ~+55x/+5pt from its label
center, consistent across both panel columns). Each set is three
steps: offset-click into the value field (focuses it), type the new
value, Return to commit. The runner re-reads the value by OCR after
every set - a typed value the panel does not show back is refused,
which is what makes the field-behavior assumption (click focuses and
selects all; Return commits) fail closed instead of silent.

Only the last node is addressable (same guard as delete - the panel
edits the current node and no API reads which that is; a create-append
selects its new node). Values format %.2f by the runner; what the
panel shows back is what counts.
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
RECIPE_NAME = "window-modify"

#: Panel parameter to its OCR label. Measured labels only - a control
#: whose label the OCR pass never returned has no entry here.
PANEL_LABELS = {
    "size": "Size",
    "aspect": "Aspect",
    "pan": "Pan",
    "tilt": "Tilt",
    "rotate": "Rotate",
    "opacity": "Opacity",
}

#: Label center to value-field center, window points. Measured on the
#: 1512x949 layout across both panel columns (Size/Pan rows and
#: Aspect/Tilt rows agree within 5pt).
FIELD_DX = 55.0
FIELD_DY = 5.0


def _field_anchor(label: str) -> Anchor:
    """The value field beside a label, as an offset anchor."""
    return Anchor(
        via="offset",
        params={"anchor": {"via": "ocr", "params": {"text": label}},
                "dx": FIELD_DX, "dy": FIELD_DY,
                "why": "value field sits right of its label (measured "
                       "+55x/+5pt)"})


def build_window_modify_script(values: dict) -> Script:
    """Click, type and commit per named panel parameter."""
    unknown = sorted(k for k in values if k not in PANEL_LABELS)
    if unknown:
        raise ScriptError(
            f"window-modify names unmeasured controls {unknown}",
            f"the measured panel labels are {sorted(PANEL_LABELS)}",
            "use a measured control, or measure the new one first",
            code="WINDOW_MODIFY_UNMEASURED",
        )
    steps = []
    for key, value in values.items():
        if not isinstance(value, str):
            raise ScriptError(
                f"window-modify value for {key!r} is not a string",
                "the runner formats numbers - the script carries text",
                "format at the recipe layer",
                code="WINDOW_MODIFY_UNFORMATTED",
            )
        label = PANEL_LABELS[key]
        steps.append(ScriptStep(
            action="click",
            anchors=[Anchor(via="ocr", params={"text": label}),
                     _field_anchor(label)],
        ))
        steps.append(ScriptStep(
            action="type",
            anchors=[Anchor(via="type", params={"text": value})],
        ))
        steps.append(ScriptStep(
            action="key",
            anchors=[Anchor(via="key", params={"key": "return"})],
        ))
    return Script(goal="set the window panel fields", steps=steps)


def _read_panel_values(client, labels: list) -> dict:
    """The values the panel shows beside each label, by fresh OCR."""
    elements = client.call("read_text").get("elements", [])
    found = {}
    for label in labels:
        label_el = next(
            (e for e in elements if e.get("text") == label), None)
        if label_el is None:
            continue
        frame = label_el.get("frame") or {}
        cx = frame.get("x", 0.0) + FIELD_DX
        cy = frame.get("y", 0.0) + FIELD_DY
        best, best_dist = None, None
        for e in elements:
            text = e.get("text", "")
            if not text or text == label:
                continue
            f = e.get("frame") or {}
            ex = f.get("x", 0.0) + f.get("width", 0.0) / 2
            ey = f.get("y", 0.0) + f.get("height", 0.0) / 2
            dist = abs(ex - cx) + abs(ey - cy)
            if best is None or dist < best_dist:
                best, best_dist = text, dist
        if best is not None and best_dist < 40.0:
            found[label] = best
    return found


def run_window_modify(client, resolve, params: dict) -> dict:
    """Set the named panel fields; the receipt, as plain data."""
    import time as _time

    from library.tools.qualification_project import QUALIFICATION_PROJECT

    unique_id = params.get("unique_id")
    values = params.get("values") or {}
    if not unique_id or not values:
        raise ScriptError(
            "window-modify needs its clip and values",
            "the receipt names what was set where",
            "pass unique_id and values",
            code="WINDOW_MODIFY_UNADDRESSED",
        )
    script = build_window_modify_script(values)
    labels = [PANEL_LABELS[k] for k in values]
    manager = resolve.GetProjectManager()
    from library.tools.qualification_project import (
        QualificationError,
        switch_project,
    )
    try:
        project = switch_project(manager, QUALIFICATION_PROJECT)
    except QualificationError as exc:
        raise ScriptError(
            f"window-modify could not open {QUALIFICATION_PROJECT!r}: {exc}",
            "the recipe runs on the qualification project only",
            "open Ren Qualification by hand, then retry",
            code="WINDOW_MODIFY_NO_PROJECT",
        ) from exc
    from library.tools.computer_use import window_delete

    timeline, item = window_delete._find_item(
        project, unique_id, params.get("timeline"))
    if item is None:
        raise ScriptError(
            f"window-modify found no clip {unique_id!r}",
            "the op names a clip the project does not hold",
            "re-plan against the qualification project's clips",
            code="WINDOW_MODIFY_NO_CLIP",
        )
    from library.tools.resolve_lock import assert_current_timeline

    assert_current_timeline(project, timeline)
    _establish_playhead(timeline, item, unique_id)
    if params.get("foreground"):
        client.call("activate_resolve", {"allow_foreground": True})
    if not resolve.OpenPage("color"):
        raise ScriptError(
            "window-modify could not open the Color page",
            "the panel lives there",
            "retry; a stuck page switch is a Resolve state to clear by hand",
            code="WINDOW_MODIFY_NO_PAGE",
        )
    _assert_color_page(client)
    before = _read_panel_values(client, labels)
    result = run_script(client, script,
                        foreground=bool(params.get("foreground")))
    if result.outcome != "done":
        raise ScriptError(
            "window-modify ran nothing to completion",
            f"the steps ended {result.outcome}",
            "retry; a moved panel is anchors to re-measure",
            code="WINDOW_MODIFY_UNRUN",
        )
    _time.sleep(2)
    after = _read_panel_values(client, labels)
    mismatched = {PANEL_LABELS[k]: (values[k], after.get(PANEL_LABELS[k]))
                  for k in values
                  if after.get(PANEL_LABELS[k]) != values[k]}
    if mismatched:
        raise ScriptError(
            f"window-modify read back {mismatched}",
            "a typed value the panel does not show back is not set",
            "verify by hand",
            code="WINDOW_MODIFY_UNVERIFIED",
        )
    return {
        "op": "color.window.modify",
        "unique_id": unique_id,
        "values": dict(values),
        "read_before": before,
        "read_after": after,
    }


def _assert_color_page(client) -> None:
    """Refuse unless the Scopes label reads - the API switch alone
    does not repaint the page in background."""
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
            return
    raise ScriptError(
        "window-modify reads no Color page (no Scopes label)",
        "the API switch alone does not repaint the page in background",
        "re-run with foreground true after announcing focus per the "
        "2026-10-06 protocol",
        code="WINDOW_MODIFY_WRONG_PAGE",
    )


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
            f"window-modify could not put the playhead on {unique_id!r}: "
            f"{exc}",
            "the panel edits the clip under the playhead",
            "verify by hand",
            code="WINDOW_MODIFY_UNESTABLISHED",
        ) from exc
    if current_uid != unique_id:
        raise ScriptError(
            f"window-modify reads current clip {current_uid!r}, not "
            f"{unique_id!r}",
            "the panel would edit the wrong clip",
            "verify by hand",
            code="WINDOW_MODIFY_NOT_CURRENT",
        )


def run_recipe_verb(client, args) -> int:
    """The `script --recipe window-modify` verb tail."""
    import json as _json
    import sys as _sys

    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import resolve_lease

    if not args.timeline:
        print("ren: window-modify needs --timeline, the Ren "
              "Qualification timeline by exact name", file=_sys.stderr)
        return 2
    if not args.clip:
        print("ren: window-modify needs --clip, the clip's timeline "
              "unique id", file=_sys.stderr)
        return 2
    if not args.values:
        print("ren: window-modify needs --values, the panel values as "
              "JSON {size, pan, ...}", file=_sys.stderr)
        return 2
    for other in ("seed", "mode", "direction", "playhead_timecode",
                  "node", "shape", "point"):
        if getattr(args, other, None) not in (None, "BI", "forward"):
            print(f"ren: --{other.replace('_', '-')} belongs to another "
                  f"recipe, not window-modify", file=_sys.stderr)
            return 2
    try:
        values = _json.loads(args.values)
    except ValueError as exc:
        print(f"ren: bad --values JSON: {exc}", file=_sys.stderr)
        return 2
    if not isinstance(values, dict):
        print("ren: --values is a JSON object", file=_sys.stderr)
        return 2
    formatted = {}
    for key, value in values.items():
        try:
            formatted[key] = f"{float(value):.2f}"
        except (TypeError, ValueError):
            print(f"ren: --values[{key}] is not a number",
                  file=_sys.stderr)
            return 2
    op = {
        "unique_id": args.clip,
        "values": formatted,
        "timeline": args.timeline,
        "foreground": bool(args.allow_foreground),
    }
    with resolve_lease("computer-use script"):
        resolve = connect_resolve()
        with borrowed(resolve):
            try:
                receipt = run_window_modify(client, resolve, op)
            except ScriptError as exc:
                print(exc.render(), file=_sys.stderr)
                return 1
    print(_json.dumps(receipt, indent=2, sort_keys=True))
    return 0
