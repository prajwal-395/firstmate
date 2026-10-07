"""Qualifier sampling: pick the eyedropper, click the subject color.

A stored recipe carries static steps; a sample cannot be static - the
eyedropper icon moves with the layout and the viewer click depends on
the measured image rect - so this module builds the `Script` from the
op at run time. The eyedropper is a template anchor (the icon is
neither AX-reachable nor OCR-readable); the viewer click is a
position anchor computed from the image rect the runner measures from
a capture. Both clicks need the foreground: Qt filters background
input, and the screen lock refuses captures outright.

Measured live 2026-10-07 on 'Ren Qualification' (Color page captures):
the qualifier eyedropper template matches at 1.0 with the best false
at 0.851, so the 0.9 threshold sits between measurement and
impostor; the image rect is the widest saturated run in the upper
band (letterbox bars are black and read as absent, which is exactly
right - the click must land on picture). A dark or monochrome frame
measures no rect and the runner refuses instead of clicking blind.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from library.tools.computer_use.scripts import (
    Anchor,
    Script,
    ScriptError,
    ScriptStep,
    run_script,
)

#: The recipe's name on the `script --recipe` verb.
RECIPE_NAME = "qualifier-sample"

#: The eyedropper template, cut from measured pixels (Color page).
#: Matched multi-scale - the resolver divides the hit by the capture
#: scale, so one template serves retina and non-retina captures.
def eyedropper_template_path() -> str:
    """The template's absolute path - resolved from the engine root,
    never the cwd, so the verb works from anywhere."""
    from pathlib import Path

    from ren.engine_root import require_engine_root

    return str(Path(require_engine_root()) / "library" / "tools"
               / "computer_use" / "templates" / "qpick.png")


def find_image_rect(capture) -> tuple:
    """The picture rect in capture pixels: left, top, right, bottom.

    The widest run of saturated columns in the upper band. Saturation,
    not brightness: the node graph and panels are bright gray and
    would fool a luma rule (measured), while letterbox bars and the
    dark surround read as absent. Refused when nothing picture-like
    measures - a blind click is worse than none.
    """
    arr = np.asarray(capture.convert("RGB"), dtype=np.float64)
    height, width, _ = arr.shape
    saturation = arr.max(axis=2) - arr.min(axis=2)
    band = saturation[int(height * 0.05):int(height * 0.5), :]
    column_mean = band.mean(axis=0)
    lit = column_mean > 25
    runs = []
    index = 0
    while index < width:
        if lit[index]:
            end = index
            while end < width - 1 and lit[end + 1]:
                end += 1
            runs.append((index, end))
            index = end + 1
        else:
            index += 1
    if not runs:
        raise ScriptError(
            "qualifier-sample found no picture on screen",
            "a dark or monochrome frame measures no saturated run",
            "sample a frame with color in it",
            code="QUALIFIER_NO_PICTURE",
        )
    left, right = max(runs, key=lambda run: run[1] - run[0])
    column = saturation[:, left:right + 1]
    row_lit = column.mean(axis=1) > 25
    row_runs = []
    index = 0
    while index < height:
        if row_lit[index]:
            end = index
            while end < height - 1 and row_lit[end + 1]:
                end += 1
            row_runs.append((index, end))
            index = end + 1
        else:
            index += 1
    # The viewer overlaps the upper band; timeline thumbnails below it
    # do not. A run touching neither is chrome, not picture.
    band_top, band_bottom = int(height * 0.05), int(height * 0.5)
    picture = [run for run in row_runs
               if run[0] <= band_bottom and run[1] >= band_top]
    if not picture:
        raise ScriptError(
            "qualifier-sample found no picture rows",
            "the saturated columns hold no saturated rows",
            "sample a frame with color in it",
            code="QUALIFIER_NO_PICTURE",
        )
    top, bottom = max(picture, key=lambda run: run[1] - run[0])
    return (int(left), int(top), int(right), int(bottom))


def viewer_point(rect, x, y) -> tuple:
    """The window point for normalized picture (x, y).

    The rect already excludes letterbox bars (they read as absent to
    the saturation rule), so the mapping is direct: no source size,
    no aspect arithmetic, nothing to mismatch. A point outside 0..1
    names no picture pixel and is refused - the click would land on
    chrome.
    """
    left, top, right, bottom = rect
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ScriptError(
            f"qualifier-sample cannot map point {(x, y)!r}",
            "a sample point is normalized picture coordinates",
            "pass [x, y] inside 0..1",
            code="QUALIFIER_SAMPLE_UNMAPPABLE",
        )
    return (left + x * (right - left), top + y * (bottom - top))


def build_qualifier_sample_script(template_path, point) -> Script:
    """Two clicks: the eyedropper, then the mapped viewer point."""
    x, y = point
    return Script(
        goal=f"sample the qualifier at viewer ({x:.0f}, {y:.0f})",
        steps=[
            ScriptStep(
                action="click",
                anchors=[Anchor(
                    via="template",
                    params={"image": template_path,
                            "threshold": 0.9})],
            ),
            ScriptStep(
                action="click",
                anchors=[Anchor(
                    via="position",
                    params={"x": x, "y": y,
                            "why": "mapped from the measured image rect; "
                                   "no anchor names a picture pixel"})],
            ),
        ],
    )


def run_qualifier_sample(client, resolve, params: dict) -> dict:
    """Sample the qualifier; the receipt, as plain data.

    Establishes like the window runners, measures the image rect from
    a fresh capture (refusing a rect-less frame), maps the op's
    normalized point, and clicks twice. The sampled range reads back
    nowhere in the API, so the receipt says sampled, never verified.
    """
    import base64
    from io import BytesIO

    from library.tools.qualification_project import QUALIFICATION_PROJECT

    unique_id = params.get("unique_id")
    point = params.get("point")
    if not unique_id or point is None:
        raise ScriptError(
            "qualifier-sample needs its clip and point",
            "the receipt names what was sampled where",
            "pass unique_id and point",
            code="QUALIFIER_SAMPLE_UNADDRESSED",
        )
    try:
        px, py = point
        assert isinstance(px, (int, float)) and isinstance(py, (int, float))
    except (TypeError, ValueError, AssertionError):
        raise ScriptError(
            f"qualifier-sample cannot map point {point!r}",
            "a sample point is two numbers",
            "pass point as [x, y]",
            code="QUALIFIER_SAMPLE_UNMAPPABLE",
        ) from None
    manager = resolve.GetProjectManager()
    from library.tools.qualification_project import (
        QualificationError,
        switch_project,
    )
    try:
        project = switch_project(manager, QUALIFICATION_PROJECT)
    except QualificationError as exc:
        raise ScriptError(
            f"qualifier-sample could not open {QUALIFICATION_PROJECT!r}: {exc}",
            "the recipe runs on the qualification project only",
            "open Ren Qualification by hand, then retry",
            code="QUALIFIER_SAMPLE_NO_PROJECT",
        ) from exc
    from library.tools.computer_use import window_delete

    timeline, item = window_delete._find_item(
        project, unique_id, params.get("timeline"))
    if item is None:
        raise ScriptError(
            f"qualifier-sample found no clip {unique_id!r}",
            "the op names a clip the project does not hold",
            "re-plan against the qualification project's clips",
            code="QUALIFIER_SAMPLE_NO_CLIP",
        )
    from library.tools.resolve_lock import assert_current_timeline

    assert_current_timeline(project, timeline)
    _establish_playhead(timeline, item, unique_id)
    if params.get("foreground"):
        client.call("activate_resolve", {"allow_foreground": True})
    if not resolve.OpenPage("color"):
        raise ScriptError(
            "qualifier-sample could not open the Color page",
            "the qualifier lives there",
            "retry; a stuck page switch is a Resolve state to clear by hand",
            code="QUALIFIER_SAMPLE_NO_PAGE",
        )
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
    if "Scopes" not in labels:
        raise ScriptError(
            "qualifier-sample reads no Color page (no Scopes label)",
            "the API switch alone does not repaint the page in background",
            "re-run with foreground true after announcing focus per the "
            "2026-10-06 protocol",
            code="QUALIFIER_SAMPLE_WRONG_PAGE",
        )
    shot = client.call("capture_window")
    with Image.open(BytesIO(base64.b64decode(shot["image"]))) as img:
        rect = find_image_rect(img)
        scale = float(shot.get("scale") or 1.0)
    vx, vy = viewer_point(rect, px, py)
    script = build_qualifier_sample_script(
        eyedropper_template_path(), (vx / scale, vy / scale))
    result = run_script(client, script,
                        foreground=bool(params.get("foreground")))
    if result.outcome != "done":
        raise ScriptError(
            "qualifier-sample clicked nothing",
            f"the steps ended {result.outcome}",
            "retry; a moved palette is a template to re-cut",
            code="QUALIFIER_SAMPLE_UNCLICKED",
        )
    return {
        "op": "color.qualifier.sample",
        "unique_id": unique_id,
        "point": [px, py],
        "viewer_point": [vx / scale, vy / scale],
        "sampled": True,
        "verified": False,
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
            f"qualifier-sample could not put the playhead on {unique_id!r}: "
            f"{exc}",
            "the eyedropper samples the clip under the playhead",
            "verify by hand",
            code="QUALIFIER_SAMPLE_UNESTABLISHED",
        ) from exc
    if current_uid != unique_id:
        raise ScriptError(
            f"qualifier-sample reads current clip {current_uid!r}, not "
            f"{unique_id!r}",
            "the eyedropper would sample the wrong clip",
            "verify by hand",
            code="QUALIFIER_SAMPLE_NOT_CURRENT",
        )


def run_recipe_verb(client, args) -> int:
    """The `script --recipe qualifier-sample` verb tail."""
    import json as _json
    import sys as _sys

    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import resolve_lease

    if not args.timeline:
        print("ren: qualifier-sample needs --timeline, the Ren "
              "Qualification timeline by exact name", file=_sys.stderr)
        return 2
    if not args.clip:
        print("ren: qualifier-sample needs --clip, the clip's timeline "
              "unique id", file=_sys.stderr)
        return 2
    if not args.point:
        print("ren: qualifier-sample needs --point, the normalized "
              "sample point as JSON [x, y]", file=_sys.stderr)
        return 2
    try:
        point = _json.loads(args.point)
    except ValueError as exc:
        print(f"ren: bad --point JSON: {exc}", file=_sys.stderr)
        return 2
    for other in ("seed", "mode", "direction", "playhead_timecode",
                  "node"):
        if getattr(args, other, None) not in (None, "BI", "forward"):
            print(f"ren: --{other.replace('_', '-')} belongs to another "
                  f"recipe, not qualifier-sample", file=_sys.stderr)
            return 2
    op = {
        "unique_id": args.clip,
        "point": point,
        "timeline": args.timeline,
        "foreground": bool(args.allow_foreground),
    }
    with resolve_lease("computer-use script"):
        resolve = connect_resolve()
        with borrowed(resolve):
            try:
                receipt = run_qualifier_sample(client, resolve, op)
            except ScriptError as exc:
                print(exc.render(), file=_sys.stderr)
                return 1
    print(_json.dumps(receipt, indent=2, sort_keys=True))
    return 0
