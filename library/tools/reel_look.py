"""The declared look on the REELS path: frame, punch-in, power, drift.

`library/tools/tv_frame.py` declares the punched-in TV-frame look and
`library/tools/tv_power.py` the switch-on and switch-off timings.  Both
landed on the MASTER path only: `step_5_04_compile_manifest` reads them
into an assembly manifest and `resolve_build_timeline` places it.  The
reels path is a different placer - `reel_build.build_reel_timeline`
drives Resolve directly, writes no manifest, and applied no Fusion comp
at all - so a project that declared the look got it on its master video
and not on one reel cut out of that video.

Measured 2026-09-09 on the fifteen reels the captain approved before the
field-test project was reset (`approved-reels-before-reset.json`): every
picture item on every one of them carries `zoom: 1.0`, on V1 and V2
alike.  That is the whole of "otherwise its just a still shot" - the
reels placer had no route to a zoom, animated or static, and no route to
a bezel.

This module is that route, and it adds no look of its own:

- WHAT the look is comes from `tv_frame.resolve_tv_frame` - the asset,
  the punch-in factor, the power timings - which reads the project's own
  declaration first and its brand template second.  A project that
  declares nothing gets `None` here and the reel it got before.
- WHICH shots drift, HOW FAR and WHY is a model's answer, resolved by
  step 4.03's own `post_bridge.resolve_vfx`. The captain's body-cadence
  direction is in `MOTION_HANDOFF`; the planner can skip a shot or span
  that conflicts with its picture, words, or existing treatment. Each
  move still needs its own rationale (`no_stated_reason`) and direction
  (`ken_burns_without_direction`). No movement is injected downstream.
- HOW a comp reaches the picture is
  `library/tools/execution/apply_fusion_comps.py`, unchanged, driven
  with a manifest built from the reel's own placements.  Process
  isolation (AGENTS.md 5) is why it runs as a subprocess: the timeline
  was created in the calling process.
- The GRADE rides two halves, the way step 6.01 delivers it on the
  master: `resolve_grade_cdl` renders the project's own
  `style.series_look` to the SetCDL values and `apply_cdl` lands them
  on every footage picture item (Color page node 1, CDL-first per the
  v04 still recipe), while `resolve_grade_look` renders the Fusion
  half (pivot contrast, glow, grain, vignette - the nodes no
  scriptable Color page call can reach) and `fusion_manifest` merges
  it onto every picture clip, so both halves travel with the switch
  animation and the drift rather than replacing them.

Under the look each speaker keeps their own picture row
------------------------------------------------------
A reel's picture inherits the MASTER's track index, so a two-camera
master puts one speaker on V1 and the other on V2 - sequentially, never
at once - and it stays there under the look (captain's ruling on Reel
09, 2026-09-09: two picture rows, one per speaker, the way the two
speech rows already are). The frame asset goes on the plan's frame row
above the picture, and the captions above that; the track plan
(`library/tools/timeline_layout.py`) owns those indices, never a
constant here. (`tv_frame.LAYER_TRACKS` still reads footage V1, frame
V2, captions V3 - that is the MASTER path's shape in
`step_5_04_compile_manifest`, which never collapsed.)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from library.tools.ren_refusal import RenRefusal

FRAME_TRACK = 2
"""The reel video track the frame asset is placed on - for a
SINGLE-angle reel, where the plan reads V1 picture, V2 frame.

Kept as the legacy fallback, not the rule: the placer puts the frame
on the plan's frame row (`track_plan.row_for_role(FRAME)`), which is V3
on a two-angle reel under the captain's Reel 09 ruling (two picture
rows, the set above them). Readers match the frame by row NAME (see
`frame_overlay_items`), never by this number alone. It is V2 here
because that is what `tv_frame.LAYER_TRACKS` declares for the master
path and what the captain's own reference capture showed before the
ruling (`reel20-standard-zoom.json`: V1 footage at 2.30, V2 `TV 4k.png`
at 1.00, V3 captions at 1.00).
"""

MOTION_PLAN_KEY = "reel_motion_plan"
"""What the model's answer file carries, as `read_answer` accepts a bare list."""

MOTION_BASIS_KEY = "_vep_motion_basis"
MOTION_ANSWER_SHAPE_KEY = "_vep_motion_answer_shape"
MOTION_BASIS_SCHEMA = "reel_motion_basis/1"
"""Private response metadata binding an answer to the motion ask it saw."""

MOTION_NOT_DECLARED = "look_not_declared"
MOTION_AWAITING_ANSWER = "awaiting_model_answer"
MOTION_PLANNED_NONE = "model_planned_none"
MOTION_EVERY_ENTRY_DROPPED = "every_entry_dropped"
MOTION_PLANNED = "planned"

MOTION_BASES = (MOTION_NOT_DECLARED, MOTION_AWAITING_ANSWER,
                MOTION_PLANNED_NONE, MOTION_EVERY_ENTRY_DROPPED,
                MOTION_PLANNED)
"""Why a reel carries the drift it carries, including none."""


class ReelLookRefused(RenRefusal):
    """The look cannot be placed on this reel, and this says why."""


