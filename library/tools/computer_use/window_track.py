"""Window tracking: press the tracker, judge coverage on sampled stills.

Two halves. `run_window_track` presses `Color > Tracker >
Forward/Reverse` (measured live 2026-10-06 - the whole menu tree reads
in background) after the same establishment the create runner uses
(clip under the playhead read back, Color page by its Scopes label).
`verify_window_coverage` judges the sampled stills: the windowed grade
must still be there on every frame, where "there" is measured against
the pipeline's own regrab jitter, never an absolute constant.

The tracker's result reads back nowhere in the 21.1 API, so stills are
the read-back: `marker_capture.grab_still` exports the playhead frame
through the gallery and deletes it again, leaving no trace. The boost
that makes the window visible (a `grade.node_set` on the windowed node)
is the caller's setup, not this module's - the verifier compares
stills, whatever graded them.
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
RECIPE_NAME = "window-track"

#: Direction to the Tracker's menu leaf. Measured, not invented: these
#: are the `Color > Tracker` items, and "backward" is not among them.
TRACK_MENU = {
    "forward": "Forward",
    "reverse": "Reverse",
}

MENU_ROOT = ["Color", "Tracker"]


def build_window_track_script(direction: str) -> Script:
    """The one press that tracks the window, as a `Script`."""
    leaf = TRACK_MENU.get(direction)
    if leaf is None:
        raise ScriptError(
            f"window-track cannot track {direction!r}",
            "the Tracker menu says Forward, Reverse and Stop",
            f"use one of {sorted(TRACK_MENU)}",
            code="WINDOW_TRACK_DIRECTION",
        )
    return Script(
        goal=f"track the window {direction}",
        steps=[
            ScriptStep(
                action="press",
                anchors=[
                    Anchor(via="ax_path",
                           params={"path": [*MENU_ROOT, leaf]}),
                ],
            ),
        ],
    )


def _luma(path: str) -> np.ndarray:
    """The still's luma as float64, 0..255."""
    with Image.open(path) as img:
        arr = np.asarray(img.convert("L"), dtype=np.float64)
    return arr


def verify_window_coverage(
    *,
    baseline: str,
    regrab: str,
    windowed: list,
) -> dict:
    """Judge sampled stills against the windowed first frame.

    `baseline` and `regrab` are two stills of the same ungraded frame:
    their difference IS the pipeline's regrab jitter, and every number
    below derives from it. `windowed[0]` is the first graded frame
    (the reference); the rest are the sampled tracked frames.

    Returns a verdict dict: per-frame bright counts, coverage ratios
    against the reference, the centroid walk (reported, never gated),
    and pass/fail. Fail reasons: the reference shows no window (an
    undrawn overlay is not rendered - nothing to track), or a sampled
    frame lost more than the measured jitter explains.
    """
    if not windowed:
        raise ScriptError(
            "window coverage has no sampled frames",
            "coverage is judged frame by frame",
            "pass at least the reference frame",
            code="WINDOW_COVERAGE_EMPTY",
        )
    base = _luma(baseline)
    re = _luma(regrab)
    if base.shape != re.shape:
        raise ScriptError(
            "window coverage mixes still sizes",
            "a comparison needs one frame geometry",
            "sample every still at the same size",
            code="WINDOW_COVERAGE_GEOMETRY",
        )
    jitter = np.abs(re - base)
    floor = max(1.0, float(np.percentile(jitter, 99.9)))
    jitter_count = int(np.count_nonzero(jitter > floor))
    frames = [_luma(p) for p in windowed]
    for frame in frames:
        if frame.shape != base.shape:
            raise ScriptError(
                "window coverage mixes still sizes",
                "a comparison needs one frame geometry",
                "sample every still at the same size",
                code="WINDOW_COVERAGE_GEOMETRY",
            )
    masks = [np.abs(frame - base) > floor for frame in frames]
    counts = [int(np.count_nonzero(m)) for m in masks]
    reference = counts[0]
    if reference == 0:
        return {
            "passed": False,
            "reason": "reference shows no window",
            "floor": floor,
            "jitter_count": jitter_count,
            "counts": counts,
            "coverage": [],
            "centroid_walk": [],
        }
    coverage = [c / reference for c in counts]
    centroids = []
    for mask in masks:
        ys, xs = np.nonzero(mask)
        centroids.append(
            [float(np.mean(xs)), float(np.mean(ys))])
    walk = [
        float(np.hypot(c[0] - centroids[0][0], c[1] - centroids[0][1]))
        for c in centroids
    ]
    failures = [
        index for index, count in enumerate(counts)
        if count + jitter_count < reference
    ]
    return {
        "passed": not failures,
        "reason": "" if not failures else (
            f"frames {[windowed[i] for i in failures]} lost more than "
            f"the measured jitter ({jitter_count} px) explains"),
        "floor": floor,
        "jitter_count": jitter_count,
        "counts": counts,
        "coverage": coverage,
        "centroid_walk": walk,
    }


