"""A project's PowerGrade, applied to timeline clips through the Color page.

The captain's ruling, 2026-09-10: the creative look belongs on the Color
page as real nodes, delivered through `.drx` files - *"that is how i
intended that we implement it in the first place"*. The earlier
no-PowerGrade rule was firstmate being over-cautious about a third-party
gift with no written licence, and the captain has overruled it: the asset
is theirs and the call is theirs.

What the probes proved, on a scratch timeline and never the captain's
(`SCRATCH_grade_probe`, 2026-09-10):

* `Gallery.CreateGalleryPowerGradeAlbum()` returns a new
  `GalleryStillAlbum`.
* `GalleryStillAlbum.ImportStills([drx_path])` returns True and the still
  appears in `GetStills()`.
* `TimelineItem.GetNodeGraph().ApplyGradeFromDRX(path, 0)` returns True,
  and a re-fetched graph reads back 8 real nodes - `Input` carrying
  `OFX: Color Space Transform`, `BAL/EXP`, `CONTRAST`, `SAT`,
  `W&B` carrying `OFX: Chromatic Adaptation`, `Output` carrying
  `OFX: Color Space Transform`, `FLC` carrying `OFX: Film Look Creator`,
  `Corrections`. A `.drx` is XML wrapping a hex-encoded binary
  `FieldsBlob`, so authoring one by hand is impractical - but IMPORTING
  one and APPLYING it are both scriptable, and both were proved by
  calling, never by `dir()` (which is unreliable on Resolve's scripting
  proxies - see `library/tools/marker_feedback.py`).

**A NODE COUNT IS NOT A GRADE, and this one is the proof.** `applied:
True, nodes: 8` below is true of `The Grade Free 1.13.1.drx` and means
nothing: measured on Reel 09 frame 300 through a one-frame Deliver
render, bypassing each node in turn, `BAL/EXP`, `CONTRAST`, `W&B`,
`FLC` and `Corrections` each move EXACTLY ZERO pixels, `SAT` moves 49
at max delta 1, and the two Color Space Transforms are an exact
inverse pair (554 px when both are bypassed together). The whole grade
nets to 486 px of 2,073,600 - 0.023% - while `SetCDL(saturation 0)` on
the same clip in the same session moves 1,435,016 px (69.2%). The
export path renders Color page grades perfectly; that file has no
grade in it, which is exactly what its own README says it is: *"a
STARTING POINT, not a finishing point ... Do not apply and export
without adjusting."*

So the look is verified per-DRX in EXPORTED PIXELS before it ships,
never assumed from a node count - and `cdl_node` below is how a
project's own declared CDL lands inside such a graph rather than being
displaced by it. Full measurement, and the withdrawal of an earlier
28.9% taken through a capture route that writes no file on this build:
`docs/RULE_EVIDENCE.md#the-powergrade-with-no-grade-in-it`.

How a DRX reaches a run:

* A project declares it under top-level `color:` in its own
  `project.yaml` - project scope only, never a brand template and never
  a house default (AGENTS.md 14: a template may set parameters, never
  artwork; a finished node tree is artwork):

      color:
        power_grade_drx:
          path: "brand_assets/example_look.drx"  # project-relative
          provenance:
            source: "Built in the Resolve GUI from ..."
            authorised_by: "captain, 2026-09-10"
            licence: "captain's own asset"

* The declaration is READ by `resolve_color_page_grade` and APPLIED by
  `apply_power_grade`, which step 6.01 calls per clip after `SetCDL`.
  Applying a DRX REPLACES the clip's whole node graph - including the
  node `SetCDL` just wrote - so a declared DRX is the grade, not an
  addition to it. The CDL half still ships in the manifest either way,
  because a run without Resolve (or without the file) must still say
  what the grade was.

* Provenance is REQUIRED, not decorative. A declaration naming a path
  with no recorded source and authorisation is REFUSED - that is the
  replacement for the withdrawn no-drx rule: the old test failed on any
  `.drx` anywhere; the new rule fails on a `.drx` nobody authorised.
  `tests/test_color_page_grade.py`, `tests/test_color_grade_delivery.py`.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional


class ColorPageGradeError(ValueError):
    """A `color.power_grade_drx` declaration that cannot be applied.

    Raised rather than dropped and rather than applied halfway. A dropped
    grade ships a video missing the look somebody asked for, forty
    minutes into an unattended run; a halfway one - CDL without the
    nodes, or nodes without provenance - is a grade nobody can review.
    Same shape as `bookends` (AGENTS.md 13): raise, never drop.
    """


#: gradeMode passed to `Graph.ApplyGradeFromDRX`. 0 is "No keyframes":
#: the grade lands as plain node values, which is what a per-clip
#: pipeline application wants. 1 ("Source Timecode aligned") and 2
#: ("Start Frames aligned") carry keyframes across, which is a GUI
#: conform concern, not a render concern.
GRADE_MODE_NO_KEYFRAMES = 0


def _project_color_block(project_folder: str) -> Optional[Mapping[str, Any]]:
    """The top-level `color:` mapping of a project's project.yaml."""
    if not project_folder:
        return None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return None
    try:
        import yaml
    except ImportError:
        raise ColorPageGradeError(
            "PyYAML is required to read project.yaml for "
            "`color.power_grade_drx`.")
    with open(project_yaml, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ColorPageGradeError(
            f"{project_yaml} does not parse as a mapping.")
    block = config.get("color")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ColorPageGradeError(
            f"{project_yaml} has a `color:` block that is not a mapping, "
            f"got {type(block).__name__}.")
    return block


def resolve_color_page_grade(
        project_folder: str = "", reel=None,
        video_preferences=None) -> Optional[dict[str, Any]]:
    """The project's declared PowerGrade, or None when it declares none.

    Returns `{"path": <absolute drx path>, "provenance": {...}}`.

    Precedence: the merged video preferences' `color_grade`
    (``style.yaml`` locked value, then ``video.yaml`` shared and
    per-reel layers)  >  the project.yaml `color.power_grade_drx`
    block. A video that declares nothing reads exactly what it always
    read. `reel` is the addressed reel where the caller has one;
    `video_preferences` is an already-merged mapping that wins over a
    disk read.

    A project that declares nothing gets nothing: no grade is applied,
    no file is read, and the CDL-plus-Fusion halves ship exactly as
    before. An absent declaration and a refused one are different facts -
    this returns None for the first and raises `ColorPageGradeError` for
    the second.
    """
    from library.tools import video_prefs as _video_prefs
    preferred = _video_prefs.effective_color_grade(
        project_folder or "", reel=reel,
        video_preferences=video_preferences)
    if preferred is not None:
        return _resolve_grade_mapping(
            preferred, project_folder or "",
            "video preferences `color_grade`")
    block = _project_color_block(project_folder)
    if block is None:
        return None
    declaration = block.get("power_grade_drx")
    if declaration is None:
        return None
    return _resolve_grade_mapping(
        declaration, project_folder or "",
        "project.yaml `color.power_grade_drx`")


def _resolve_grade_mapping(
        declaration: dict, project_folder: str, where: str
        ) -> Optional[dict[str, Any]]:
    """A `power_grade_drx`-shaped mapping resolved to disk, or None.

    `where` names the declaration for refusals (the project.yaml
    `color:` block or a video-preferences file). Shared by the
    project.yaml read below and the video-preferences read above it,
    so the two cannot disagree about what a grade declaration means.
    """
    if not isinstance(declaration, dict):
        raise ColorPageGradeError(
            f"{where} must be a mapping carrying `path` "
            f"and `provenance`; got {declaration!r}.")
    unknown = sorted(set(declaration) - {"path", "provenance", "cdl_node"})
    if unknown:
        raise ColorPageGradeError(
            f"{where} carries {unknown}, which nothing "
            "reads. Known: `path`, `provenance`, `cdl_node`.")
    cdl_node = declaration.get("cdl_node")
    if cdl_node is not None and (not isinstance(cdl_node, str)
                                 or not cdl_node.strip()):
        raise ColorPageGradeError(
            f"{where} declares `cdl_node` {cdl_node!r}, "
            "which is not a node label. It must NAME a node in the "
            "graph the `.drx` builds - the label is matched, never a "
            "node index, because an index means a different node in "
            "every grade.")
    path = declaration.get("path")
    if not path or not isinstance(path, str):
        raise ColorPageGradeError(
            f"{where} declares no `path`. A grade nobody "
            "can find is a grade nobody can review.")
    provenance = declaration.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ColorPageGradeError(
            f"{where} for {path!r} records no provenance. "
            "A `.drx` the captain did not authorise is refused - record "
            "`source`, `authorised_by` and `licence` (AGENTS.md 11).")
    for key in ("source", "authorised_by", "licence"):
        if not provenance.get(key):
            raise ColorPageGradeError(
                f"{where} provenance for {path!r} is "
                f"missing `{key}`. Provenance with a hole is provenance "
                "nobody wrote.")
    abspath = (path if os.path.isabs(path)
               else os.path.join(project_folder, path))
    if not os.path.exists(abspath):
        raise ColorPageGradeError(
            f"{where} points at {abspath!r}, which is not "
            "on disk. A declaration naming a file that is not there "
            "refuses rather than rendering ungraded.")
    if not abspath.lower().endswith(".drx"):
        raise ColorPageGradeError(
            f"{where} points at {abspath!r}, which is not "
            "a `.drx`. `ApplyGradeFromDRX` reads stills; anything else "
            "fails inside Resolve with no message worth forwarding.")
    return {"path": abspath, "provenance": dict(provenance),
            "cdl_node": cdl_node.strip() if cdl_node else None}


def resolve_power_grade_mapping(
        declaration: dict, project_folder: str, where: str
        ) -> Optional[dict[str, Any]]:
    """Validate and resolve a PowerGrade declaration from another owner.

    The edit ledger stores the same path and provenance contract as
    ``project.yaml``. Keeping resolution here means those two declaration
    routes cannot drift on authorization, file existence, or extension.
    """
    return _resolve_grade_mapping(declaration, project_folder, where)


def apply_power_grade(timeline_item: Any, drx_path: str) -> dict[str, Any]:
    """Apply a PowerGrade file to one timeline item's node graph.

    The proven call sequence (`SCRATCH_grade_probe`, 2026-09-10):
    `item.GetNodeGraph().ApplyGradeFromDRX(path, 0)`, then re-fetch the
    graph and read back `GetNumNodes()` - the handle goes stale across
    the apply, so the count must be read off a fresh one.

    Returns a record carrying `applied`, the `nodes` read back (None
    where the graph could not be re-read - said, not zeroed), the `path`
    applied, and, where it did not apply, `reason`. A `False` return from
    Resolve is REPORTED in the record, never raised: one clip's grade
    failing must not stop the render, and the warning names the clip.
    A declaration problem (no file, no provenance) raises in
    `resolve_color_page_grade` before this is ever called.
    """
    record: dict[str, Any] = {"path": drx_path, "applied": False,
                              "nodes": None}
    try:
        graph = timeline_item.GetNodeGraph()
    except Exception as exc:  # noqa: BLE001 - reported on the record
        record["reason"] = f"GetNodeGraph raised: {exc}"
        return record
    if graph is None:
        record["reason"] = "Resolve returned no node graph for this item."
        return record
    try:
        applied = graph.ApplyGradeFromDRX(
            drx_path, GRADE_MODE_NO_KEYFRAMES)
    except Exception as exc:  # noqa: BLE001 - reported on the record
        record["reason"] = f"ApplyGradeFromDRX raised: {exc}"
        return record
    if not applied:
        record["reason"] = (
            "ApplyGradeFromDRX returned False. The file parsed as a "
            "still but the grade did not land - re-export it from the "
            "Color page GUI and re-stage it.")
        return record
    record["applied"] = True
    try:
        record["nodes"] = timeline_item.GetNodeGraph().GetNumNodes()
    except Exception as exc:  # noqa: BLE001 - said, not zeroed
        record["nodes"] = None
        record["nodes_unreadable_because"] = str(exc)
    return record


def node_index_by_label(timeline_item: Any, label: str) -> Optional[int]:
    """The 1-based index of the node LABELLED `label`, or None.

    Measured on `Podcast (field test)` / Reel 09, 2026-09-10: after
    `ApplyGradeFromDRX` of `The Grade Free_1.13.1.drx`, a re-fetched
    graph reads back 8 nodes labelled `Input`, `BAL/EXP`, `CONTRAST`,
    `SAT`, `W&B`, `Output`, `FLC`, `Corrections` - the same eight, in
    the same order, that the `.drx`'s own compressed node body carries.

    A LABEL is matched, never an index, because index 2 is `BAL/EXP` in
    THIS grade and something else in the next one. A label that is not
    in the graph returns None and the caller REFUSES to place the CDL
    rather than falling back to a number: node 1 here is the Input
    colour-space transform, and writing an exposure correction into it
    would replace the grade's own input conversion.
    """
    try:
        graph = timeline_item.GetNodeGraph()
        count = graph.GetNumNodes()
    except Exception:  # noqa: BLE001 - reported by the caller as absent
        return None
    if not isinstance(count, int):
        return None
    for index in range(1, count + 1):
        try:
            if graph.GetNodeLabel(index) == label:
                return index
        except Exception:  # noqa: BLE001 - a node that will not name itself
            continue
    return None


def apply_cdl_to_node(timeline_item: Any, node_index: int,
                      cdl_values: Mapping[str, Any]) -> dict[str, Any]:
    """`SetCDL` the four terms onto ONE named node of an applied grade.

    This is where a per-clip exposure and balance correction belongs
    under a PowerGrade: `The Grade Free`'s own README says node 02
    (`BAL/EXP`) and node 04 (`W&B`) are dialled in per clip and ships
    them as identity placeholders, so the look is the graph and the
    correction is one node inside it. Proved by calling on Reel 09,
    2026-09-10: `SetCDL({"NodeIndex": "2", "Slope": "1.6 1.6 1.6"})`
    after the DRX moved the still's picture luma 46.91 -> 84.17 across
    533,240 pixels, and setting the same node back to 1.0 returned the
    frame BYTE-IDENTICAL to the DRX-only still.

    **A `True` return from `SetCDL` is not evidence the grade landed.**
    Measured the same day on the same clip: the declared
    `v04_teal_split` CDL - slope 1.03/1.0/0.96, offset
    -0.01/0.005/0.02, power 1.0, saturation 1.12 - returned True on
    every clip and rendered a still byte-identical to no grade at all
    (0 of 2,073,600 pixels moved), reproduced six times including a
    six-second settle, with `SetCDL(saturation 0)` immediately before
    and after as a positive control (601,760 pixels, max delta 154).
    Each of those four terms moves the picture ON ITS OWN. Only pixels
    settle whether a CDL arrived; this returns what Resolve said and
    the caller records it, and neither is a substitute for looking.
    """
    record: dict[str, Any] = {"node_index": node_index, "landed": None}
    payload = {
        "NodeIndex": str(node_index),
        "Slope": (f"{cdl_values.get('slope_r', 1.0):.4f} "
                  f"{cdl_values.get('slope_g', 1.0):.4f} "
                  f"{cdl_values.get('slope_b', 1.0):.4f}"),
        "Offset": (f"{cdl_values.get('offset_r', 0.0):.4f} "
                   f"{cdl_values.get('offset_g', 0.0):.4f} "
                   f"{cdl_values.get('offset_b', 0.0):.4f}"),
        "Power": (f"{cdl_values.get('power_r', 1.0):.4f} "
                  f"{cdl_values.get('power_g', 1.0):.4f} "
                  f"{cdl_values.get('power_b', 1.0):.4f}"),
        "Saturation": f"{cdl_values.get('saturation', 1.0):.4f}",
    }
    record["payload"] = payload
    try:
        record["landed"] = bool(timeline_item.SetCDL(payload))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        record["landed"] = False
        record["reason"] = f"SetCDL raised: {exc}"
    return record