def resolve_look(project_folder: str, frame_width: int = 0,
                 frame_height: int = 0) -> Optional[dict]:
    """This project's TV-frame declaration, or None for no look.

    Delegates entirely to `tv_frame.resolve_tv_frame`, resolving the
    brand template the same way `reel_build.declared_cards` does, so a
    reel and its master read one declaration.

    Given the delivery frame, the declaration is also CHECKED against it
    before it is returned - `tv_frame.assert_frameable`, which refuses a
    frame with no transparent window and one that covering would upscale
    beyond its own pixels.  A mismatched ASPECT is not refused: it is
    cover-scaled (`tv_frame.cover_zoom`), which is what the captain did
    by hand on 2026-09-09.  A caller that passes no frame gets the
    declaration unchecked, and the two callers that build something both
    pass one.
    """
    from library.tools.tv_frame import assert_frameable, resolve_tv_frame

    template = None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if os.path.exists(project_yaml):
        import yaml
        from library.tools.brand_registry import resolve_project_template
        with open(project_yaml, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        named = ((config.get("pipeline") or {}).get("brand_template") or "")
        if named:
            template = resolve_project_template(
                named, project_folder=project_folder)
    look = resolve_tv_frame(project_folder, template)
    if look is not None and frame_width and frame_height:
        assert_frameable(look, frame_width, frame_height)
    return look


def _template_style(project_folder: str):
    """This project's brand template style, or None.

    One spelling for the two grade-half resolvers below: both read the
    project's own `project.yaml` for the template name and return its
    `style` as the mapping `effective_series_look` reads. A project that
    names no template, or whose file is absent, resolves to None - and
    then the project's own declaration (or its absence) is the answer.
    """
    project_yaml = os.path.join(project_folder, "project.yaml")
    if os.path.exists(project_yaml):
        import yaml
        from library.tools.brand_registry import resolve_project_template
        with open(project_yaml, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        named = ((config.get("pipeline") or {}).get("brand_template") or "")
        if named:
            template = resolve_project_template(
                named, project_folder=project_folder)
            style = getattr(template, "style", None)
            if style is not None:
                return {"series_look": getattr(style, "series_look", None)}
    return None


def resolve_grade_look(project_folder: str) -> dict:
    """This project's designed film look as Fusion effect parameters.

    The same declaration step 5.01 resolves - the project's own
    `style.series_look` winning whole-slot over its brand template's
    (`effective_series_look`) - rendered to the parameter names
    `build_effect_comp` dispatches on (`DeclaredLook.fusion()`).
    `{}` where nothing is declared, and `{}` means no comp is drawn
    for the look's sake at all.

    A malformed declaration RAISES rather than shipping reels missing
    a grade somebody asked for. Read once per build, beside
    `resolve_look`, for the same reason.
    """
    from library.tools.series_look import (
        effective_series_look,
        resolve_look as resolve_series_look,
    )

    look = resolve_series_look(
        effective_series_look(_template_style(project_folder),
                             project_folder))
    return dict(look.fusion()) if look is not None else {}


def resolve_grade_cdl(project_folder: str) -> dict:
    """This project's CDL half, in the key names the renderer reads.

    The same declaration `resolve_grade_look` reads - the project's own
    `style.series_look` winning whole-slot over its brand template's
    (`effective_series_look`) - rendered by `DeclaredLook.cdl()` to the
    `slope_r`...`saturation` keys step 6.01 formats onto the master.
    `{}` where nothing is declared, and `{}` grades nothing.

    The DECLARED look only: no per-clip exposure normalisation and no
    colourist correction. Those are per master clip (step 5.01 folds
    them into each clip's own CDL), and a reel re-cuts the footage into
    different ranges - there is no clip they could follow. A malformed
    declaration RAISES, for the same reason the Fusion half does.
    """
    from library.tools.series_look import (
        effective_series_look,
        resolve_look as resolve_series_look,
    )

    look = resolve_series_look(
        effective_series_look(_template_style(project_folder),
                             project_folder))
    return dict(look.cdl()) if look is not None else {}


CDL_RETURN_IS_NOT_EVIDENCE = (
    "TimelineItem.SetCDL returns True without applying the grade. "
    "Measured 2026-09-10 on `Podcast (field test)` / `Reel 09 - "
    "your-website-is-only-20-percent`, task "
    "vep-fusion-grade-check-the-frame: the project's declared "
    "v04_teal_split CDL (slope 1.03/1.0/0.96, offset -0.01/0.005/0.02, "
    "power 1.0, saturation 1.12) returned True on all six picture "
    "clips and rendered a still BYTE-IDENTICAL to no grade at all - 0 "
    "of 2,073,600 pixels moved, max delta 0, same md5. Reproduced six "
    "times including a six-second settle and a re-grab, with "
    "SetCDL(saturation 0) immediately before AND after as a positive "
    "control that moved 601,760 pixels at max delta 154, so the clips "
    "demonstrably responded. numpy predicts those values should move "
    "2,073,600 pixels at max delta 22, so it is not arithmetic "
    "cancellation, and each of the four terms moves the picture on its "
    "own. Nothing readable back from Resolve distinguishes the two "
    "cases: there is no GetCDL, and a .drx exported from a "
    "SetCDL-graded clip does not carry the CDL either (checked - the "
    "node body is byte-for-byte the ungraded one plus a timestamp). "
    "Only exported pixels settle whether a CDL arrived."
)
"""Why a CDL record is never `verified`, with the numbers.

AGENTS.md 5's rule - judge a Resolve call by what it RETURNS, never by
`hasattr`, and a True past a silent clamp still lies - arriving from a
new direction: here the READ BACK lies too, because there is nothing to
read back. The rule this constant enforces is in `apply_grade`: a look
that can only be delivered by a route nothing can verify is REFUSED,
not reported.
"""

CDL_NODE_INDEX = "1"
"""The Color page node `apply_cdl` writes: PR 870 established SetCDL
lands on node 1, on the master and therefore here."""


def _footage_picture_items(timeline, track_plan, footage_sources,
                           record: Dict[str, Any], skip_note: str):
    """(item, short name) for every PLACED FOOTAGE item on a picture row.

    The one join both grade routes need, so `apply_cdl` and
    `apply_power_grade_to_footage` cannot drift on what counts as
    footage.  WHAT gets graded: picture items on the plan's a-roll rows
    whose pool item's File Path or basename is one of `footage_sources`
    (the placements' own source files).  A rendered card shares the
    first picture row but is a graphic, not footage - grading one would
    tint it, and the master path never grades one either.  The frame
    overlay and the captions ride other rows and are never visited.

    Everything skipped is RECORDED with `skip_note` saying which grade
    it did not get, because "no clip matched" and "no grade declared"
    are different facts and a reader must be able to tell them apart.
    """
    sources = {str(s) for s in (footage_sources or ())}
    basenames = {s.rsplit("/", 1)[-1].lower() for s in sources}
    for row in picture_rows(track_plan).values():
        try:
            items = timeline.GetItemListInTrack("video", row) or []
        except Exception:
            # A row Resolve will not list is not an empty row: its clips
            # go ungraded either way, and the record must say the row was
            # never seen rather than claim nothing on it needed grading.
            record["skipped"].append(
                f"V{row}: unreadable - {skip_note}")
            continue
        for item in items:
            try:
                pool_item = item.GetMediaPoolItem()
                path = (pool_item.GetClipProperty("File Path")
                        if pool_item is not None else "")
                name = (pool_item.GetClipProperty("File Name")
                        if pool_item is not None else "") or item.GetName()
            except Exception:
                path, name = "", ""
            short = (name or (path or "?").rsplit("/", 1)[-1])
            if not path or (path not in sources
                            and short.lower() not in basenames):
                record["skipped"].append(
                    f"{short}: not placed footage - {skip_note}")
                continue
            yield item, short


def apply_cdl(timeline, track_plan, cdl_values: dict,
              footage_sources=()) -> dict:
    """SetCDL on every footage picture item, the way 6.01 does it.

    **`applied` on this record means the CALL RETURNED TRUE and NOT
    that the grade reached the picture** - see
    `CDL_RETURN_IS_NOT_EVIDENCE`, which is why the record carries
    `verified: False` and why `apply_grade` refuses this route as a
    reel's only grade. It is kept as the mechanism because the master
    path uses it and because it is how a per-clip correction lands
    INSIDE an applied PowerGrade, where the node graph read-back does
    evidence the surrounding look.

    One item, one `SetCDL` with `NodeIndex` 1 and the four terms as
    4-decimal strings - at 3 the offsets, the smallest numbers in a
    CDL, lose part of the shadow tint (6.01's own comment). A falsy
    return falls back to `SetClipProperty`, the same route 6.01 takes;
    a clip neither route reaches is RECORDED, never raised: one clip's
    grade failing must not stop the reel, and the warning names it.

    WHAT gets it: footage picture items on the plan's a-roll rows,
    matched by source path or basename against `footage_sources` (the
    placements' own source files). A rendered card shares the first
    picture row but is a graphic, not footage - the look's CDL would
    tint it, and the master path never grades one either, because its
    CDL loop only reaches clips its per-clip table names. The frame
    overlay and the captions ride other rows and are never visited.

    WHEN it runs is the v04 still's own order
    (`data/vep-grade-variants/report.md` section 2): CDL first, as
    SetCDL on the timeline item, then the Fusion chain. The caller
    applies this inside `build_reel_timeline` - after the picture is
    placed and before it returns - while the Fusion pass runs
    afterwards in its own process, so CDL-before-Fusion holds
    structurally rather than by convention.

    THE CDL IS THE FALLBACK, NOT THE ROUTE, where a project declares a
    `.drx`: `apply_grade` calls `apply_power_grade_to_footage` instead
    of this, because `ApplyGradeFromDRX` REPLACES the whole node graph
    including the node `SetCDL` writes. Two grades on one clip is one
    grade nobody chose. This function stays exactly as the master's
    6.01 loop writes it, so the two paths keep one CDL spelling.
    """
    record: Dict[str, Any] = {"applied": [], "skipped": [],
                              "warnings": [], "verified": False,
                              "unverified_because": CDL_RETURN_IS_NOT_EVIDENCE}
    if not cdl_values:
        record["basis"] = "no look declared - nothing graded"
        return record
    slope = (f"{cdl_values.get('slope_r', 1.0):.4f} "
             f"{cdl_values.get('slope_g', 1.0):.4f} "
             f"{cdl_values.get('slope_b', 1.0):.4f}")
    offset = (f"{cdl_values.get('offset_r', 0.0):.4f} "
              f"{cdl_values.get('offset_g', 0.0):.4f} "
              f"{cdl_values.get('offset_b', 0.0):.4f}")
    power = (f"{cdl_values.get('power_r', 1.0):.4f} "
             f"{cdl_values.get('power_g', 1.0):.4f} "
             f"{cdl_values.get('power_b', 1.0):.4f}")
    saturation = f"{cdl_values.get('saturation', 1.0):.4f}"
    for item, short in _footage_picture_items(
            timeline, track_plan, footage_sources, record, "no CDL"):
        try:
            landed = item.SetCDL({
                "NodeIndex": CDL_NODE_INDEX,
                "Slope": slope,
                "Offset": offset,
                "Power": power,
                "Saturation": saturation,
            })
            if not landed:
                item.SetClipProperty("Slope", slope)
                item.SetClipProperty("Offset", offset)
                item.SetClipProperty("Power", power)
                item.SetClipProperty("Saturation", saturation)
        except Exception as exc:
            record["warnings"].append(f"SetCDL failed on {short}: {exc}")
            continue
        record["applied"].append(short)
    return record



def resolve_power_grade(project_folder: str) -> Optional[dict]:
    """This project's declared PowerGrade `.drx`, or None.

    Delegates entirely to `color_page_grade.resolve_color_page_grade`,
    so a reel and its master read ONE declaration and one provenance
    rule. Read once per build beside `resolve_grade_cdl`, for the same
    reason: a refused declaration must stop the whole build, not the
    fourteenth reel.
    """
    from library.tools.color_page_grade import resolve_color_page_grade

    return resolve_color_page_grade(project_folder)


def apply_power_grade_to_footage(timeline, track_plan, drx_path: str,
                                 footage_sources=(), cdl_values=None,
                                 cdl_node: Optional[str] = None) -> dict:
    """`ApplyGradeFromDRX` on every footage picture item of a reel.

    The Color page route, and the reason it exists: a `.drx` carries a
    REAL node graph - the reference grade's own Film Look Creator,
    Chromatic Adaptation and colour-space transforms - and no ASC CDL
    can express any of it. A CDL has four terms; a split-tone with a
    luminance midpoint, seven hue spheres and lum-vs-sat curves has
    none of them. Approximating that across `SetCDL` plus Fusion
    BrightnessContrast/Glow/Grain/Vignette is not a coarser version of
    the grade, it is a different picture.

    Reaches exactly the items `apply_cdl` reaches - same rows, same
    footage join - so swapping route does not swap WHICH clips are
    graded. A clip Resolve refuses is RECORDED and the reel continues:
    one clip's grade failing must not stop the build, and the record
    names the clip and what Resolve said.

    `cdl_node` is where the declared CDL lands INSIDE the applied
    graph, by LABEL. `The Grade Free` ships `BAL/EXP` and `W&B` as
    identity placeholders precisely so a per-clip exposure and balance
    correction goes there, under the look - so the look is the graph
    and the correction is one node inside it, rather than the two
    fighting for the clip. A label the graph does not carry is
    REFUSED per clip and recorded as `cdl_node_missing`: node 1 is the
    grade's own input colour-space transform, and writing a correction
    into it because a label was misspelt would replace the conversion
    the whole grade is built on. No `cdl_node` means the DRX alone -
    which is the grade exactly as its author saved it.
    """
    record: Dict[str, Any] = {"applied": [], "skipped": [],
                              "warnings": [], "path": drx_path,
                              "nodes": {}, "cdl_node": cdl_node,
                              "cdl_landed_on": {}, "verified": False}
    if not drx_path:
        record["basis"] = "no power grade declared - nothing graded"
        return record
    from library.tools.color_page_grade import (
        apply_cdl_to_node,
        apply_power_grade,
        node_index_by_label,
    )

    for item, short in _footage_picture_items(
            timeline, track_plan, footage_sources, record, "no DRX grade"):
        landed = apply_power_grade(item, drx_path)
        if not landed.get("applied"):
            record["warnings"].append(
                f"ApplyGradeFromDRX failed on {short}: "
                f"{landed.get('reason', 'no reason given')}")
            continue
        record["applied"].append(short)
        record["nodes"][short] = landed.get("nodes")
        if not (cdl_node and cdl_values):
            continue
        index = node_index_by_label(item, cdl_node)
        if index is None:
            record["warnings"].append(
                f"cdl_node_missing on {short}: the applied grade has no "
                f"node labelled {cdl_node!r}, so the per-clip CDL was "
                f"NOT placed - it is not guessed onto a node index.")
            continue
        placed = apply_cdl_to_node(item, index, cdl_values)
        record["cdl_landed_on"][short] = placed
        if not placed.get("landed"):
            record["warnings"].append(
                f"SetCDL on {cdl_node!r} (node {index}) refused for "
                f"{short}: {placed.get('reason', 'returned false')}")
    # VERIFIED means read back, not returned. Every clip that took the
    # grade reported a node COUNT off a re-fetched graph - the handle
    # goes stale across the apply - and a graph carrying the `.drx`'s
    # nodes is evidence the CDL route has no equivalent of. A count of
    # 1 is the bare graph: the call said yes and the nodes are not
    # there, which is exactly the shape this record exists to catch.
    counts = [n for n in record["nodes"].values() if isinstance(n, int)]
    record["verified"] = bool(
        record["applied"] and counts
        and len(counts) == len(set(record["nodes"]))
        and all(n > 1 for n in counts))
    if record["applied"] and not record["verified"]:
        record["warnings"].append(
            "the grade was APPLIED but not VERIFIED: no re-fetched node "
            "graph came back carrying more than the bare node. Treat "
            "this reel as ungraded until a frame says otherwise.")
    return record


def apply_grade(timeline, track_plan, cdl_values: dict,
                power_grade: Optional[dict] = None,
                footage_sources=(),
                allow_unverified_cdl: bool = False) -> dict:
    """The reel's grade, by whichever route the project declared.

    ONE call site in `build_reel_timeline`, so the choice between the
    two routes is made in one place and recorded in one shape. A
    project that declares a `.drx` gets the DRX and NOT the CDL -
    `ApplyGradeFromDRX` replaces the node graph `SetCDL` writes into,
    so applying both would leave whichever ran second and call it the
    grade. A project that declares no `.drx` gets the CDL half exactly
    as before.

    The returned record always says `route` - `"power_grade_drx"`,
    `"cdl"`, or `"none"` - because a reader looking at a reel that
    came out wrong must be able to tell which mechanism drew it
    without re-deriving the declaration.

    **A LOOK THAT CAN ONLY BE DELIVERED BY THE CDL ROUTE IS REFUSED.**
    `SetCDL` returns True without applying the grade
    (`CDL_RETURN_IS_NOT_EVIDENCE`), and there is nothing to read back
    that would catch it - so a reel built this way ships a video
    missing the grade somebody asked for, and a build record saying
    `applied` on six clips that were never touched. Reporting a grade
    nobody applied is worse than refusing, because it reads as
    coverage (AGENTS.md 10.4). The fix is one line of the project's own
    `project.yaml`: declare `color.power_grade_drx`, which IS verified
    - the node graph reads back the nodes the `.drx` builds.

    `allow_unverified_cdl=True` is the ONE deliberate exception and
    the master path is not it: it is for a caller that has its own
    pixel evidence, and it still returns `verified: False` so the
    record cannot claim otherwise. It is never defaulted on.
    """
    if power_grade and power_grade.get("path"):
        record = apply_power_grade_to_footage(
            timeline, track_plan, power_grade["path"],
            footage_sources=footage_sources,
            cdl_values=cdl_values,
            cdl_node=power_grade.get("cdl_node"))
        record["route"] = "power_grade_drx"
        record["provenance"] = dict(power_grade.get("provenance") or {})
        return record
    if cdl_values and not allow_unverified_cdl:
        raise ReelLookRefused(
            "this project declares a look but no "
            "`color.power_grade_drx`",
            "the only route to the picture is TimelineItem.SetCDL - "
            "and that route cannot be shown to have worked.\n\n"
            f"{CDL_RETURN_IS_NOT_EVIDENCE}\n\n"
            "Refusing here rather than writing `applied` "
            "against six clips nothing reached.",
            "declare the look as a `.drx` under `color.power_grade_drx` "
            "in the project's own project.yaml (with provenance - see "
            "library/tools/color_page_grade.py). That route IS "
            "verified: the node graph reads back the nodes the file "
            "builds.")
    record = apply_cdl(timeline, track_plan, cdl_values,
                       footage_sources=footage_sources)
    record["route"] = "cdl" if cdl_values else "none"
    return record


def _picture(placements: Sequence[dict]) -> List[dict]:
    """The video placements, in play order."""
    out = [p for p in placements
           if getattr(p["clip"], "track_type", "video") == "video"]
    out.sort(key=lambda p: p["snapped_record"])
    return out


def frame_runs(placements: Sequence[dict], fps: float) -> List[Tuple[int, int]]:
    """Contiguous (start_frame, end_frame) runs of picture on the reel.

    One frame clip per run, exactly as `compile_manifest._content_runs`
    does on the master: the set dresses the show, and a gap in the
    picture is a gap the set has nothing to sit on.
    """
    runs: List[List[int]] = []
    for p in _picture(placements):
        start = int(p["snapped_record"])
        end = start + int(round((p["source_out"] - p["source_in"]) * fps))
        if runs and start <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    return [(a, b) for a, b in runs]


FRAME_OVERLAY_NAME_SHAPE = r"^tv_frame_[0-9a-f]{10}(_\d+f)?$"
"""What a rendered frame overlay is called, as a pattern.

Read by the conformance verifier the same way `CARD_NAME_SHAPE` is: an
item on the frame track shaped like this and accounted for by the
declaration is the set, and one that is NOT accounted for is an overlay
appended out of band.

The duration suffix is OPTIONAL, not absent: renders written before
2026-09-09 carry `_<frames>f` (one file per length), and live timelines
still place those files until the captain re-points them.  A shape that
dropped either half would misread the set - the old files as out-of-band
overlays, or the new shared file as one.
"""


def frame_overlay_items(video_items, look) -> set:
    """The ids of timeline items that are this look's frame overlay.

    `look` None returns the empty set, so a project that declares no
    look excludes nothing and the verifier reads exactly what it read
    before.  Matching is on the frame ROW's name and the render name
    shape together: under the captain's Reel 09 ruling the frame sits
    on the plan's frame row (V3 on a two-angle reel), so a track
    number alone cannot identify it. The legacy V2 match stays for
    timelines built before rows were named.
    """
    import re

    if look is None:
        return set()
    shape = re.compile(FRAME_OVERLAY_NAME_SHAPE)
    out = set()
    for item in video_items or ():
        stem = (getattr(item, "source_file", "") or "").rsplit(
            "/", 1)[-1].rsplit(".", 1)[0]
        if not shape.match(stem):
            continue
        track_name = (getattr(item, "track_name", "") or "").strip()
        if track_name == "Frame":
            out.add(id(item))
        elif getattr(item, "track_index", 0) == FRAME_TRACK:
            out.add(id(item))
    return out


def declared_zoom_over(declared_crop_factor: float, look) -> float:
    """The crop factor a shot really plays at under the look.

    `tv_frame.v1_zoom_for_look` is ABSOLUTE on the conform, and the
    project's own framing decides what is being zoomed - so the picture
    a reel under the look delivers is the project's declared framing
    multiplied by the declared punch-in.  Returning the multiplied
    factor rather than teaching F12 a second geometry keeps ONE formula
    in `reel_framing.declared_picture`.
    """
    if look is None:
        return declared_crop_factor
    from library.tools.tv_frame import look_scale, v1_zoom_for_look
    return float(declared_crop_factor or 1.0) * v1_zoom_for_look(
        look["punch_in"], look_scale(look))


def screen_window_rect_for(look, frame_width: int, frame_height: int):
    """The look's screen window in timeline pixels, for readers.

    A thin pass-through to `tv_frame.screen_window_rect` so the verifier
    reaches the window through the same module it reaches every other
    part of the look through, rather than importing a second one.
    """
    from library.tools.tv_frame import screen_window_rect

    return screen_window_rect(look, frame_width, frame_height)


def uncovered_window_edges(delivered, window, tolerance: float = 1.0) -> list:
    """Which edges of the screen window this picture fails to reach.

    The one property an aimed punch-in has to keep, stated once so the
    placer's clamp and the conformance check grade the same thing.  The
    verifier cannot re-run the face measurement the aim came from, but
    it does not need to: what matters is not WHERE the picture was
    aimed, it is that the aim left no black inside the television.
    """
    edges = []
    if delivered.left > window[0] + tolerance:
        edges.append(f"left {delivered.left - window[0]:.1f}px")
    if delivered.top > window[1] + tolerance:
        edges.append(f"top {delivered.top - window[1]:.1f}px")
    if delivered.right < window[2] - tolerance:
        edges.append(f"right {window[2] - delivered.right:.1f}px")
    if delivered.bottom < window[3] - tolerance:
        edges.append(f"bottom {window[3] - delivered.bottom:.1f}px")
    return edges


def _rendered_frame_count(path: str) -> int:
    """How many frames the render on disk holds, or 0 when it is unusable.

    A missing, corrupt or unreadable file reads as 0, which renders it
    again - the caller treats "shorter than needed" and "absent" as one
    case, and a probe that raised instead would turn a stale render into
    a refusal.  `nb_frames` is what the render was asked for, so it is
    what is read back; a container that does not report it falls back to
    its duration times the requested rate.
    """
    import subprocess as _subprocess

    if not os.path.isfile(path):
        return 0
    try:
        probe = _subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames,r_frame_rate,duration",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, encoding="utf-8", check=False)
    except OSError:
        return 0
    if probe.returncode != 0:
        return 0
    fields = {}
    for line in (probe.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    try:
        if fields.get("nb_frames", "").isdigit():
            return int(fields["nb_frames"])
        num, den = fields.get("r_frame_rate", "0/1").split("/")
        return int(float(fields.get("duration", 0)) * float(num)
                   / float(den or 1))
    except (ValueError, ZeroDivisionError):
        return 0


def frame_overlay_segments(look: dict, runs: Sequence[Tuple[int, int]],
                           fps: float, width: int, height: int,
                           project_folder: str) -> List[dict]:
    """The frame asset as timed overlay segments, one per picture run.

    RENDERED rather than placed as a still, because a still cannot be
    placed for an arbitrary length through Resolve's scripting API: a
    PNG reports one frame and Resolve gives it the project's standard
    still duration, so a sixty-one second run came out five seconds long
    and the conformance check read the difference as a speaker losing
    thirty-one seconds of picture.  Stretching the placed still
    afterwards would mean duration surgery through the same API whose
    still handling already proved untrustworthy here, so the movie stays:
    what changed is its IDENTITY, not its carriage.

    ONE artefact per still, however many lengths use it - the captain's
    model, measured against on 2026-09-09 when six renders of one frame
    at six lengths held 2.8 GB.  The duration used to be part of the
    filename, so the existence check missed on every new length and
    re-rendered the whole thing.  Now the file is rendered ONCE at the
    longest run and shorter runs trim it at placement (`startFrame: 0`,
    `endFrame: total_frames` in `place_overlay_segments`), which needs
    no re-encode because a looped still is the same picture on every
    frame.  A run longer than the render on disk re-renders it, still as
    the one file; a run shorter than it renders nothing.

    Rendered at the asset's COVER size for this frame
    (`tv_frame.cover_size`), never at the delivery frame: the overlay is
    then placed at `tv_frame.cover_zoom` and Resolve draws it at exactly
    the pixels rendered here.  The first version rendered a fitted band
    padded into 1080x1920, which is the letterboxed strip the captain
    rejected - and zooming THAT to cover would have upscaled a 1080-wide
    render 3.16x.
    """
    import hashlib
    import subprocess as _subprocess

    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.tv_frame import (
        applied_rotation, cover_size, look_scale, oriented_size)

    out_dir = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "reel_look", "frame_overlays")
    os.makedirs(out_dir, exist_ok=True)

    asset = look["asset"]
    from PIL import Image
    with Image.open(asset) as image:
        asset_size = image.size
    # Turned upright for this delivery FIRST, then measured: the
    # captain's frame is a landscape television and a reel is portrait,
    # and rotated it matches the delivery exactly (2160x3840 against
    # 1080x1920) instead of needing a cover zoom at all.
    rotation = applied_rotation(look, asset_size, width, height)
    drawn_width, drawn_height = cover_size(
        oriented_size(asset_size, rotation), width, height)

    # A scaled look (`tv_frame.validate_scale`) draws the television
    # smaller INSIDE the same canvas and fills the rest with opaque
    # black: the overlay still covers the whole delivery at the same
    # zoom and offset, and the picture - wider than the window it shows
    # through - cannot spill past the smaller bezel. Zooming the overlay
    # instead uncovered the frame's edges and showed the picture there
    # (the captain's shrink previews, 2026-09-25).
    shrink = look_scale(look)
    stamp = hashlib.sha1(
        f"{asset}|{os.path.getmtime(asset)}|{drawn_width}x{drawn_height}"
        f"|r{rotation}|{fps}".encode("utf-8")
        + (f"|s{shrink}".encode() if shrink != 1.0 else b"")
    ).hexdigest()[:10]

    # `transpose=1` is a quarter turn clockwise, `2` anticlockwise, and
    # 180 is two of them. Named here rather than computed, because
    # ffmpeg's filter takes a mode and not an angle.
    transpose = {90: "transpose=1,", 180: "transpose=1,transpose=1,",
                 270: "transpose=2,"}.get(int(rotation), "")

    runs = [(int(start_frame), int(end_frame))
            for start_frame, end_frame in runs]
    if not runs:
        return []
    # The file's identity is the ASSET's - the stamp above, which is
    # already correct - and duration is not part of it.
    path = os.path.join(out_dir, f"tv_frame_{stamp}.mov")
    longest = max(end_frame - start_frame for start_frame, end_frame in runs)
    if _rendered_frame_count(path) < longest:
        # ProRes 4444 for the alpha: the bezel is largely transparent
        # and a codec without an alpha plane would put a black card
        # over the picture rather than a window onto it.
        result = _subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", asset,
            "-t", f"{longest / fps:.5f}",
            "-r", f"{fps:.6f}",
            "-vf", (f"{transpose}"
                    f"scale={drawn_width}:{drawn_height}:flags=lanczos"
                    + ("" if shrink == 1.0 else
                       f",scale={_even(drawn_width * shrink)}:"
                       f"{_even(drawn_height * shrink)}:flags=lanczos,"
                       f"format=yuva444p10le,"
                       f"pad={drawn_width}:{drawn_height}:"
                       f"(ow-iw)/2:(oh-ih)/2:color=black@1.0")),
            "-c:v", "prores_ks", "-profile:v", "4444",
            "-pix_fmt", "yuva444p10le", path,
        ], capture_output=True, encoding="utf-8", check=False)
        if result.returncode != 0 or not os.path.isfile(path):
            raise ReelLookRefused(
                f"the TV frame could not be rendered to {longest} frames",
                f"ffmpeg exited {result.returncode}. "
                f"{(result.stderr or '').strip()[-500:]}",
                "re-run the build; if it refuses again, the ffmpeg error "
                "above names the cause (usually the frame asset)")
    segments = []
    for start_frame, end_frame in runs:
        segments.append({
            "overlay_path": path,
            "timeline_start": start_frame / fps,
            "total_frames": end_frame - start_frame,
        })
    return segments


