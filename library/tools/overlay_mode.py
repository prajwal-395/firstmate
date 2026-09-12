"""How a project chooses to carry its caption overlays, if it chooses.

Two independent axes, both defaulting to tight. The default was full
canvas until 2026-09-10, when the captain reversed it ("the media
graphics are not tightbox and are full frame ... please fix this"):
tight boxes are smaller, faster and movable after the fact, and the
minimum canvas height (`tight_box.MIN_CANVAS_HEIGHT`) keeps every box
inside Resolve's Pan/Tilt rail, with the clamp gate and the
ink-touches-edge guard still refusing what cannot be placed. A tight
caption is rendered natively at the constant structural canvas
(`tight_box.constant_caption_box`) - no probe, no crop. See the test
module for the declaration shape; resolvers below are what the steps
call.
"""

from __future__ import annotations

from typing import Optional

GEOMETRIES = ("full", "tight")
"""What the render draws: the delivery frame, or only the drawn bounds."""

#: What a rendered overlay artefact IS. Bumped whenever the CARRIAGE
#: changes, and digested into the caption reuse key
#: (`step_4_05_render_subtitles._reuse_key`) so an artefact produced by
#: a previous carriage is UNUSABLE rather than merely stale. The
#: `frame-baked-1` era baked the position into delivery-frame pixels
#: and placed with no transform; `tight-480-1` renders the tight
#: canvas (floored at `tight_box.MIN_CANVAS_HEIGHT`) and carries it on
#: Scaling/Pan/Tilt Resolve holds inside its measured 3840 rail
#: (`library/tools/tight_box.py`); `tight-480-2` computed those
#: placements under a "2x draw gain" that does not exist, so every
#: one of its sidecars carries HALF the Tilt its artefact needs;
#: `tight-480-3` computes them from the one measured law
#: (`library/tools/resolve_transform.py`, gain 1, re-measured on 16
#: rendered plates across two builds). A `tight-480-2` sidecar
#: restored verbatim would draw its caption ~108px high, so the bump
#: retires it exactly as it retired `tight-480-1`. `tight-480-4` is
#: the CODEC: the artefact is QuickTime Animation RGBA rather than
#: ProRes 4444 (`library/tools/overlay_carriage.py`), and Resolve
#: needs a clip attribute for it that a ProRes artefact must NOT be
#: given, so the two are not interchangeable and the key has to be
#: able to tell them apart.
#:
#: A BUMP DOES NOT MEAN A RE-RENDER, and that is a deliberate ruling
#: rather than an omission. What the key promises is a PICTURE, and
#: `qtrle` is lossless over the 8-bit RGBA a Chromium render produces
#: - measured bit-exact on 80 of 80 of the captain's own overlays - so
#: an existing artefact transcoded in place IS the picture its recorded
#: key names, reached by a cheaper route than drawing it again (~0.2s a
#: file against ~4.4s to re-render one). `overlay_carriage` VERIFIES
#: that frame by frame over the whole file before it replaces anything,
#: and leaves the original untouched where it does not hold, so the
#: transcode is checked rather than assumed. What would make it
#: illegitimate is a carriage change that moved a pixel; this one does
#: not. `scripts/migrate_overlay_carriage.py` is the migration.
OVERLAY_CARRIAGE = "tight-480-4"

CONTAINERS = ("video", "frames")
"""What reaches Resolve: one stitched mov, or the PNG sequence itself."""

DEFAULT_GEOMETRY = "tight"
DEFAULT_CONTAINER = "video"


def _pipeline_block(project_folder: Optional[str]) -> dict:
    if not project_folder:
        return {}
    from library.tools.brand_registry import project_pipeline_block
    return project_pipeline_block(project_folder) or {}


def resolve_overlay_geometry(project_folder: Optional[str] = None) -> str:
    """`pipeline.subtitle_overlay_geometry`: `tight` unless `full` is declared."""
    declared = (_pipeline_block(project_folder).get(
        "subtitle_overlay_geometry") or "").strip()
    if not declared:
        return DEFAULT_GEOMETRY
    if declared not in GEOMETRIES:
        raise ValueError(
            f"Unknown subtitle_overlay_geometry {declared!r}. "
            f"Known geometries: {list(GEOMETRIES)}. Declaring nothing "
            f"means {DEFAULT_GEOMETRY!r}.")
    return declared


def resolve_overlay_container(project_folder: Optional[str] = None) -> str:
    """`pipeline.subtitle_overlay_container`: `video` unless `frames` is declared."""
    declared = (_pipeline_block(project_folder).get(
        "subtitle_overlay_container") or "").strip()
    if not declared:
        return DEFAULT_CONTAINER
    if declared not in CONTAINERS:
        raise ValueError(
            f"Unknown subtitle_overlay_container {declared!r}. "
            f"Known containers: {list(CONTAINERS)}. Declaring nothing "
            f"means {DEFAULT_CONTAINER!r}.")
    return declared


def resolve_motion_graphics_geometry(
        project_folder: Optional[str] = None) -> str:
    """`pipeline.motion_graphics_overlay_geometry`: `tight` unless `full` is declared.

    The motion-graphics half of the caption geometry above, and a
    SEPARATE key on purpose: a project may want tight captions with
    full-canvas graphics, and one key for both would take that choice
    away. See `library/tools/mg_tight_box.py` for what tight bounds.
    """
    declared = (_pipeline_block(project_folder).get(
        "motion_graphics_overlay_geometry") or "").strip()
    if not declared:
        return DEFAULT_GEOMETRY
    if declared not in GEOMETRIES:
        raise ValueError(
            f"Unknown motion_graphics_overlay_geometry {declared!r}. "
            f"Known geometries: {list(GEOMETRIES)}. Declaring nothing "
            f"means {DEFAULT_GEOMETRY!r}.")
    return declared
