"""Which picture a QA check needs, and so whether it needs Resolve to render.

One enumeration. Every QA check this repository runs is classified here by
the FIDELITY of the picture its question needs, and only one class makes
Resolve render anything:

- ``structural``: answered from structure - the manifest, a timeline
  read-back, a comp file, a plan. No pixels at all.
- ``source_pixel``: answered from the pixels of the SOURCE media file
  ("is this the right footage?"). ffmpeg reads the file; Resolve is not
  asked.
- ``generated_asset``: answered from a file this pipeline rendered itself
  (Remotion, hyperframes, a bookend card). ffmpeg reads the file.
- ``resolve_composite``: answered only by the picture Resolve composites -
  grade, Fusion transition or effect, transform, a caption over its
  background. This is the one class that may queue a Resolve render, and
  `segment_renderer.render_batch` renders every such request of a pass in
  ONE batch.

A check whose question changes with its subject is classified per
instance: `clip_placement` on a bookend card is a `generated_asset` (the
card is a file this pipeline rendered), on cut footage it is a
`source_pixel`. That is `frame_check_fidelity`.

A check that reads an EXISTING export (`render_qa`, `render_watch`) is
`resolve_composite` by what it needs, and queues nothing: the export it
reads is the delivery render, which happens whether or not QA runs.
"""
from __future__ import annotations

STRUCTURAL = "structural"
SOURCE_PIXEL = "source_pixel"
GENERATED_ASSET = "generated_asset"
RESOLVE_COMPOSITE = "resolve_composite"

FIDELITIES = (STRUCTURAL, SOURCE_PIXEL, GENERATED_ASSET, RESOLVE_COMPOSITE)

# check -> (fidelity, where it lives). The router's frame/segment check
# types are the keys `visual_qa_router.plan_qa_checks` emits; the rest are
# the other QA surfaces, so the whole set reads in one place.
CHECKS = {
    # visual_qa_router.plan_qa_checks - frame grabs and segment checks
    "clip_placement": (SOURCE_PIXEL, "library/tools/visual_qa_router.py"),
    "transition": (RESOLVE_COMPOSITE, "library/tools/visual_qa_router.py"),
    "vfx": (RESOLVE_COMPOSITE, "library/tools/visual_qa_router.py"),
    "color_grade": (RESOLVE_COMPOSITE, "library/tools/visual_qa_router.py"),
    # "readable against the background" is a question about the composite;
    # its structural halves are `subtitle_qa` and `overlay_verify` below.
    "subtitle": (RESOLVE_COMPOSITE, "library/tools/visual_qa_router.py"),
    # letterbox / head cropped off: the transform Resolve applied
    "perceptual": (RESOLVE_COMPOSITE, "library/tools/perceptual_qa.py"),
    # timeline_qa stations: read-backs off the live timeline, no render
    "timeline_clip_placement": (STRUCTURAL, "library/tools/timeline_qa.py"),
    "timeline_transitions": (STRUCTURAL, "library/tools/timeline_qa.py"),
    "timeline_color_grades": (STRUCTURAL, "library/tools/timeline_qa.py"),
    "timeline_audio": (STRUCTURAL, "library/tools/timeline_qa.py"),
    "timeline_fusion_comps": (STRUCTURAL, "library/tools/timeline_qa.py"),
    "timeline_sync": (STRUCTURAL, "library/tools/qa/timeline_sync_qa.py"),
    "transform_drift": (STRUCTURAL, "library/tools/transform_drift.py"),
    "manifest_validation": (STRUCTURAL, "library/tools/manifest_validator.py"),
    "subtitle_plan": (STRUCTURAL, "library/tools/qa/subtitle_qa.py"),
    "overlay_values": (STRUCTURAL, "library/tools/overlay_verify.py"),
    "treatment": (STRUCTURAL, "library/tools/treatment_verify.py"),
    # files this pipeline rendered, read off disk
    "overlay_pixels": (GENERATED_ASSET, "library/tools/overlay_verify.py"),
    "asset_alpha": (GENERATED_ASSET, "library/tools/qa/asset_qa.py"),
    # the composite, read off an export or the gallery - queue no QA render
    "render_file": (RESOLVE_COMPOSITE, "library/tools/render_qa.py"),
    "render_watch": (RESOLVE_COMPOSITE, "library/tools/render_watch.py"),
    "gate_stills": (RESOLVE_COMPOSITE, "library/tools/gate_stills.py"),
}


def fidelity_of(check: str) -> str:
    """The fidelity a named check needs. An unclassified check raises."""
    return CHECKS[check][0]


def frame_check_fidelity(check_type: str, clip: dict | None = None) -> str:
    """The fidelity of one planned frame check, given the clip it is on."""
    fidelity = fidelity_of(check_type)
    if fidelity == SOURCE_PIXEL and clip is not None and clip.get("bookend"):
        return GENERATED_ASSET
    return fidelity


def needs_resolve_render(fidelity: str) -> bool:
    """Only the composite is worth a Resolve render."""
    if fidelity not in FIDELITIES:
        raise ValueError(f"unknown QA fidelity {fidelity!r}")
    return fidelity == RESOLVE_COMPOSITE