def _even(value: float) -> int:
    """Round to an even pixel count, which every encoder here accepts."""
    return max(2, round(value / 2) * 2)


def frame_properties(look: dict, frame_width: int,
                     frame_height: int,
                     draw_gain: float = None) -> Dict[str, float]:
    """The transform the frame overlay plays under: the COVER zoom.

    Derived by `tv_frame.cover_zoom` from the size the overlay was
    RENDERED at - which is the asset turned upright for this delivery
    and scaled to cover - so nothing here holds a number.  For the
    captain's 3840x2160 asset in a 1080x1920 reel the turn makes the
    aspects match exactly, and this computes 1.0: the frame plays at
    its natural size and the alignment is the rotation, not a zoom.
    """
    from library.tools.tv_frame import (
        applied_rotation, cover_size, cover_zoom, oriented_size)

    from PIL import Image
    with Image.open(look["asset"]) as image:
        asset_size = image.size
    rotation = applied_rotation(look, asset_size, frame_width, frame_height)
    drawn = cover_size(oriented_size(asset_size, rotation),
                       frame_width, frame_height)
    # A scaled look is drawn smaller INSIDE the overlay
    # (`frame_overlay_segments`), so the overlay itself keeps this zoom.
    zoom = cover_zoom(drawn, frame_width, frame_height)
    properties = {"ZoomX": zoom, "ZoomY": zoom}
    offset = int(look.get("offset_y") or 0)
    if offset:
        # The frame moves with its window (`tv_frame.screen_window_rect`
        # adds the same offset), down being a NEGATIVE Tilt.
        from library.tools.resolve_transform import (
            FALLBACK_DRAW_GAIN, units_for_shift)
        properties["Tilt"] = -units_for_shift(
            offset, drawn[1], frame_height,
            draw_gain=FALLBACK_DRAW_GAIN if draw_gain is None else draw_gain)
    return properties


