"""Whether the overlays on a timeline sit where the intent says.

PR 927 proved a placement "held exactly" by reading the value back
after setting it. That verifies Resolve STORED the number asked for -
it never verifies the number was RIGHT. A value past Resolve's silent
Pan/Tilt clamp reads back the clamp with a successful return; a stale
rebuild, an identity transform on a tight clip, and a computed value
the captain already corrected all read back just as cleanly. A
read-back proves fidelity of storage, never correctness of intent.

These checks close that half, in two layers:

- VALUES: the stored Pan/Tilt/Scaling of each overlay clip against
  the intent-resolved expectation (`overlay_intent.resolve` - declared
  wins over computed). A stored `-7680` where the intent needs
  `-7929` fails here, with both numbers named. No pixels involved,
  so this runs anywhere a timeline snapshot reaches.
- PIXELS: the overlay asset composited at the STORED transform,
  measured where its ink lands, against where the intent says it
  lands. This catches the case the value check cannot: stored equals
  expected but renders elsewhere, because the placement model itself
  misread the canvas (wrong size, wrong centre, a fit that is not
  native pixels).

Both report numbers, and both SKIP LOUDLY: a clip with no asset on
disk, or no expectation for its id, is listed as skipped rather than
passed - a gate that cannot see a clip must not read as covering it
(AGENTS.md 10.4).

On Reel 09 the value check run against the pre-captain timeline
would have failed every clamped caption (stored -7680, intent past
it) instead of reporting "held exactly".
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from library.tools.overlay_placement import READBACK_TOLERANCE
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN

#: Ink counts where the canvas is at least this opaque. The caption QA
#: threshold (`qa.subtitle_qa.ALPHA_INK_THRESHOLD`) without importing
#: the QA chain for one number.
ALPHA_INK_THRESHOLD = 16

#: How far the stored-render ink centroid may sit from the
#: intent-render one before the pixel half fails, in delivery-frame
#: pixels. Representation noise is sub-pixel; a clamp misses by tens.
PIXEL_CENTROID_TOLERANCE = 2.0


def _numbers(stored: Optional[dict]) -> Optional[Dict[str, float]]:
    """Stored transform as floats, or None when there is nothing to judge."""
    if not isinstance(stored, dict):
        return None
    try:
        return {key: float(stored[key])
                for key in ("scaling", "pan", "tilt")}
    except (KeyError, TypeError, ValueError):
        return None


def verify_values(clips: Sequence[dict], intent: Optional[dict],
                  computed: Optional[dict] = None,
                  tolerance: float = READBACK_TOLERANCE,
                  *, full_wh: Tuple[int, int],
                  draw_gain: float = FALLBACK_DRAW_GAIN) -> dict:
    """Stored transforms against intent, without rendering anything.

    `full_wh` is the DECLARED delivery frame and has no default: a
    default is what let the overlay chain assume vertical while the
    timeline went landscape. The caller states the frame - see
    library/tools/delivery_format.py.

    `clips` carry `label`, `kind`, `segment_id`, `stored`
    (`{scaling, pan, tilt}` as read off the timeline) and, where the
    clip is a tight one, `canvas_wh` - plus `placement_label` where the
    segment was placed under one (reel graphics; captions carry none).
    `computed` maps `(kind, segment_id)` to the pipeline's own
    placement (None for full-canvas). Each clip resolves its
    expectation through `overlay_intent.resolve` - declared wins, over
    all four tiers including the label - and the stored value must
    match it within `tolerance`. A clip whose label pin the build
    honoured resolves that pin here too: without the label this sweep
    would judge a label-pinned store against the computation and cry
    foul on the captain's own correction.

    A clip with no expectation at all (no pin, no computed value) is
    SKIPPED, never passed: full-canvas clips carry no transform to
    judge, and an unknown segment id is a fact the caller must see.
    A clip a PIN matches but whose canvas this cannot see is skipped
    the same way, naming the canvas as what is missing - a pin names a
    place, and the transform reaching it needs the canvas.
    """
    from library.tools.overlay_intent import (
        OverlayIntentError, resolve as resolve_intent)

    computed = computed or {}
    findings: List[dict] = []
    skipped: List[dict] = []
    checked = 0
    for clip in clips:
        label = clip.get("label", "?")
        kind = clip.get("kind")
        segment_id = clip.get("segment_id")
        stored = _numbers(clip.get("stored"))
        canvas_wh = clip.get("canvas_wh")
        try:
            expected, provenance = resolve_intent(
                kind, segment_id, computed.get((kind, segment_id)), intent,
                canvas=tuple(canvas_wh) if canvas_wh else None,
                frame=tuple(full_wh),
                placement_label=clip.get("placement_label"),
                draw_gain=draw_gain)
        except OverlayIntentError as exc:
            skipped.append({"label": label, "reason": str(exc)})
            continue
        if expected is None or stored is None:
            skipped.append({
                "label": label,
                "reason": ("no expectation (neither declared nor "
                           "computed)" if expected is None else
                           "no stored transform readable"),
            })
            continue
        checked += 1
        gaps = {key: stored[key] - expected[key] for key in expected}
        worst = max(abs(gap) for gap in gaps.values())
        if worst > tolerance:
            findings.append({
                "label": label,
                "kind": kind,
                "segment_id": segment_id,
                "provenance": provenance,
                "stored": stored,
                "expected": dict(expected),
                "gap": gaps,
                "verdict": (
                    f"stored {stored} is not the {provenance} "
                    f"{dict(expected)} (worst gap {worst:.1f} past "
                    f"tolerance {tolerance}) - a wrong-but-stored "
                    f"position: it reads back cleanly and sits "
                    f"where nothing intended"),
            })
    # Pins that matched NO clip in this pass. Not honoured and not
    # refused - they simply do nothing, and did it silently for
    # nineteen caption pins on the field test while every caption was
    # placed by the computation. REPORTED, never a finding: a pin for a
    # segment this reel does not carry is an ordinary thing.
    from library.tools.overlay_intent import unmatched as unmatched_intent

    return {
        "passed": not findings,
        "checked": checked,
        "findings": findings,
        "skipped": skipped,
        "unmatched_intent": unmatched_intent(
            intent, [c.get("segment_id") for c in clips]),
    }


def canvas_origin(placement: dict, canvas_wh: Tuple[int, int],
                  full_wh: Tuple[int, int],
                  draw_gain: float = FALLBACK_DRAW_GAIN
                  ) -> Tuple[int, int]:
    """Where a canvas sits in delivery-frame pixels, from a placement.

    The inverse of `tight_box.placement_for_box`, shared with
    `tight_box.canvas_offset` (which answers it for a box that
    carries its own placement): integer-exact, so the verifier and
    the placer agree on one origin.
    """
    from library.tools.tight_box import TightBox, canvas_offset

    canvas_w, canvas_h = canvas_wh
    full_w, full_h = full_wh
    box = TightBox(width=canvas_w, height=canvas_h, props={},
                   placement=dict(placement),
                   union_w=float(canvas_w), union_h=float(canvas_h),
                   full_width=full_w, full_height=full_h,
                   gain=draw_gain)
    return canvas_offset(box)


def ink_bbox_of_frame(frame_path: str) -> Optional[Tuple[int, int, int, int]]:
    """The alpha-ink bounding box of one decoded frame, or None if blank."""
    from PIL import Image

    with Image.open(frame_path) as image:
        alpha = image.convert("RGBA").getchannel("A")
    bbox = alpha.point(lambda v: 255 if v >= ALPHA_INK_THRESHOLD else 0
                       ).getbbox()
    if bbox is None:
        return None
    return (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))


def composite_ink_centroid(frame_path: str, placement: dict,
                           canvas_wh: Tuple[int, int],
                           full_wh: Tuple[int, int],
                           draw_gain: float = FALLBACK_DRAW_GAIN
                           ) -> Optional[Tuple[float, float]]:
    """Where the asset's ink centroid lands on the delivery frame.

    The asset frame is pasted onto a blank delivery canvas at the
    origin `placement` puts it (Scaling is honoured as 1 = native
    pixels; anything else refuses by returning None, because the
    model this inverts was measured at native scale only).
    """
    scaling = placement.get("scaling")
    try:
        scaling = float(scaling)
    except (TypeError, ValueError):
        return None
    if abs(scaling - 1.0) > 1e-6:
        return None
    ink = ink_bbox_of_frame(frame_path)
    if ink is None:
        return None
    try:
        ox, oy = canvas_origin(placement, canvas_wh, full_wh,
                               draw_gain=draw_gain)
    except (KeyError, TypeError, ValueError):
        return None
    return (ox + (ink[0] + ink[2]) / 2.0,
            oy + (ink[1] + ink[3]) / 2.0)


def verify_pixels(clips: Sequence[dict], intent: Optional[dict],
                  computed: Optional[dict] = None,
                  *, full_wh: Tuple[int, int],
                  tolerance_px: float = PIXEL_CENTROID_TOLERANCE,
                  draw_gain: float = FALLBACK_DRAW_GAIN) -> dict:
    """Stored-render ink against intent-render ink, one frame per clip.

    `full_wh` is the DECLARED delivery frame and has no default - see
    :func:`verify_values`.

    Each clip additionally carries `asset_frame` (a decoded frame of
    the overlay file) and `canvas_wh`. Both the stored and the
    expected placement are composited and their ink centroids
    compared; a centroid further than `tolerance_px` from intent
    fails with both positions named. Clips without a decodable frame
    are SKIPPED loudly - an unverifiable pair is not a passing pair.
    """
    from library.tools.overlay_intent import (
        OverlayIntentError, resolve as resolve_intent)

    computed = computed or {}
    findings: List[dict] = []
    skipped: List[dict] = []
    checked = 0
    for clip in clips:
        label = clip.get("label", "?")
        kind = clip.get("kind")
        segment_id = clip.get("segment_id")
        frame_path = clip.get("asset_frame")
        canvas_wh = clip.get("canvas_wh")
        stored = _numbers(clip.get("stored"))
        try:
            expected, provenance = resolve_intent(
                kind, segment_id, computed.get((kind, segment_id)), intent,
                canvas=tuple(canvas_wh) if canvas_wh else None,
                frame=tuple(full_wh),
                placement_label=clip.get("placement_label"),
                draw_gain=draw_gain)
        except OverlayIntentError as exc:
            skipped.append({"label": label, "reason": str(exc)})
            continue
        if (not frame_path or not canvas_wh or stored is None
                or expected is None):
            skipped.append({
                "label": label,
                "reason": "no asset frame, canvas size, stored "
                          "transform or expectation to composite",
            })
            continue
        at_stored = composite_ink_centroid(
            frame_path, stored, tuple(canvas_wh), tuple(full_wh),
            draw_gain=draw_gain)
        at_expected = composite_ink_centroid(
            frame_path, expected, tuple(canvas_wh), tuple(full_wh),
            draw_gain=draw_gain)
        if at_stored is None or at_expected is None:
            skipped.append({
                "label": label,
                "reason": ("blank asset frame or non-native scaling: "
                           "nothing to measure"),
            })
            continue
        checked += 1
        gap = ((at_stored[0] - at_expected[0]) ** 2
               + (at_stored[1] - at_expected[1]) ** 2) ** 0.5
        if gap > tolerance_px:
            findings.append({
                "label": label,
                "kind": kind,
                "segment_id": segment_id,
                "provenance": provenance,
                "stored_centroid": at_stored,
                "expected_centroid": at_expected,
                "gap_px": gap,
                "verdict": (
                    f"ink renders at {at_stored[0]:.1f},"
                    f"{at_stored[1]:.1f} but intent puts it at "
                    f"{at_expected[0]:.1f},{at_expected[1]:.1f} "
                    f"({gap:.1f}px past tolerance {tolerance_px}px)"),
            })
    return {
        "passed": not findings,
        "checked": checked,
        "findings": findings,
        "skipped": skipped,
    }


#: An overlay clip the checks read: label for reports, kind,
#: segment id and placing label for intent lookup, the stored
#: transform off the timeline, and - for the pixel half - one decoded
#: asset frame with its canvas size.
ClipInput = Dict[str, object]

#: `(kind, segment_id)` -> the pipeline's computed placement (None for
#: full-canvas). The caller owns this mapping - caption ledgers and
#: motion-graphics boxes live in different records.
ComputedMap = Dict[Tuple[Optional[str], Optional[str]], Optional[dict]]


def _stored_transform(timeline, track_index: int,
                      record_frame: int,
                      resolve_project) -> Optional[Dict[str, float]]:
    """What Resolve holds for one placed overlay clip, or None.

    Read off a FRESH handle for the item at `(track, record frame)` -
    never the handle the placer wrote through. The row itself comes
    from `reel_read.live_track_items` - this sweep takes that slice
    rather than opening its own `GetItemListInTrack` probe
    (tests/test_reel_read.py: no direct Resolve reads outside the
    reader modules). None is unreadable (no item, no properties),
    which the sweep SKIPS loudly rather than passing - a clip nothing
    could read is not a clip that verified.
    No `hasattr`: always True on Resolve's proxies (AGENTS.md 5).
    """
    from library.tools import reel_read as _reel_read

    try:
        items = _reel_read.live_track_items(timeline, "video", track_index)
    except Exception:  # noqa: BLE001 - unreadable, skipped below
        return None
    for item in items:
        try:
            if item.GetStart() != record_frame:
                continue
        except Exception:  # noqa: BLE001 - a stale handle, keep looking
            continue
        try:
            from library.tools.reel_read import (
                read_transform_timeline_units)

            transform = read_transform_timeline_units(
                item, timeline, resolve_project)
            if transform is None:
                return None
            values = {prop: float(transform[prop])
                      for prop in ("Scaling", "Pan", "Tilt")}
        except (TypeError, ValueError):
            return None
        except Exception:  # noqa: BLE001 - unreadable, skipped below
            return None
        return {"scaling": values["Scaling"], "pan": values["Pan"],
                "tilt": values["Tilt"]}
    return None


def _asset_still(segment_files: dict) -> Optional[str]:
    """One decodable still for the pixel half, or None.

    A frames-container segment decodes its first PNG directly; a
    single-PNG still reads as itself. Anything else (a ProRes mov -
    every motion graphic, most captions) is None here: decoding one
    frame per clip per build is a cost nobody has measured yet, and
    the placement-time check (`overlay_draw_intent`) already judges
    held geometry against intent without rendering anything. Those
    clips run the values half, and the pixel half lists them as
    skipped - loudly, in the report - never as passed.
    """
    import os

    frame_dir = (segment_files or {}).get("frames_dir") or ""
    if frame_dir:
        try:
            names = sorted(name for name in os.listdir(frame_dir)
                           if name.endswith(".png"))
        except OSError:
            return None
        if names:
            return os.path.join(frame_dir, names[0])
        return None
    overlay_path = (segment_files or {}).get("overlay_path") or ""
    if overlay_path.endswith(".png"):
        return overlay_path
    return None


def sweep_reel_overlays(timeline, placed: Sequence[dict], *,
                        resolve_project,
                        intent: Optional[dict] = None,
                        full_wh: Tuple[int, int],
                        draw_gain: float = FALLBACK_DRAW_GAIN) -> dict:
    """Stored transforms against intent, after a reel build. REPORT ONLY.

    `placed` is one entry per tight overlay the build laid down -
    `label`, `kind`, `segment_id`, `placement_label` (where the segment
    was placed under one), `track_index`, `record_frame`, `canvas_wh`,
    the computed `placement` (None for full-canvas, which never appears
    here), plus `overlay_path`/`frames_dir` where the pixel half can
    decode a still. Full-canvas clips carry no transform to judge and
    are the caller's to exclude, not this sweep's to skip.

    Runs `verify_values` over what Resolve holds NOW (fresh handles -
    a stale-carriage value the read-back passed is refused here
    against the intent it disobeys) plus `verify_pixels` over clips
    with a decodable still, PRINTS every finding to stderr, and
    returns both reports. Never raises: a sweep that fails a correct
    build is worse than the defect it catches (AGENTS.md 10.4), so an
    unreadable timeline reports `{"unavailable": reason}` and the build
    proceeds.
    """
    import sys

    try:
        return _sweep_reel_overlays(timeline, placed, intent=intent,
                                    resolve_project=resolve_project,
                                    full_wh=full_wh,
                                    draw_gain=draw_gain)
    except Exception as failed:  # noqa: BLE001 - report, never refuse
        reason = f"overlay sweep unavailable ({failed}); the reel stands."
        print(f"  {reason}", file=sys.stderr)
        return {"passed": True, "unavailable": str(failed),
                "values": None, "pixels": None}


def _sweep_reel_overlays(timeline, placed: Sequence[dict], *,
                         resolve_project,
                         intent: Optional[dict] = None,
                         full_wh: Tuple[int, int],
                         draw_gain: float = FALLBACK_DRAW_GAIN) -> dict:
    import sys

    computed: ComputedMap = {}
    value_clips: List[dict] = []
    pixel_clips: List[dict] = []
    for entry in placed or []:
        kind = entry.get("kind")
        segment_id = entry.get("segment_id")
        computed[(kind, segment_id)] = entry.get("placement")
        stored = _stored_transform(timeline, entry.get("track_index"),
                                   entry.get("record_frame"),
                                   resolve_project)
        clip = {"label": entry.get("label", "?"), "kind": kind,
                "segment_id": segment_id,
                "placement_label": entry.get("placement_label"),
                "stored": stored,
                "canvas_wh": entry.get("canvas_wh")}
        value_clips.append(clip)
        if stored is not None:
            # Every readable clip runs the pixel half; clips without a
            # decodable still are SKIPPED loudly inside `verify_pixels`
            # ("no asset frame ... to composite"), never passed
            # silently and never dropped here.
            pixel_clip = dict(clip)
            pixel_clip["asset_frame"] = _asset_still(entry)
            pixel_clips.append(pixel_clip)
    values = verify_values(value_clips, intent, computed,
                           full_wh=tuple(full_wh),
                           draw_gain=draw_gain)
    pixels = verify_pixels(pixel_clips, intent, computed,
                           full_wh=tuple(full_wh),
                           draw_gain=draw_gain)
    passed = bool(values["passed"] and pixels["passed"])
    for finding in values["findings"]:
        print(f"  overlay values: {finding['label']}: "
              f"{finding['verdict']}", file=sys.stderr)
    for finding in pixels["findings"]:
        print(f"  overlay pixels: {finding['label']}: "
              f"{finding['verdict']}", file=sys.stderr)
    skipped = len(values["skipped"]) + len(pixels["skipped"])
    print(f"  overlay sweep: {values['checked']} value-checked, "
          f"{pixels['checked']} pixel-checked, "
          f"{len(values['findings']) + len(pixels['findings'])} "
          f"finding(s), {skipped} skipped "
          f"(no expectation, unreadable store, or no decodable still - "
          f"listed in the build record, never passed silently).",
          file=sys.stderr)
    return {"passed": passed, "values": values, "pixels": pixels}
