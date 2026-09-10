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

What the probes did NOT prove, stated plainly: the still export of the
captain's own reference DPX with those 8 nodes applied showed ~no pixel
change (0.03% of pixels moved), while `SetCDL(saturation 0)` on the same
clip rendered to black-and-white through the same export path. So the
export path renders Color page grades, and this DRX nets to near-identity
on its own reference frame in this environment - the clip reads
neon-clipped (`[255, 54, 105]` at center), which says misinterpreted DPX
gamma rather than a verified look. The node graph LANDS; the look itself
is verified per-DRX in pixels before it ships, never assumed.

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
        project_folder: str = "") -> Optional[dict[str, Any]]:
    """The project's declared PowerGrade, or None when it declares none.

    Returns `{"path": <absolute drx path>, "provenance": {...}}`.

    A project that declares nothing gets nothing: no grade is applied,
    no file is read, and the CDL-plus-Fusion halves ship exactly as
    before. An absent declaration and a refused one are different facts -
    this returns None for the first and raises `ColorPageGradeError` for
    the second.
    """
    block = _project_color_block(project_folder)
    if block is None:
        return None
    declaration = block.get("power_grade_drx")
    if declaration is None:
        return None
    if not isinstance(declaration, dict):
        raise ColorPageGradeError(
            "`color.power_grade_drx` must be a mapping carrying `path` "
            f"and `provenance`; got {declaration!r}.")
    unknown = sorted(set(declaration) - {"path", "provenance"})
    if unknown:
        raise ColorPageGradeError(
            f"`color.power_grade_drx` carries {unknown}, which nothing "
            "reads. Known: `path`, `provenance`.")
    path = declaration.get("path")
    if not path or not isinstance(path, str):
        raise ColorPageGradeError(
            "`color.power_grade_drx` declares no `path`. A grade nobody "
            "can find is a grade nobody can review.")
    provenance = declaration.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ColorPageGradeError(
            f"`color.power_grade_drx` for {path!r} records no provenance. "
            "A `.drx` the captain did not authorise is refused - record "
            "`source`, `authorised_by` and `licence` (AGENTS.md 11).")
    for key in ("source", "authorised_by", "licence"):
        if not provenance.get(key):
            raise ColorPageGradeError(
                f"`color.power_grade_drx` provenance for {path!r} is "
                f"missing `{key}`. Provenance with a hole is provenance "
                "nobody wrote.")
    abspath = (path if os.path.isabs(path)
               else os.path.join(project_folder, path))
    if not os.path.exists(abspath):
        raise ColorPageGradeError(
            f"`color.power_grade_drx` points at {abspath!r}, which is not "
            "on disk. A declaration naming a file that is not there "
            "refuses rather than rendering ungraded.")
    if not abspath.lower().endswith(".drx"):
        raise ColorPageGradeError(
            f"`color.power_grade_drx` points at {abspath!r}, which is not "
            "a `.drx`. `ApplyGradeFromDRX` reads stills; anything else "
            "fails inside Resolve with no message worth forwarding.")
    return {"path": abspath, "provenance": dict(provenance)}


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