PUNCH_IN_REFUSED_NO_SUBJECT = "no_subject_measured"
"""Why a shot plays unpunched: nothing measured where the speaker is."""

PUNCH_IN_REFUSED_NOT_A_CLOSE_UP = "more_than_one_subject"
"""Why a shot plays unpunched: it holds more than one person.

A crop aimed at "the largest face" aims at whoever sits nearest the
camera, which in a two-shot is not reliably the speaker - and cropping
to the wrong one puts the speaker outside the frame. Refused rather than
aimed at a guess, which is the same ruling as having no measurement at
all.
"""


class PunchInLeavesBlack(ReelLookRefused):
    """The picture does not reach the edges of the television's screen."""


def punch_in_properties(look: dict, subject, source_width: int,
                        source_height: int, frame_width: int,
                        frame_height: int, window=None,
                        draw_gain: float = None):
    """The transform one SHOT plays under the frame, or None to refuse.

    Returns None when `subject` is None or the shot holds more than one
    person: a crop with nothing aiming it is a guess about where the
    speaker is, and the captain's ruling is to refuse rather than guess.

    Where a subject IS measured, three numbers are decided here and each
    is derived from something measured:

    - **Zoom** must COVER THE SCREEN WINDOW the frame leaves, not the
      delivery frame.  Those are different rectangles, and using the
      frame was the defect: with the bezel cover-scaled the window is
      2491x1853 timeline pixels while a 2.30 picture is 2484x1397, so
      the television showed 228px of black above and below its own
      picture.  `tv_frame.window_cover_zoom` derives the minimum, and
      the drawn zoom is the LARGER of that and the declaration - a
      project may punch in tighter than the screen needs, never looser.
      With the frame turned upright the minimum is 2.3070 against the
      captain's declared 2.30, which is the bezel and the punch-in
      agreeing to three pixels.
    - **Pan and Tilt** aim the subject at the centre of the WINDOW and
      are clamped so the picture still covers that window on every
      edge.  Aiming at the frame centre would aim at a point the
      viewer cannot see through the bezel.  The aim is computed in
      frame pixels and converted to Resolve units by the one measured
      law (`library/tools/resolve_transform.py`) - a unit is not a
      pixel, and treating it as one under-applied every vertical aim
      by 3.16x.
    - Then the result is CHECKED: a transform that leaves any black
      inside the window raises `PunchInLeavesBlack` rather than being
      placed.  That is the defect the captain has had to catch twice,
      and a post-condition is what stops a third time.
    """
    from library.tools.tv_frame import (
        look_scale,
        v1_zoom_for_look,
        window_cover_zoom,
    )
    from library.tools.resolve_transform import (
        fit_base_scale, pan_tilt_for_centre)

    if subject is None:
        return None
    if int(getattr(subject, "others", 0)) > 0:
        return None
    if window is None:
        raise ValueError(
            "punch_in_properties needs the screen window it must cover; "
            "covering the delivery frame instead is the defect this "
            "argument exists to make impossible to repeat.")

    declared = v1_zoom_for_look(look["punch_in"], look_scale(look))
    required = window_cover_zoom(source_width, source_height, window,
                                 frame_width, frame_height)
    zoom = max(declared, required)

    fit = fit_base_scale(source_width, source_height,
                         frame_width, frame_height)
    shown_width = source_width * fit * zoom
    shown_height = source_height * fit * zoom

    window_cx = (window[0] + window[2]) / 2.0
    window_cy = (window[1] + window[3]) / 2.0
    # The aim is decided in FRAME PIXELS - put the subject at the
    # centre of what the viewer can actually see - and converted to
    # Resolve units once, at the end.  Pan/Tilt are not frame pixels:
    # one unit moves the picture `source_dim / frame_dim * fit`
    # pixels (`library/tools/resolve_transform.py`, the same law every
    # overlay is placed by).  Spelling the aim in units directly was
    # right on Pan only because this geometry's fit is width-bound,
    # which makes that factor exactly 1; on Tilt it under-applied the
    # aim by 3.16x, and `assert_covers_window` read the result back
    # through the same error, so it could pass a punch that leaves
    # black.  Both halves now speak one language.
    aim_x = window_cx + shown_width * (0.5 - float(subject.center_x))
    aim_y = window_cy + shown_height * (
        0.5 - float(getattr(subject, "center_y", 0.5)))
    # And no further than the picture can go while still covering it.
    aim_x = max(window[2] - shown_width / 2.0,
                min(window[0] + shown_width / 2.0, aim_x))
    aim_y = max(window[3] - shown_height / 2.0,
                min(window[1] + shown_height / 2.0, aim_y))
    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN
    if draw_gain is None:
        draw_gain = FALLBACK_DRAW_GAIN
    pan, tilt = pan_tilt_for_centre(
        source_width, source_height, frame_width, frame_height,
        aim_x, aim_y, fit, draw_gain)

    properties = {"ZoomX": zoom, "ZoomY": zoom,
                  "Pan": round(pan, 3), "Tilt": round(tilt, 3)}
    assert_covers_window(properties, source_width, source_height,
                         frame_width, frame_height, window,
                         draw_gain=draw_gain)
    return properties


def window_zoom_for(look, source_size, frame_width: int,
                    frame_height: int) -> float:
    """The minimum zoom this look's screen window needs, for reporting."""
    from library.tools.tv_frame import screen_window_rect, window_cover_zoom

    window = screen_window_rect(look, frame_width, frame_height)
    return window_cover_zoom(source_size[0], source_size[1], window,
                             frame_width, frame_height)


def assert_covers_window(properties, source_width: int, source_height: int,
                         frame_width: int, frame_height: int,
                         window, tolerance: float = 1.0,
                         draw_gain: float = None) -> None:
    """Raise unless the picture reaches every edge of the screen window.

    The check the captain should not have had to make: black inside a
    television's screen is the most visible defect this look can have,
    and it survived two reviews because nothing measured it.  One pixel
    of tolerance, for the same reason `reel_framing.PIXEL` allows one -
    two roundings of one real number.
    """
    from library.tools.reel_framing import delivered_picture

    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN
    if draw_gain is None:
        draw_gain = FALLBACK_DRAW_GAIN
    picture = delivered_picture(source_width, source_height,
                                frame_width, frame_height, properties,
                                draw_gain=draw_gain)
    bands = uncovered_window_edges(picture, window, tolerance)
    if bands:
        raise PunchInLeavesBlack(
            "the punch-in leaves black inside the television's screen: "
            f"{', '.join(bands)}",
            f"the picture is {picture.rect} and the screen window is "
            f"({window[0]:.0f}, {window[1]:.0f}, {window[2]:.0f}, "
            f"{window[3]:.0f}). A picture that does not reach the edges "
            f"of the screen shows the set's own background through it, "
            f"which is what a viewer reads as a broken render",
            "raise the `punch_in` zoom in the reel look declaration "
            "until the picture covers the screen window, then rebuild")


def assert_motion_covers_window(properties: dict, motion: Sequence[dict],
                                 source_size: tuple, frame_width: int,
                                 frame_height: int, window,
                                 draw_gain: float) -> dict:
    """Prove the least zoom of a drift still covers the measured window.

    The base punch-in has already been read back and checked on the actual
    Resolve item. Ken Burns scales are relative to it, so the smallest scale
    in every move is composed with that held transform and sent through the
    same black-edge predicate with this build's measured draw gain.
    """
    factors = [1.0]
    for spec in motion:
        params = spec["params"]
        for key in ("zoom_start", "zoom_mid", "zoom_end"):
            value = params.get(key)
            if value is None:
                continue
            if (isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))):
                raise ReelLookRefused(
                    f"the Ken Burns move on picture shot "
                    f"{spec['target_block_position']} has a non-finite "
                    f"{key} scale {value!r}.",
                    "A non-finite transform cannot prove that the measured "
                    "screen window stays covered.",
                    "Replace the scale with finite values and rebuild the "
                    "reel.",
                )
            factors.append(float(value))
    minimum = min(factors)
    effective = dict(properties)
    effective["ZoomX"] = float(properties["ZoomX"]) * minimum
    effective["ZoomY"] = float(properties["ZoomY"]) * minimum
    assert_covers_window(
        effective, int(source_size[0]), int(source_size[1]),
        frame_width, frame_height, window, draw_gain=draw_gain)
    return {"minimum_relative_scale": minimum,
            "draw_gain": float(draw_gain),
            "checked_zoom_x": effective["ZoomX"],
            "checked_zoom_y": effective["ZoomY"]}