def run_window_track(client, resolve, params: dict) -> dict:
    """Track the established clip's window; the receipt, as plain data.

    Establishes like the create runner (qualification project read
    back, clip under the playhead read back, Color page by its Scopes
    label), presses the direction's Tracker item, and returns where the
    tracker was sent. Coverage of the tracked frames is judged by
    `verify_window_coverage`, not here - the menu press has no
    read-back of its own.
    """
    import time as _time

    from library.tools.qualification_project import QUALIFICATION_PROJECT

    direction = params.get("direction")
    script = build_window_track_script(direction)
    unique_id = params.get("unique_id")
    if not unique_id:
        raise ScriptError(
            "window-track needs its clip",
            "the receipt names what was tracked",
            "pass unique_id with the direction",
            code="WINDOW_TRACK_UNADDRESSED",
        )
    manager = resolve.GetProjectManager()
    project = manager.LoadProject(QUALIFICATION_PROJECT)
    if project is None or project.GetName() != QUALIFICATION_PROJECT:
        raise ScriptError(
            f"window-track could not open {QUALIFICATION_PROJECT!r}",
            "the recipe runs on the qualification project only",
            "open Ren Qualification by hand, then retry",
            code="WINDOW_TRACK_NO_PROJECT",
        )
    timeline, item = _find_item(project, unique_id)
    if item is None:
        raise ScriptError(
            f"window-track found no clip {unique_id!r}",
            "the op names a clip the project does not hold",
            "re-plan against the qualification project's clips",
            code="WINDOW_TRACK_NO_CLIP",
        )
    project.SetCurrentTimeline(timeline)
    _establish_playhead(timeline, item, unique_id)
    if params.get("foreground"):
        client.call("activate_resolve", {"allow_foreground": True})
    if not resolve.OpenPage("color"):
        raise ScriptError(
            "window-track could not open the Color page",
            "the Tracker menu lives there",
            "retry; a stuck page switch is a Resolve state to clear by hand",
            code="WINDOW_TRACK_NO_PAGE",
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
            "window-track reads no Color page (no Scopes label)",
            "the API switch alone does not repaint the page in background",
            "re-run with foreground true after announcing focus per the "
            "2026-10-06 protocol",
            code="WINDOW_TRACK_WRONG_PAGE",
        )
    result = run_script(client, script)
    if result.outcome != "done":
        raise ScriptError(
            "window-track pressed nothing",
            f"the menu step ended {result.outcome}",
            "retry; a moved menu is a recipe to re-measure",
            code="WINDOW_TRACK_UNPRESSED",
        )
    return {
        "op": "color.window.track",
        "unique_id": unique_id,
        "direction": direction,
        "menu_item": f"Color > Tracker > {TRACK_MENU[direction]}",
    }


def _find_item(project, unique_id: str):
    """The timeline item carrying unique_id, or (None, None)."""
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            continue
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
            f"window-track could not put the playhead on {unique_id!r}: "
            f"{exc}",
            "the Tracker acts on the clip under the playhead",
            "verify by hand",
            code="WINDOW_TRACK_UNESTABLISHED",
        ) from exc
    if current_uid != unique_id:
        raise ScriptError(
            f"window-track reads current clip {current_uid!r}, not "
            f"{unique_id!r}",
            "the menu would track the wrong clip",
            "verify by hand",
            code="WINDOW_TRACK_NOT_CURRENT",
        )