def power_effects(look: dict, first_label: str,
                  last_label: str, ending=None) -> Dict[str, dict]:
    """The switch-on / switch-off comp keys, per clip label.

    The same two keys `compile_manifest` sets on the master, resolved the
    same way: the module's declared timings, overridden by whatever the
    declaration states.  The animation runs on the picture, not on the
    set - so it lands on the first and last FOOTAGE clip, never on the
    frame asset.

    `ending` is the reel's DECLARED ending
    (`library/tools/reel_ending.py`) or None.  Where one is declared it
    owns the tail: the element it names is what draws there, and a
    declaration of `none` draws nothing.  Without a declaration this
    arms the switch-off on whatever clip sorts last, which is what it
    has always done - and is exactly how Reel 13 armed an 18-frame
    animation onto a 12-frame shot that had no business being the
    ending at all.
    """
    from library.tools.tv_power import switch_shape

    # ONE shape, two directions: the head plays it black -> picture and
    # the tail picture -> black, off the SAME declaration, so a project
    # cannot re-time one half and leave the other behind.
    declared = (look or {}).get("power", {}) or {}
    head = dict(switch_shape())
    head.update(declared)
    tail = dict(head)

    out: Dict[str, dict] = {}
    out.setdefault(first_label, {}).update(
        {"tv_power_head": True, "tv_power_head_timing": head})
    if ending is None:
        out.setdefault(last_label, {}).update(
            {"tv_power_tail": True, "tv_power_tail_timing": tail})
        return out
    from library.tools import reel_ending as _ending

    keys = _ending.tail_effects(ending, look)
    if keys:
        out.setdefault(last_label, {}).update(keys)
    return out


def clip_label(index: int) -> str:
    """What a reel's picture clip is called in the Fusion manifest.

    Positional, because a reel's picture is a sequence of keep ranges and
    nothing else names them.  The label is only ever used to join this
    module's own effects to its own manifest clips, so it never leaves.
    """
    return f"reel_picture_{index:02d}"


def motion_spine(placements: Sequence[dict], fps: float,
                 word_segments: Sequence[dict] = ()) -> dict:
    """The reel's picture as a SPINE step 4.03's resolver can read.

    One block per picture placement, positions counted from zero, with
    the reel's own timeline seconds.  Built so the drift plan for a reel
    goes through `post_bridge.resolve_vfx` unchanged - which is what
    makes `no_stated_reason` and `ken_burns_without_direction` true here
    without either being restated.
    """
    structure = []
    for index, p in enumerate(_picture(placements)):
        start = int(p["snapped_record"])
        end = start + int(round((p["source_out"] - p["source_in"]) * fps))
        # The MASTER seconds this shot was cut from travel too, because
        # the words are recorded against the master timeline and a shot
        # described with no words is a shot the model cannot reason
        # about (AGENTS.md 10.1: a table the prompt names arriving with
        # zero rows is reported, not shipped empty).
        master_start = (getattr(p["clip"], "timeline_start", 0.0)
                        + (p["source_in"]
                           - getattr(p["clip"], "source_in", 0.0)))
        structure.append({
            "position": index,
            "timeline_start": round(start / fps, 3),
            "timeline_end": round(end / fps, 3),
            # The seconds are a display value; plan_vfx resolves word
            # edges against the same frame cursor as the placed picture.
            # Keeping both frame boundaries here lets a sub-frame
            # transcript overrun snap to the actual played edge.
            "timeline_start_frame": start,
            "timeline_end_frame": end,
            "master_start": round(master_start, 3),
            "master_end": round(master_start
                                + (p["source_out"] - p["source_in"]), 3),
            "source_start": float(p["source_in"]),
            "source_end": float(p["source_out"]),
            "source_file": p["clip"].source_file,
            "speaker": p.get("speaker") or "",
            "word_timestamps": [],
        })
    for block in structure:
        for segment in word_segments or ():
            if (segment["source_file"] != block["source_file"]
                    or segment["source_start"] is None):
                continue
            segment_timeline_start = float(segment["timeline_start"])
            segment_source_start = float(segment["source_start"])
            for word in segment["words"]:
                # Transcript word spans are in master-timeline seconds;
                # motion anchors are resolved against the source range
                # played by this picture clip.
                try:
                    source_start = (segment_source_start
                                    + float(word["start"])
                                    - segment_timeline_start)
                    source_end = (segment_source_start
                                  + float(word["end"])
                                  - segment_timeline_start)
                except (TypeError, ValueError):
                    continue
                if (source_start < block["source_end"]
                        and source_end > block["source_start"]):
                    block["word_timestamps"].append({
                        "word": word["word"],
                        "source_start": source_start,
                        "source_end": source_end,
                    })
        block["word_timestamps"].sort(
            key=lambda word: word["source_start"])
    return {"structure": structure, "frame_rate": fps}


MOTION_HANDOFF = """Plan this reel's picture MOTION, shot by shot.

The camera stays on the picture layer. Captions and graphics stay on their
own tracks above it. Add gentle Ken Burns movement through the speaking BODY,
not only on the opener or the closer: aim for one move per roughly 6-8 seconds
of continuous talking-head footage when the image and words leave room for it.
On a long uncut shot, use several non-overlapping word-anchored moves rather
than one crawl across the whole take. Alternate zoom-in and zoom-out across
successive body moves within each continuous shot. After a cut, start a new
move sequence from the already-framed picture. Keep each move slow and
subtle, never a punch.

Rules that are not yours to change:

- `context.locked_closing_moves` are the existing CTA moves. Copy each entry
  exactly, with the same target, timing, direction, values and rationale.
  Do not add a new move to any CTA shot. `context.existing_reel_motion_plan`
  is the prior plan. If adding cadence to a body shot with an existing
  whole-shot Ken Burns entry, replace that entry with anchored windows; do not
  layer the new windows over it. Leave unchosen body entries and other picture
  treatments alone.
- The cadence is a target, not a move on every shot. Skip a shot or span that
  is too short, visually busy, or would fight its words or another picture
  treatment. Do not put a Ken Burns move on a shot that already has a punch
  or other camera treatment. Captions, graphics and their timing stay intact.
- Every new move belongs wholly inside one `target_block_position`. On a long
  shot, set `anchor` and `anchor_end` to words inside that shot so no move
  straddles a cut. Use only the listed `word_spans` and keep successive spans
  separate and ordered. A word omitted at a shot boundary is not an anchor
  in that shot.
- A move is a slow drift, not `zoom_emphasis` or a punch. Across adjacent
  spans on the same shot, each move starts at the scale where the previous
  move ended. Start the first body move at 1.0. A zoom-out returns to 1.0 or
  above, never below the already-framed picture; this keeps the measured
  screen-window coverage.
- `rationale` is per shot and about THAT shot.  An entry whose rationale is
  missing or blank is dropped as `no_stated_reason` (captain's ruling,
  2026-09-08).  "adds movement" is not a reason; what the shot is doing at
  that moment is.
- `ken_burns` reads its DIRECTION off your own values: `zoom_end` above
  `zoom_start` pushes in, below pulls out.  Equal or missing is dropped as
  `ken_burns_without_direction`.  Nothing is defaulted.
- Keep the scale change small over each several-second span. Keep every body
  scale between 1.0 and 1.05, and let each move take at least 3 seconds. The
  engine does not turn a Ken Burns move into a punch or alter the base speaker
  punch-in.

`shots` lists picture blocks, their spoken words and word timings.
`target_block_position` is a shot's position. `anchor` / `anchor_end` take
the same word forms as the shared sub-block anchor contract.
"""

BODY_KEN_BURNS_MAX_SCALE = 1.05
BODY_KEN_BURNS_MIN_SECONDS = 3.0

MOTION_EXPECTED_SCHEMA = (
    '{"reel_motion_plan": [{"target_block_position": 1, '
    '"anchor": {"word": "first"}, '
    '"anchor_end": {"word": "phrase", "edge": "end"}, '
    '"effect_type": "ken_burns", "params": {"zoom_start": 1.0, '
    '"zoom_end": 1.03}, "rationale": "why this body span drifts"}]}. '
    'Long shots may have several distinct anchored entries; preserve every '
    'entry in context.locked_closing_moves exactly.'
)


def motion_request_stem(reel_number: int) -> str:
    """The file stem this reel's motion ask and answer share."""
    return f"reel_motion_{int(reel_number):02d}"


def _motion_path(project_folder: str, reel_number: int, area) -> str:
    from library.tools.project_layout import ProjectLayout

    return os.path.join(str(ProjectLayout(project_folder).read_dir(area)),
                        motion_request_stem(reel_number) + ".json")


def _read_motion_plan_file(path: str) -> Optional[list]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(
            payload.get(MOTION_PLAN_KEY), list):
        return payload[MOTION_PLAN_KEY]
    return None


def motion_request_basis(request: dict) -> dict:
    """Fingerprint the prompt inputs that determine anchored motion.

    The old plan is intentionally excluded: it is echoed into a follow-up
    ask for continuity, and including it would make a response invalidate
    itself. The shots and locked closing positions are the spine the model
    addresses, including each block's text, timing and word spans.
    """
    context = request["context"]
    source = {
        "schema": MOTION_BASIS_SCHEMA,
        "step_id": request["step_id"],
        "reel_number": int(request["reel_number"]),
        "prompt": request["prompt"],
        "expected_schema": request["expected_schema"],
        "shots": context["shots"],
        "locked_closing_positions": context["locked_closing_positions"],
    }
    canonical = json.dumps(
        source, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")
    return {"schema": MOTION_BASIS_SCHEMA,
            "sha256": hashlib.sha256(canonical).hexdigest()}


def _load_motion_request(project_folder: str,
                         reel_number: int) -> Optional[dict]:
    from library.tools.project_layout import Area

    path = _motion_path(project_folder, reel_number, Area.LLM_REQUESTS)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            request = json.load(handle)
    except (OSError, ValueError):
        return None
    return request if isinstance(request, dict) else None


def bind_motion_answer(project_folder: str, reel_number: int,
                       answer: Any) -> dict:
    """Attach the current ask's source fingerprint to a model answer."""
    request = _load_motion_request(project_folder, reel_number)
    if request is None:
        raise ValueError(
            f"reel {int(reel_number):02d} has no readable motion ask; "
            "write the ask before its answer")
    answer_shape = "object"
    if isinstance(answer, list):
        payload = {MOTION_PLAN_KEY: answer}
        answer_shape = "list"
    elif isinstance(answer, dict):
        payload = dict(answer)
    else:
        raise TypeError(
            f"reel {int(reel_number):02d} motion answer must be a list "
            "or object")
    if (MOTION_BASIS_KEY in payload
            or MOTION_ANSWER_SHAPE_KEY in payload):
        raise ValueError(
            f"reel {int(reel_number):02d} motion answer uses a reserved "
            f"pipeline metadata key")
    payload[MOTION_BASIS_KEY] = motion_request_basis(request)
    if answer_shape == "list":
        payload[MOTION_ANSWER_SHAPE_KEY] = answer_shape
    return payload


def strip_motion_answer_basis(payload: Any) -> Any:
    """Return the model payload without pipeline-owned response metadata."""
    if not isinstance(payload, dict) or MOTION_BASIS_KEY not in payload:
        return payload
    answer = dict(payload)
    del answer[MOTION_BASIS_KEY]
    answer_shape = answer.pop(MOTION_ANSWER_SHAPE_KEY, None)
    if answer_shape == "list":
        plan = answer.pop(MOTION_PLAN_KEY, None)
        if isinstance(plan, list):
            return plan
    return answer


def _response_basis(payload: Any) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    basis = payload.get(MOTION_BASIS_KEY)
    if (isinstance(basis, dict)
            and basis.get("schema") == MOTION_BASIS_SCHEMA
            and isinstance(basis.get("sha256"), str)):
        return basis
    return None


def _answer_is_after_latest_ask(project_folder: str, reel_number: int,
                                response_path: str) -> bool:
    """Whether a legacy answer file landed after the recorded motion ask."""
    from library.tools import reel_phase_log

    event = reel_phase_log.latest_event(
        project_folder, reel_number, reel_phase_log.PLAN_ASKED,
        detail_prefix=(f"motion ask written: "
                       f"{motion_request_stem(reel_number)}.json"))
    if event is None:
        return False
    try:
        asked_at = datetime.fromisoformat(
            event["at"].replace("Z", "+00:00")).timestamp()
        answered_at = os.stat(response_path).st_mtime
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return answered_at >= asked_at


def _write_motion_response(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _invalidate_motion_answer(response_path: str, reel_number: int,
                              reason: str) -> None:
    try:
        os.unlink(response_path)
    except FileNotFoundError:
        return
    print(f"  reel {int(reel_number):02d}: invalidated stored motion "
          f"answer - {reason}; answer the current motion ask again",
          file=sys.stderr)


def locked_closing_positions(project_folder: str,
                             reel_number: int) -> set[int]:
    """The request's closing/CTA blocks whose existing plan is protected."""
    from library.tools.project_layout import Area

    path = _motion_path(project_folder, reel_number, Area.LLM_REQUESTS)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            request = json.load(handle)
    except (OSError, ValueError):
        return set()
    return {
        int(position)
        for position in request["context"].get(
            "locked_closing_positions", [])
    }


def _is_locked_motion_entry(entry: Any, locked_positions: set[int]) -> bool:
    if not isinstance(entry, dict):
        return False
    try:
        return int(entry["target_block_position"]) in locked_positions
    except (KeyError, TypeError, ValueError):
        return False


def _closing_positions(spine: dict, call_to_action) -> list[int]:
    structure = spine["structure"]
    if call_to_action is None:
        return ([max((int(block["position"]) for block in structure))]
                if structure else [])
    if isinstance(call_to_action, dict):
        cta_start = float(call_to_action["timeline_start"])
        cta_end = float(call_to_action["timeline_end"])
    else:
        cta_start = float(call_to_action.timeline_start)
        cta_end = float(call_to_action.timeline_end)
    return [
        int(block["position"])
        for block in structure
        if block["master_start"] < cta_end
        and block["master_end"] > cta_start
    ]


def write_motion_request(reel_number: int, reel_name: str,
                         spine: dict, says: Sequence[dict],
                         project_folder: str,
                         call_to_action=None) -> str:
    """Write the ask, and return where it went.

    Same three-part file interface the reel's V6 overlay ask uses
    (`reel_semantic_visual.write_request`): the engine writes the
    question, a model writes the answer beside it, and an unanswered ask
    builds the reel without motion and SAYS so.
    """
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.frame_utils import seconds_to_frame, frame_to_seconds
    frame_rate = spine.get("frame_rate")

    rows = []
    for block in spine.get("structure", []):
        # Selected on the MASTER seconds the shot was cut from, which is
        # the timebase the transcript rows are recorded against.
        spoken = [s for s in says
                  if s.get("timeline_start", 0.0) < block["master_end"]
                  and s.get("timeline_end", 0.0) > block["master_start"]]
        word_spans = []
        for word in block["word_timestamps"]:
            start = (block["timeline_start"]
                     + word["source_start"] - block["source_start"])
            end = (block["timeline_start"]
                   + word["source_end"] - block["source_start"])
            if frame_rate is not None:
                start_frame = seconds_to_frame(start, frame_rate)
                end_frame = seconds_to_frame(end, frame_rate)
                # A word ending on the shot's final frame is safe to offer
                # at that boundary. A later-frame edge is not part of this
                # picture and must not be offered as an anchor. Keep the
                # source transcript untouched so the shared resolver still
                # reports genuinely out-of-shot answers correctly.
                if (start_frame < block["timeline_start_frame"]
                        or end_frame > block["timeline_end_frame"]):
                    continue
                if start_frame == block["timeline_start_frame"]:
                    start = frame_to_seconds(start_frame, frame_rate)
                if end_frame == block["timeline_end_frame"]:
                    end = frame_to_seconds(end_frame, frame_rate)
            word_spans.append({
                "word": word["word"],
                "start": round(start, 3),
                "end": round(end, 3),
            })
        rows.append({
            "target_block_position": block["position"],
            "timeline_start": block["timeline_start"],
            "timeline_end": block["timeline_end"],
            "seconds": round(block["timeline_end"] - block["timeline_start"], 2),
            "speaker": block.get("speaker", ""),
            "says": " ".join(" ".join(str(s.get("text", "")).split())
                             for s in spoken)[:900],
            "word_spans": word_spans,
        })
    empty = [r["target_block_position"] for r in rows if not r["says"]]
    if empty:
        # SAID on the run that sends it. A shot described to the planner
        # with no words is a shot it must answer about blind, and the
        # answer would read as a reasoned one.
        print(f"  reel motion ask: shots {empty} carry NO WORDS - the "
              f"transcript has nothing over the master seconds they were "
              f"cut from, so any motion planned for them is planned blind",
              file=sys.stderr)
    closing_positions = set(_closing_positions(spine, call_to_action))
    basis_request = {
        "step_id": "reel_motion",
        "reel_number": int(reel_number),
        "prompt": MOTION_HANDOFF,
        "expected_schema": MOTION_EXPECTED_SCHEMA,
        "context": {
            "shots": rows,
            "locked_closing_positions": sorted(closing_positions),
        },
    }
    current_basis = motion_request_basis(basis_request)
    previous_request = _load_motion_request(project_folder, reel_number)
    response_path = _motion_path(
        project_folder, reel_number, Area.LLM_RESPONSES)
    stored_plan = _read_motion_plan_file(response_path)
    existing_plan = stored_plan or []
    if stored_plan is not None:
        try:
            with open(response_path, "r", encoding="utf-8") as handle:
                response_payload = json.load(handle)
        except (OSError, ValueError):
            response_payload = None
        response_basis = _response_basis(response_payload)
        if response_basis is not None:
            if response_basis != current_basis:
                _invalidate_motion_answer(
                    response_path, reel_number,
                    "the shot text, word timings or closing positions changed")
                existing_plan = []
        elif (isinstance(response_payload, dict)
              and MOTION_BASIS_KEY in response_payload):
            _invalidate_motion_answer(
                response_path, reel_number,
                "its source fingerprint metadata is malformed")
            existing_plan = []
        else:
            previous_basis = None
            if previous_request is not None:
                try:
                    previous_basis = motion_request_basis(previous_request)
                except (KeyError, TypeError, ValueError):
                    pass
            if previous_basis != current_basis:
                _invalidate_motion_answer(
                    response_path, reel_number,
                    "the legacy answer was keyed only by reel number and "
                    "its previous ask differs")
                existing_plan = []
            elif not _answer_is_after_latest_ask(
                    project_folder, reel_number, response_path):
                _invalidate_motion_answer(
                    response_path, reel_number,
                    "the latest recorded ask is newer than the legacy answer")
                existing_plan = []
            elif isinstance(response_payload, (dict, list)):
                if isinstance(response_payload, list):
                    response_payload = {
                        MOTION_PLAN_KEY: response_payload,
                        MOTION_ANSWER_SHAPE_KEY: "list",
                    }
                response_payload[MOTION_BASIS_KEY] = current_basis
                _write_motion_response(response_path, response_payload)

    locked_closing_moves = [
        entry for entry in existing_plan
        if _is_locked_motion_entry(entry, closing_positions)]
    payload = {
        "step_id": "reel_motion",
        "reel_number": int(reel_number),
        "reel_name": reel_name,
        "prompt": MOTION_HANDOFF,
        "context": {
            "shots": rows,
            "existing_reel_motion_plan": existing_plan,
            "locked_closing_positions": sorted(closing_positions),
            "locked_closing_moves": locked_closing_moves,
        },
        "expected_schema": MOTION_EXPECTED_SCHEMA,
        "project_folder": project_folder,
    }
    directory = str(ProjectLayout(project_folder).read_dir(Area.LLM_REQUESTS))
    os.makedirs(directory, exist_ok=True)
    path = _motion_path(project_folder, reel_number, Area.LLM_REQUESTS)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    # The ask, logged AT THE ASK: request files are rewritten on every
    # build, so their mtime is the last rewrite and not the ask - the
    # phase log carries its own timestamp instead (`reel_phase_log`).
    try:
        from library.tools import reel_phase_log as _phase_log
        _phase_log.log_event(
            project_folder, int(reel_number), reel_name,
            _phase_log.PLAN_ASKED,
            detail=f"motion ask written: "
                   f"{motion_request_stem(reel_number)}.json")
    except Exception:
        pass
    return path


def read_motion_answer(project_folder: str,
                       reel_number: int) -> Optional[list]:
    """The model's motion answer, or None when unanswered.

    A file that will not parse reads as UNANSWERED rather than as an
    empty plan, for the reason `reel_semantic_visual.read_answer` gives:
    a malformed answer is not a decision for no motion.
    """
    from library.tools.project_layout import Area

    response_path = _motion_path(
        project_folder, reel_number, Area.LLM_RESPONSES)
    plan = _read_motion_plan_file(response_path)
    if plan is None:
        return None
    request = _load_motion_request(project_folder, reel_number)
    if request is None:
        _invalidate_motion_answer(
            response_path, reel_number, "the current motion ask is missing")
        return None
    try:
        expected_basis = motion_request_basis(request)
    except (KeyError, TypeError, ValueError):
        _invalidate_motion_answer(
            response_path, reel_number,
            "the current motion ask has no readable source basis")
        return None
    try:
        with open(response_path, "r", encoding="utf-8") as handle:
            response_payload = json.load(handle)
    except (OSError, ValueError):
        return None
    response_basis = _response_basis(response_payload)
    if response_basis is not None:
        if response_basis != expected_basis:
            _invalidate_motion_answer(
                response_path, reel_number,
                "its recorded source fingerprint does not match the current "
                "motion ask")
            return None
    elif (isinstance(response_payload, dict)
          and MOTION_BASIS_KEY in response_payload):
        _invalidate_motion_answer(
            response_path, reel_number,
            "its source fingerprint metadata is malformed")
        return None
    elif not _answer_is_after_latest_ask(
            project_folder, reel_number, response_path):
        _invalidate_motion_answer(
            response_path, reel_number,
            "the latest recorded ask is newer than the legacy answer")
        return None
    else:
        if isinstance(response_payload, list):
            response_payload = {
                MOTION_PLAN_KEY: response_payload,
                MOTION_ANSWER_SHAPE_KEY: "list",
            }
        response_payload[MOTION_BASIS_KEY] = expected_basis
        _write_motion_response(response_path, response_payload)

    context = (request or {}).get("context") or {}
    locked_positions = {
        int(position)
        for position in context.get("locked_closing_positions", [])
    }
    if locked_positions:
        locked_moves = context["locked_closing_moves"]
        plan = [entry for entry in plan
                if not _is_locked_motion_entry(entry, locked_positions)]
        plan.extend(locked_moves)
    return plan


def resolve_motion_for_build(project_folder: str, reel_number: int,
                             spine: dict, fps: float) -> Tuple[list, dict]:
    """Read and resolve the stored answer at the build's pre-placement gate.

    This is kept offline so the build's plan check can be exercised without
    opening Resolve; both reel build paths call this same gate.
    """
    return resolve_motion(read_motion_answer(project_folder, reel_number),
                          spine, fps)


def resolve_motion(plan: Optional[list], spine: dict,
                   fps: float) -> Tuple[list, dict]:
    """(resolved specs, record) for a reel's drift plan.

    The resolver is step 4.03's, imported by path for the reason
    `reel_semantic_visual._step_4_06_bridge` gives: it is a step script,
    and there must not be a second copy of the drop rules.
    """
    record: Dict[str, Any] = {"basis": MOTION_AWAITING_ANSWER,
                              "proposed": 0, "resolved": 0, "dropped": []}
    if plan is None:
        return [], record
    record["proposed"] = len(plan)
    if not plan:
        record["basis"] = MOTION_PLANNED_NONE
        return [], record

    post_bridge = _step_4_03_post_bridge()
    dropped: list = []
    resolved = post_bridge.resolve_vfx(list(plan), spine, frame_rate=fps,
                                       dropped=dropped)
    record["resolved"] = len(resolved)
    record["moves"] = [{
        "target_block_position": int(spec["target_block_position"]),
        "timeline_start": float(spec["timeline_start"]),
        "timeline_end": float(spec["timeline_end"]),
        "effect_type": spec["effect_type"],
        "zoom_start": spec["params"].get("zoom_start"),
        "zoom_end": spec["params"].get("zoom_end"),
        "anchor_method": spec.get("anchor_method"),
    } for spec in resolved]
    record["dropped"] = [
        {"target_block_position": d.target_block_position,
         "effect_type": d.effect_type, "reason": d.reason,
         "detail": d.detail}
        for d in dropped]
    record["basis"] = (MOTION_PLANNED if resolved
                       else MOTION_EVERY_ENTRY_DROPPED)
    return resolved, record


def _step_4_03_post_bridge():
    """Step 4.03's post-bridge module, imported by path."""
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "steps", "step_4_03_plan_vfx",
                        "post_bridge.py")
    spec = importlib.util.spec_from_file_location(
        "step_4_03_post_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def picture_rows(track_plan) -> Dict[str, int]:
    """{angle key: reel video row index} for every a-roll row the plan mints.

    `track_plan` is `timeline_layout`'s answer - the live TrackPlan or
    its serializable form as recorded in the build record - never a
    hardcoded V1/V2 beside it.  Empty when no plan is given, which is
    the single-camera shape: one picture row, V1.
    """
    if track_plan is None:
        return {}
    from library.tools import timeline_layout as _layout

    if isinstance(track_plan, dict):
        rows = [t for t in track_plan.get("video_tracks", [])
                if t.get("role") == _layout.A_ROLL]
        return {str(t.get("occupant")): int(t.get("index")) for t in rows}
    return {str(t.occupant): int(t.index)
            for t in track_plan.aroll_rows()}


def _reel_row_for(placement: dict, rows: Dict[str, int],
                  first_row: int, angle_key) -> int:
    """The reel video row one picture placement's comp travels on.

    Read off the plan's rows by the placement's angle - the same join
    the builder places the picture by.  A placement whose angle the
    plan does not name (a layer the builder skipped) rides the first
    picture row, where the pass's source-path match leaves it
    unvisited exactly as before: misassignment skips, never
    misapplies.
    """
    key = None
    if angle_key is not None:
        try:
            key = angle_key(placement.get("clip"))
        except Exception:
            key = None
    else:
        try:
            key = str(int(getattr(placement.get("clip"), "track_index",
                                  "")))
        except (TypeError, ValueError):
            key = None
    if key is not None and key in rows:
        return rows[key]
    return first_row


def _motion_window_frames(spec: dict, placement: dict,
                          fps: float) -> dict:
    """The motion span in the placed clip's comp frames.

    Legacy plans have no word anchor and keep their existing whole-shot
    treatment. New body moves carry an explicit word-anchored span.
    """
    start = int(placement["snapped_record"])
    played = int(round((placement["source_out"]
                        - placement["source_in"]) * fps))
    if ("timeline_start" not in spec and "timeline_end" not in spec):
        return {"first_frame": 0, "last_frame": played - 1}
    if "timeline_start" not in spec or "timeline_end" not in spec:
        raise ReelLookRefused(
            f"Ken Burns window on picture shot "
            f"{spec['target_block_position']} has an incomplete time span.",
            "An anchored move needs both start and end times to remain "
            "inside one picture shot.",
            "Provide both timeline_start and timeline_end, then rebuild.",
        )
    first = int(round(float(spec["timeline_start"]) * fps)) - start
    last = int(round(float(spec["timeline_end"]) * fps)) - start - 1
    if first < 0 or last >= played or last <= first:
        raise ReelLookRefused(
            f"Ken Burns window {spec['timeline_start']:.3f}-"
            f"{spec['timeline_end']:.3f}s does not fit inside picture shot "
            f"{spec['target_block_position']} at "
            f"{start / fps:.3f}s for {played} played frames.",
            "The anchored motion window would escape the clip's played "
            "frames or collapse to no movement.",
            "Anchor both words inside this picture cut and rebuild.",
        )
    return {"first_frame": first, "last_frame": last}


def _assert_subtle_body_move(spec: dict, position: int,
                             window: dict, fps: float,
                             allow_mid: bool = True) -> tuple[float, float]:
    """Reject an anchored body drift that plays as a punch."""
    params = spec["params"]
    scales = []
    for key in ("zoom_start", "zoom_mid", "zoom_end"):
        value = params.get(key)
        if value is None and key == "zoom_mid":
            continue
        if (isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))):
            raise ReelLookRefused(
                f"Ken Burns move on picture shot {position} has an invalid "
                f"{key} scale {value!r}.",
                "The camera curve cannot be composed or checked with a "
                "missing or non-finite scale.",
                "Provide finite start and end scales and rebuild.",
            )
        scales.append(float(value))
    start_zoom, end_zoom = scales[0], scales[-1]
    if (not allow_mid and len(scales) == 3):
        raise ReelLookRefused(
            f"Ken Burns move on picture shot {position} has a midpoint "
            "scale that multi-window composition cannot preserve.",
            "The anchored-window compositor reads only each window's start "
            "and end scale.",
            "Remove `zoom_mid` from each anchored window and rebuild.",
        )
    if any(value < 1.0 or value > BODY_KEN_BURNS_MAX_SCALE
           for value in scales):
        raise ReelLookRefused(
            f"Ken Burns move on picture shot {position} exceeds the subtle "
            f"1.0-{BODY_KEN_BURNS_MAX_SCALE:.2f} body scale envelope.",
            "A larger scale change reads as a punch rather than a slow "
            "Ken Burns drift.",
            f"Keep both scales between 1.0 and "
            f"{BODY_KEN_BURNS_MAX_SCALE:.2f} and rebuild.",
        )
    move_type = spec["effect_type"]
    if move_type not in ("slow_zoom_in", "slow_zoom_out"):
        raise ReelLookRefused(
            f"picture shot {position} combines a Ken Burns window with "
            f"{move_type!r}.",
            "A punch or other camera effect on the same shot would clash "
            "with the slow body movement.",
            "Remove the new Ken Burns move from this treated shot or remove "
            "the conflicting treatment before rebuilding.",
        )
    increasing = (move_type == "slow_zoom_in")
    is_monotonic = all(
        (left <= right if increasing else left >= right)
        for left, right in zip(scales, scales[1:]))
    if not is_monotonic or start_zoom == end_zoom:
        raise ReelLookRefused(
            f"Ken Burns {move_type} on picture shot {position} does not "
            "move in its declared direction.",
            "A scale curve that runs against its declared direction is not "
            "the planned camera move.",
            "Reverse the scale endpoints or correct the move direction and "
            "rebuild.",
        )
    duration = (window["last_frame"] - window["first_frame"] + 1) / fps
    if duration < BODY_KEN_BURNS_MIN_SECONDS:
        raise ReelLookRefused(
            f"Ken Burns move on picture shot {position} lasts "
            f"{duration:.2f}s; body camera moves must take at least "
            f"{BODY_KEN_BURNS_MIN_SECONDS:.1f}s so they read as a drift, "
            "not a punch.",
            "The scale changes too quickly to read as a slow camera drift.",
            f"Choose a word-anchored span of at least "
            f"{BODY_KEN_BURNS_MIN_SECONDS:.1f} seconds and rebuild.",
        )
    return start_zoom, end_zoom


def fusion_manifest(placements: Sequence[dict], look: dict,
                    motion: Sequence[dict], fps: float,
                    track_plan=None, angle_key=None,
                    grade_look: Optional[dict] = None,
                    ending=None,
                    locked_closing_positions: Sequence[int] = ()) -> dict:
    """The manifest `apply_fusion_comps` reads for one reel.

    Only the keys that pass actually reads: `tracks.V{row}.clips` in
    placed order (matched to timeline items by full source path) and
    `fusion_effects.per_clip` keyed by label.  Building a whole assembly
    manifest here would be a second compile_manifest.

    The clips are grouped by the plan's a-roll rows - one entry per
    picture row the layout owner minted, never V1 alone.  Before the
    per-speaker ruling every reel had one picture row and V1-only was
    harmless; now a drift planned for a V2 shot must travel on V2 or
    the pass never visits it.  Labels stay positional over the whole
    picture, so the motion join by `target_block_position` is
    unchanged.  No plan means the legacy single-row shape: all clips
    on V1.

    `grade_look` is the project's designed film look as
    `resolve_grade_look` renders it (pivot contrast, glow, grain,
    vignette - the nodes no scriptable Color page call can reach).
    It is merged onto every picture clip with `setdefault`, the
    compile_manifest rule: a value the motion plan states wins over
    the look's, and None means no grade rides the reels at all.

    `ending` is the reel's declared ending or None; it decides what
    draws over the LAST picture clip (`power_effects` above).
    """
    picture = _picture(placements)
    rows = picture_rows(track_plan)
    first_row = min(rows.values()) if rows else 1
    by_row: Dict[int, list] = {}
    for index, p in enumerate(picture):
        row = (_reel_row_for(p, rows, first_row, angle_key) if rows
               else 1)
        by_row.setdefault(row, []).append((index, p))
    tracks: Dict[str, dict] = {}
    for row in sorted(by_row):
        tracks[f"V{row}"] = {"clips": [{
            "label": clip_label(index),
            "source_file": p["clip"].source_file,
            "source_in": p["source_in"],
            "source_out": p["source_out"],
        } for index, p in by_row[row]]}

    per_clip: Dict[str, dict] = {}
    if picture:
        for label, effects in power_effects(
                look, clip_label(0), clip_label(len(picture) - 1),
                ending=ending).items():
            per_clip.setdefault(label, {}).update(effects)

    # The designed film look (grade node_3 + node_4). Merged BEFORE
    # the motion join: a value the motion plan states wins, the
    # compile_manifest rule, unchanged.
    for index in range(len(picture)):
        effect = per_clip.setdefault(clip_label(index), {})
        for key, value in (grade_look or {}).items():
            effect.setdefault(key, value)

    # The drift, joined to the clip it covers by the shot position the
    # plan targeted - the same join `compile_manifest` makes by seconds,
    # done by index here because a reel's shots ARE its picture clips.
    # A single legacy entry still draws across the full shot. Multiple
    # anchored entries on one long shot become one Fusion spline with
    # held scale between their disjoint windows.
    by_position: Dict[int, list] = {}
    for spec in motion or ():
        position = int(spec["target_block_position"])
        if 0 <= position < len(picture):
            by_position.setdefault(position, []).append(spec)

    locked_closing = {int(position)
                      for position in locked_closing_positions}
    for position, specs in by_position.items():
        effect = per_clip.setdefault(clip_label(position), {})
        placement = picture[position]
        if position in locked_closing:
            # The closing shot carries the existing CTA treatment. Keep
            # its pre-window composition semantics byte-for-byte: an
            # anchor added to the request must never shorten or reshape
            # a locked closer that already plays over that full shot.
            for spec in specs:
                effect["_preset"] = spec["effect_type"]
                effect.update(spec["params"])
            continue
        if len(specs) == 1:
            spec = specs[0]
            effect["_preset"] = spec["effect_type"]
            effect.update(spec["params"])
            if spec["effect_type"] in ("slow_zoom_in", "slow_zoom_out"):
                if spec["params"].get("pan_end") is not None:
                    raise ReelLookRefused(
                        f"Ken Burns move on picture shot {position} adds a "
                        "pan.",
                        "The measured draw-gain coverage check proves the "
                        "scale envelope, not an animated pan envelope.",
                        "Remove `pan_end` from the slow body move and rebuild.",
                    )
                window = _motion_window_frames(spec, placement, fps)
                start_zoom, _ = _assert_subtle_body_move(
                    spec, position, window, fps)
                if not math.isclose(start_zoom, 1.0,
                                    rel_tol=0.0, abs_tol=1e-4):
                    raise ReelLookRefused(
                        f"the first Ken Burns move on picture shot {position} "
                        "must start at the already-framed 1.0 scale.",
                        "No earlier body drift establishes a larger held "
                        "scale for this shot.",
                        "Start this shot's first body window at 1.0 and "
                        "rebuild.",
                    )
                if spec.get("anchor_method"):
                    effect["effect_window_frames"] = window
            continue

        ordered = sorted(
            specs,
            key=lambda entry: float(entry.get("timeline_start", -math.inf)))
        windows = []
        previous_last = -1
        previous_end = None
        previous_direction = None
        for index, spec in enumerate(ordered):
            if not spec.get("anchor_method"):
                raise ReelLookRefused(
                    f"picture shot {position} has multiple camera moves and "
                    "one spans the full shot; anchor each move to a distinct "
                    "word span or leave the shot with one move.",
                    "Combining a full-shot treatment with anchored windows "
                    "would make their camera curves overlap.",
                    "Anchor each move to a distinct word span or keep the "
                    "existing single-shot treatment unchanged.",
                )
            params = spec["params"]
            start_zoom = params["zoom_start"]
            end_zoom = params["zoom_end"]
            if any(isinstance(value, bool)
                   or not isinstance(value, (int, float))
                   or not math.isfinite(float(value))
                   for value in (start_zoom, end_zoom)):
                raise ReelLookRefused(
                    f"picture shot {position} has a Ken Burns scale that is "
                    "not a finite number.",
                    "The camera curve cannot be composed or checked with a "
                    "non-finite scale.",
                    "Replace the scale endpoints with finite numbers and "
                    "rebuild.",
                )
            start_zoom, end_zoom = float(start_zoom), float(end_zoom)
            if start_zoom < 1.0 or end_zoom < 1.0:
                raise ReelLookRefused(
                    f"picture shot {position} has a zoom below the already-"
                    "framed 1.0 scale. A zoom-out must return to 1.0 or "
                    "above so it cannot uncover the measured screen window.",
                    "The held picture transform is the coverage baseline, "
                    "and a smaller relative scale can reveal black edges.",
                    "Return the zoom-out to 1.0 or above and rebuild.",
                )
            if index == 0 and not math.isclose(start_zoom, 1.0,
                                               abs_tol=1e-4):
                raise ReelLookRefused(
                    f"the first Ken Burns move on picture shot {position} "
                    "must start at the already-framed 1.0 scale.",
                    "No earlier body drift establishes a larger held scale "
                    "for this shot.",
                    "Start this shot's first body window at 1.0 and rebuild.",
                )
            direction = spec["effect_type"]
            if direction not in ("slow_zoom_in", "slow_zoom_out"):
                raise ReelLookRefused(
                    f"picture shot {position} combines a Ken Burns move "
                    f"with {direction!r}. Keep the existing picture "
                    "treatment separate from body drift.",
                    "A punch or other camera effect on the same shot would "
                    "clash with the slow body movement.",
                    "Remove the new Ken Burns move from this treated shot "
                    "or remove the conflicting treatment before rebuilding.",
                )
            if (previous_direction is not None
                    and direction == previous_direction):
                raise ReelLookRefused(
                    f"successive Ken Burns moves on picture shot {position} "
                    "must alternate between zoom-in and zoom-out.",
                    "Consecutive same-direction moves would make a single "
                    "long push or pull rather than a cadence.",
                    "Alternate the move directions within this shot and "
                    "rebuild.",
                )
            if (previous_end is not None
                    and not math.isclose(start_zoom, previous_end,
                                         rel_tol=0.0, abs_tol=1e-4)):
                raise ReelLookRefused(
                    f"Ken Burns moves on picture shot {position} do not "
                    "meet at the same scale; the camera would jump between "
                    "anchored spans.",
                    "Each later window must begin at the scale the prior "
                    "window holds when it ends.",
                    "Match the adjacent scale endpoints and rebuild.",
                )
            window = _motion_window_frames(spec, placement, fps)
            _assert_subtle_body_move(
                spec, position, window, fps, allow_mid=False)
            if window["first_frame"] <= previous_last:
                raise ReelLookRefused(
                    f"Ken Burns windows overlap inside picture shot "
                    f"{position} at frame {window['first_frame']}; move "
                    "spans must remain separate.",
                    "Overlapping camera curves would compose as one move "
                    "and obscure the intended cadence.",
                    "Move the word anchors so the windows are ordered and "
                    "disjoint, then rebuild.",
                )
            windows.append({
                **window,
                "zoom_start": start_zoom,
                "zoom_end": end_zoom,
            })
            previous_last = window["last_frame"]
            previous_end = end_zoom
            previous_direction = direction

        effect["_preset"] = ordered[0]["effect_type"]
        effect["zoom_windows"] = windows

    return {
        "tracks": tracks,
        "fusion_effects": {"per_clip": per_clip, "transitions": []},
    }


def apply_comps(manifest: dict, project_folder: str,
                resolve_project_name: str, timeline_name: str,
                python_executable: Optional[str] = None,
                step_id: str = "build_reels") -> bool:
    """Run the Fusion pass over the reel's own timeline, in its own process.

    AGENTS.md 5: never create a timeline and use `ImportFusionComp` in
    the same Python process.  The reel's timeline was created by the
    caller, so this is a subprocess, and it is handed the project and
    timeline it must find current - `apply_fusion_comps` refuses every
    mutation on a mismatch rather than writing comps onto the captain's
    rough cut.
    """
    if not (manifest.get("fusion_effects", {}).get("per_clip")):
        return True
    from library.tools.project_layout import Area, ProjectLayout

    scratch = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "reel_look")
    os.makedirs(scratch, exist_ok=True)
    manifest_path = os.path.join(
        scratch, f"{_slug(timeline_name)}_fusion_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    module = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "execution", "apply_fusion_comps.py")
    result = subprocess.run(
        [python_executable or sys.executable, module, manifest_path,
         "--project-folder", project_folder,
         "--expected-project", resolve_project_name,
         "--expected-timeline", timeline_name,
         "--step-id", step_id],
        capture_output=True, encoding="utf-8", check=False)
    if result.stdout:
        print(result.stdout, file=sys.stderr)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def fit_picture_spec(project_folder: str, tracks, reel: int, scale: float,
                     *, fps: float, draw_gain: float,
                     move_rows: Sequence[str] = (),
                     skip_prefixes: Sequence[str] = (),
                     frame: tuple[int, int] = (1080, 1920)) -> dict:
    """The `touch-reel` spec that puts a built reel's TV picture at ``scale``.

    Three parts, one touch: the camera rows under the frame zoom and
    close in on the frame's centre (`row_shift.scale_spec`); each
    ``Frame`` item's pixels are SWAPPED for the overlay drawn at
    ``scale`` inside an opaque black surround (`frame_overlay_segments`),
    keeping the item's own transform; ``move_rows`` - graphics riding on
    the picture - keep their size and their place on it. ``scale`` is
    ABSOLUTE, as ``pipeline.tv_frame.scale`` states it; the reel is
    taken to be at the project's declared scale now.
    """
    from library.tools import row_shift
    from library.tools.timeline_layout import FRAME_NAME
    from library.tools.tv_frame import look_scale, resolve_tv_frame

    look = resolve_tv_frame(project_folder)
    if look is None:
        raise row_shift.RowShiftError(
            "the project declares no TV frame (pipeline.tv_frame)")
    factor = float(scale) / look_scale(look)
    anchor = (frame[0] / 2, frame[1] / 2 + int(look.get("offset_y") or 0))
    spec = row_shift.scale_spec(
        tracks, reel, [row_shift.PICTURE], factor, move_rows=move_rows,
        anchor=anchor, frame=frame, draw_gain=draw_gain,
        skip_prefixes=skip_prefixes)
    frame_row = next(t for t in tracks
                     if str(t.get("type", "")).lower().startswith("v")
                     and t.get("name") == FRAME_NAME)
    items = list(frame_row.get("clips") or ())
    runs = [(int(c["record_in"]), int(c["record_in"]) + int(c["duration"]))
            for c in items]
    segments = frame_overlay_segments(dict(look, scale=float(scale)), runs,
                                      fps, frame[0], frame[1],
                                      project_folder)
    spec["edits"] += [{"op": "swap_pixels",
                       "row": f"V{int(frame_row['index'])}", "item": index,
                       "media": segment["overlay_path"]}
                      for index, segment in enumerate(segments)]
    return spec
