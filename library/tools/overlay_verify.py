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
RENDERED_BOX_INK_THRESHOLD = 64
RENDERED_BOX_MARGIN_PX = 32

#: How far the stored-render ink centroid may sit from the
#: intent-render one before the pixel half fails, in delivery-frame
#: pixels. Representation noise is sub-pixel; a clamp misses by tens.
PIXEL_CENTROID_TOLERANCE = 2.0


def _numbers(stored: Optional[dict]) -> Optional[Dict[str, float]]:
    """Stored transform as floats, or None when there is nothing to judge."""
    if not isinstance(stored, dict):
        return None
    try:
        def read(lower, upper, default=None):
            value = stored.get(lower, stored.get(upper, default))
            return float(value) if value is not None else None

        zoom = read("zoom", "Zoom", 1.0)
        values = {
            "scaling": read("scaling", "Scaling"),
            "pan": read("pan", "Pan"),
            "tilt": read("tilt", "Tilt"),
            "zoom_x": read("zoom_x", "ZoomX", zoom),
            "zoom_y": read("zoom_y", "ZoomY", zoom),
        }
        if any(values[key] is None
               for key in ("scaling", "pan", "tilt")):
            return None
        return values
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
        expected_values = _numbers(expected)
        if expected_values is None:
            skipped.append({"label": label,
                            "reason": "expected transform is unreadable"})
            continue
        gaps = {key: stored[key] - expected_values[key]
                for key in expected_values}
        worst = max(abs(gap) for gap in gaps.values())
        if worst > tolerance:
            findings.append({
                "label": label,
                "kind": kind,
                "segment_id": segment_id,
                "provenance": provenance,
                "stored": stored,
                "expected": expected_values,
                "gap": gaps,
                "verdict": (
                    f"stored {stored} is not the {provenance} "
                    f"{expected_values} (worst gap {worst:.1f} past "
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
    zoom_x = float(placement.get("zoom_x", placement.get("zoom", 1.0)))
    zoom_y = float(placement.get("zoom_y", placement.get("zoom", 1.0)))
    source_w, source_h = map(float, canvas_wh)
    centre_x, centre_y = ox + source_w / 2.0, oy + source_h / 2.0
    return (centre_x + ((ink[0] + ink[2]) / 2.0 - source_w / 2.0) * zoom_x,
            centre_y + ((ink[1] + ink[3]) / 2.0 - source_h / 2.0) * zoom_y)


def verify_rendered_boxes(resolve, project, timeline, records: Sequence[dict],
                          output_dir: str, *, tolerance_px: int = 12,
                          allow_empty: bool = False) -> dict:
    """Measure each overlay's actual rendered box against its reel box.

    Each item is rendered twice at its midpoint: once with the item
    enabled and once disabled. Their pixel difference isolates what
    that overlay actually contributed to the composited picture.
    Samples and the measured boxes are retained under pipeline output.
    """
    import os
    import subprocess
    import time
    from types import SimpleNamespace
    import uuid
    import numpy as np
    from PIL import Image
    from library.tools.segment_renderer import (
        cleanup_segment, extract_frames, render_batch,
    )

    os.makedirs(output_dir, exist_ok=True)
    findings, checked, skipped = [], 0, []
    if not records:
        return {"passed": bool(allow_empty), "checked": 0, "findings": [],
                "skipped": ([] if allow_empty else [{
                    "label": "reel",
                    "reason": "no overlay records were supplied"}]),
                "detail": ("build declared no overlay placements"
                           if allow_empty else ""),
                "output_dir": output_dir}

    def asset_alpha_bounds(record, source_frame, destination):
        """Alpha box of the exact asset frame that plays at the sample."""
        path = record.get("overlay_path")
        if not path:
            return None
        if os.path.splitext(path)[1].lower() == ".png":
            with Image.open(path) as image:
                alpha = image.convert("RGBA").getchannel("A")
                return alpha.point(
                    lambda value: 255
                    if value >= RENDERED_BOX_INK_THRESHOLD else 0
                ).getbbox()
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path,
             "-vf", f"select=eq(n\\,{int(source_frame)})",
             "-fps_mode", "passthrough", "-frames:v", "1", destination],
            capture_output=True, timeout=30, check=False)
        if result.returncode != 0 or not os.path.isfile(destination):
            return None
        with Image.open(destination) as image:
            alpha = image.convert("RGBA").getchannel("A")
            return alpha.point(
                lambda value: 255
                if value >= RENDERED_BOX_INK_THRESHOLD else 0
            ).getbbox()

    def item_asset_name(item):
        """Resolve's returned pool item name, or empty when it cannot read it."""
        try:
            pool_item = item.GetMediaPoolItem()
            if pool_item is None:
                return ""
            return str(pool_item.GetName() or "")
        except Exception:  # noqa: BLE001 - missing media is a named refusal
            return ""

    def find_overlay_item(record, sample):
        """Find the placed item by source asset and time, across video lanes.

        Resolve drops an empty planned row and compacts the lanes. The
        build's intended lane is therefore not a reliable reader index;
        source identity plus the record span is.
        """
        path = record.get("overlay_path") or ""
        expected_names = {os.path.basename(path).casefold(),
                          os.path.splitext(os.path.basename(path))[0].casefold()}
        matches = []
        try:
            track_count = int(timeline.GetTrackCount("video") or 0)
        except Exception as exc:  # noqa: BLE001 - cannot enumerate picture rows
            return None, f"video track inventory unavailable: {exc}"
        for track in range(1, track_count + 1):
            try:
                items = timeline.GetItemListInTrack("video", track) or []
            except Exception:  # noqa: BLE001 - another row can still match
                continue
            for candidate in items:
                try:
                    covers = (int(candidate.GetStart()) <= sample
                              < int(candidate.GetEnd()))
                except Exception:  # noqa: BLE001 - stale candidate handle
                    continue
                if not covers:
                    continue
                name = item_asset_name(candidate).casefold()
                if name in expected_names:
                    matches.append((track, candidate))
        if len(matches) == 1:
            return matches[0][1], matches[0][0]
        if len(matches) > 1:
            return None, f"{len(matches)} source-matched items cover frame {sample}"
        return None, f"no source-matched overlay item covers frame {sample}"

    def render_frame(frame, name, resolution):
        # Resolve treats a one-frame render range as SelectAllFrames on
        # some timelines, and two-frame H.264 segments can be too small for
        # the renderer's broken-output guard. Render a short window around
        # the requested sample, then extract only that frame.
        timeline_end = int(timeline.GetEndFrame())
        window = 12
        mark_in = max(0, min(frame - window // 2,
                             timeline_end - window))
        mark_out = mark_in + window - 1
        render_name = f"{name}-{uuid.uuid4().hex[:8]}"
        batch = render_batch(
            resolve, project, timeline, [(mark_in, mark_out)],
            output_dir=output_dir, resolution=resolution,
            name_prefix=render_name)
        if not batch.segments:
            raise RuntimeError("Resolve returned no segment for the frame")
        segment = batch.segments[0]
        if not segment.success:
            raise RuntimeError(
                f"Resolve frame render failed: {segment.error or 'unknown error'}")
        try:
            # Resolve can report a completed H.264 job before the file is
            # ready for ffmpeg to decode. Retry with a bounded backoff; a
            # genuinely missing sample still raises below.
            # Keep extraction tied to the exact marks requested from
            # Resolve. Its segment result can expose stale marks when a
            # completed render job is reused.
            extraction_segment = SimpleNamespace(
                path=segment.path, mark_in=mark_in, mark_out=mark_out,
                success=segment.success)
            for delay in (0.0, 0.5, 1.0, 2.0, 4.0, 8.0):
                extracted = extract_frames(
                    extraction_segment, [frame], output_dir)
                if extracted.get(frame):
                    return extracted[frame]
                if delay:
                    time.sleep(delay)
            # The common extractor can reject a valid H.264 sample when
            # Resolve's segment marks disagree with the requested window.
            # Decode by the request-relative index as a last, checked path.
            direct_path = os.path.join(
                output_dir, f"{render_name}-direct.png")
            direct = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", segment.path,
                 "-vf", f"select=eq(n\\,{frame - mark_in})",
                 "-fps_mode", "passthrough", "-frames:v", "1",
                 direct_path],
                capture_output=True, timeout=60, check=False)
            if (direct.returncode == 0 and os.path.isfile(direct_path)
                    and os.path.getsize(direct_path) > 0):
                return direct_path
            return None
        finally:
            cleanup_segment(segment.path)

    for ordinal, record in enumerate(records, 1):
        box = record.get("absolute_box")
        if not box or len(box) != 4:
            skipped.append({"label": record.get("label", "?"),
                            "reason": "no declared absolute box"})
            continue
        descriptor_data = record.get("geometry")
        descriptor = None
        if descriptor_data is None:
            skipped.append({"label": record.get("label", "?"),
                            "reason": "geometry descriptor is missing"})
            continue
        try:
            from library.tools.layout_model import GeometryDescriptor
            descriptor = GeometryDescriptor.from_dict(descriptor_data)
        except (KeyError, TypeError, ValueError) as exc:
            skipped.append({"label": record.get("label", "?"),
                            "reason": f"geometry descriptor is invalid: {exc}"})
            continue
        if tuple(map(float, box)) != tuple(map(float, descriptor.box)):
            findings.append({"label": record.get("label", "?"),
                             "reason": "geometry descriptor box differs "
                                       "from recorded output box"})
            continue
        if record.get("canvas_wh") and tuple(map(float, record["canvas_wh"])) \
                != tuple(map(float, descriptor.canvas)):
            findings.append({"label": record.get("label", "?"),
                             "reason": "geometry descriptor canvas differs "
                                       "from recorded asset canvas"})
            continue
        box = descriptor.box
        x, y, right, bottom = map(int, box)
        width, height = right - x, bottom - y
        if width <= 0 or height <= 0:
            skipped.append({"label": record.get("label", "?"),
                            "reason": "declared absolute box has no area"})
            continue
        start = int(record["timeline_start_frame"])
        duration = int(record["duration_frames"])
        sample = start + max(0, (duration - 1) // 2)
        source_frame = int(record.get("source_start_frame", 0)) + sample - start
        canvas_wh = record.get("canvas_wh")
        if not canvas_wh or len(canvas_wh) != 2:
            skipped.append({"label": record.get("label", "?"),
                            "reason": "no rendered asset canvas size"})
            continue
        alpha_path = os.path.join(output_dir,
                                  f"{ordinal:03d}-asset-alpha.png")
        alpha_box = asset_alpha_bounds(record, source_frame, alpha_path)
        if alpha_box is None:
            skipped.append({"label": record.get("label", "?"),
                            "reason": "rendered asset alpha frame unavailable"})
            continue
        source_w, source_h = map(float, canvas_wh)
        expected = (
            round(x + alpha_box[0] / source_w * width),
            round(y + alpha_box[1] / source_h * height),
            round(x + alpha_box[2] / source_w * width),
            round(y + alpha_box[3] / source_h * height),
        )
        # `track_index` is the Resolve API's one-based video lane index
        # as recorded by the reel builder (V1 is 1).
        item, track = find_overlay_item(record, sample)
        if item is None:
            findings.append({"label": record.get("label", "?"),
                             "frame": sample,
                             "reason": track})
            continue
        label = str(record.get("label", f"overlay-{ordinal}"))
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        render_resolution = tuple(map(int, descriptor.frame))
        enabled_path = os.path.join(output_dir, f"{ordinal:03d}-{safe}-enabled.png")
        disabled_path = os.path.join(output_dir, f"{ordinal:03d}-{safe}-disabled.png")
        diff_path = os.path.join(output_dir, f"{ordinal:03d}-{safe}-diff.png")
        try:
            item.SetClipEnabled(False)
            if item.GetClipEnabled() is not False:
                raise RuntimeError("overlay item did not read back disabled")
            disabled = render_frame(
                sample, f"{ordinal:03d}-disabled", render_resolution)
            if not disabled:
                raise RuntimeError(
                    "disabled baseline frame was not extracted "
                    f"for {label!r} at timeline frame {sample} "
                    f"from {ordinal:03d}-disabled (Resolve segment "
                    "rendered successfully but returned no extracted frame)")
            Image.open(disabled).convert("RGB").save(disabled_path)
            item.SetClipEnabled(True)
            if item.GetClipEnabled() is not True:
                raise RuntimeError("overlay item did not read back enabled")
            enabled = render_frame(
                sample, f"{ordinal:03d}-enabled", render_resolution)
            if not enabled:
                raise RuntimeError("enabled overlay frame was not extracted")
            Image.open(enabled).convert("RGB").save(enabled_path)
        finally:
            item.SetClipEnabled(True)
            if item.GetClipEnabled() is not True:
                raise RuntimeError("failed to restore overlay enabled state")
        with Image.open(enabled_path) as image:
            actual = np.asarray(image.convert("RGB"), dtype=np.int16)
        with Image.open(disabled_path) as image:
            baseline = np.asarray(image.convert("RGB"), dtype=np.int16)
        if descriptor is not None and tuple(map(int, descriptor.frame)) != (
                actual.shape[1], actual.shape[0]):
            findings.append({"label": label, "frame": sample,
                             "reason": "rendered output dimensions differ "
                                       "from geometry descriptor frame",
                             "expected_frame": descriptor.frame,
                             "rendered_frame": (actual.shape[1], actual.shape[0])})
            continue
        delta = np.max(np.abs(actual - baseline), axis=2)
        # Other clips on the same frame may be moving, especially Fusion
        # and Remotion graphics. The A/B render can then differ outside
        # the item we toggled. Measure the item's rendered footprint in a
        # neighborhood of its asset-derived box so unrelated picture
        # changes cannot fail this placement check.
        margin = max(RENDERED_BOX_MARGIN_PX, tolerance_px * 2)
        left = max(0, expected[0] - margin)
        top = max(0, expected[1] - margin)
        right = min(actual.shape[1], expected[2] + margin)
        bottom = min(actual.shape[0], expected[3] + margin)
        mask = np.zeros(delta.shape, dtype=bool)
        mask[top:bottom, left:right] = (
            delta[top:bottom, left:right] >= RENDERED_BOX_INK_THRESHOLD)
        ys, xs = np.nonzero(mask)
        if not len(xs):
            findings.append({"label": label, "frame": sample,
                             "reason": "overlay contributes no visible pixels"})
            continue
        measured = (int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                    int(ys.max()) + 1)
        Image.fromarray(np.where(mask[:, :, None], 255, 0).astype("uint8")
                        .repeat(3, axis=2)).save(diff_path)
        gaps = tuple(a - b for a, b in zip(measured, expected))
        checked += 1
        if max(abs(gap) for gap in gaps) > tolerance_px:
            findings.append({"label": label, "frame": sample,
                             "expected": expected, "measured": measured,
                             "edge_gaps": gaps, "enabled": enabled_path,
                             "disabled": disabled_path, "diff": diff_path})
    return {"passed": not findings and not skipped, "checked": checked,
            "findings": findings, "skipped": skipped,
            "output_dir": output_dir}


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
        expected_values = _numbers(expected)
        if expected_values is None:
            skipped.append({"label": label,
                            "reason": "expected transform is unreadable"})
            continue
        at_stored = composite_ink_centroid(
            frame_path, stored, tuple(canvas_wh), tuple(full_wh),
            draw_gain=draw_gain)
        at_expected = composite_ink_centroid(
            frame_path, expected_values, tuple(canvas_wh), tuple(full_wh),
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


def _row_items_by_start(timeline, track_index: int) -> Optional[dict]:
    """One listing of a row: record frame -> the FIRST item starting there.

    The row comes from `reel_read.live_track_items` - this sweep takes
    that slice rather than opening its own `GetItemListInTrack` probe
    (tests/unit/reels/test_reel_read.py: no direct Resolve reads outside the
    reader modules). Listed ONCE per row, not once per overlay: a
    reel's captions share one row, and re-listing it for each of them
    held the instance for rows x items reads where one listing serves.
    None is an unreadable row. An item whose start will not read (a
    stale handle) is passed over, as it always was.
    """
    from library.tools import reel_read as _reel_read

    try:
        items = _reel_read.live_track_items(timeline, "video", track_index)
    except Exception:  # noqa: BLE001 - unreadable, skipped below
        return None
    by_start: dict = {}
    for item in items:
        try:
            start = item.GetStart()
        except Exception:  # noqa: BLE001 - a stale handle, keep looking
            continue
        by_start.setdefault(start, item)
    return by_start


def _stored_transform(item, timeline,
                      resolve_project) -> Optional[Dict[str, float]]:
    """What Resolve holds for one placed overlay clip, or None.

    Read off a FRESH handle for the item at `(track, record frame)` -
    never the handle the placer wrote through. None is unreadable (no
    item, no properties), which the sweep SKIPS loudly rather than
    passing - a clip nothing could read is not a clip that verified.
    No `hasattr`: always True on Resolve's proxies (AGENTS.md 5).
    """
    if item is None:
        return None
    try:
        from library.tools.reel_read import (
            read_transform_timeline_units)

        transform = read_transform_timeline_units(
            item, timeline, resolve_project)
        if transform is None:
            return None
        values = {prop: float(transform[prop])
                  for prop in ("Scaling", "Pan", "Tilt")}
        values["ZoomX"] = float(transform.get("ZoomX", 1.0))
        values["ZoomY"] = float(transform.get("ZoomY", 1.0))
    except (TypeError, ValueError):
        return None
    except Exception:  # noqa: BLE001 - unreadable, skipped below
        return None
    return {"scaling": values["Scaling"], "pan": values["Pan"],
            "tilt": values["Tilt"], "zoom_x": values["ZoomX"],
            "zoom_y": values["ZoomY"]}


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

    Two halves, so a caller holding the instance can keep only the
    first under its hold (FREE -> RESOLVE -> FREE): `read_reel_overlays`
    is the Resolve read, `judge_reel_overlays` the comparison and the
    pixel compositing, which touch no Resolve state.
    """
    return judge_reel_overlays(
        read_reel_overlays(timeline, placed,
                           resolve_project=resolve_project),
        intent=intent, full_wh=full_wh, draw_gain=draw_gain)


def _span(item) -> Optional[Tuple[int, int]]:
    """A placed item's record span `(in, out)`, or None unreadable."""
    if item is None:
        return None
    try:
        return int(item.GetStart()), int(item.GetEnd())
    except Exception:  # noqa: BLE001 - unreadable, skipped by its reader
        return None


def read_reel_overlays(timeline, placed: Sequence[dict], *,
                       resolve_project,
                       picture_tracks: Sequence[int] = ()) -> dict:
    """The RESOLVE half of the sweep: each placed overlay's stored
    transform and record span, as plain data `judge_reel_overlays` reads
    with no handle - plus, where `picture_tracks` names the picture
    rows, the clips on them (`reel_read.picture_items`) for the
    keep-clear half (`overlay_keep_clear`).

    Never raises; a read that fails is carried as `unavailable` and
    reported by the judge. A picture read that fails costs only the
    keep-clear half, which says so.
    """
    try:
        rows: dict = {}
        stored, spans = [], []
        for entry in placed or []:
            track_index = entry.get("track_index")
            if track_index not in rows:
                rows[track_index] = _row_items_by_start(timeline,
                                                        track_index)
            row = rows[track_index]
            item = None if row is None else row.get(entry.get("record_frame"))
            stored.append(_stored_transform(item, timeline, resolve_project))
            spans.append(_span(item))
    except Exception as failed:  # noqa: BLE001 - report, never refuse
        return {"unavailable": str(failed)}
    reads = {"placed": [dict(entry) for entry in placed or []],
             "stored": stored, "spans": spans, "pictures_read": False}
    if picture_tracks:
        from library.tools.reel_read import picture_items
        try:
            reads["pictures"] = picture_items(
                timeline, picture_tracks, resolve_project=resolve_project)
            reads["pictures_read"] = True
        except Exception as failed:  # noqa: BLE001 - keep-clear says so
            reads["pictures_unavailable"] = str(failed)
    return reads


def judge_reel_overlays(reads: dict, *,
                        intent: Optional[dict] = None,
                        full_wh: Tuple[int, int],
                        draw_gain: float = FALLBACK_DRAW_GAIN,
                        project_folder: str = "",
                        fps: Optional[float] = None) -> dict:
    """The FREE half of the sweep: `read_reel_overlays`' reads against
    intent, values then pixels - and, given the project and the
    timeline rate, the keep-clear report (`overlay_keep_clear`: which
    overlays sit over a person region or a salient box). Keep-clear
    REPORTS and never enters `passed`. Never raises."""
    import sys

    try:
        if "unavailable" in reads:
            raise RuntimeError(reads["unavailable"])
        result = _judge_reel_overlays(reads, intent=intent, full_wh=full_wh,
                                      draw_gain=draw_gain)
    except Exception as failed:  # noqa: BLE001 - report, never refuse
        reason = f"overlay sweep unavailable ({failed}); the reel stands."
        print(f"  {reason}", file=sys.stderr)
        return {"passed": True, "unavailable": str(failed),
                "values": None, "pixels": None}
    if project_folder and fps:
        from library.tools.overlay_keep_clear import keep_clear_from_reads
        result["keep_clear"] = keep_clear_from_reads(
            reads, project_folder=project_folder, full_wh=tuple(full_wh),
            fps=float(fps), draw_gain=draw_gain)
    return result


def _judge_reel_overlays(reads: dict, *,
                         intent: Optional[dict] = None,
                         full_wh: Tuple[int, int],
                         draw_gain: float = FALLBACK_DRAW_GAIN) -> dict:
    import sys

    computed: ComputedMap = {}
    value_clips: List[dict] = []
    pixel_clips: List[dict] = []
    for entry, stored in zip(reads["placed"], reads["stored"]):
        kind = entry.get("kind")
        segment_id = entry.get("segment_id")
        computed[(kind, segment_id)] = entry.get("placement")
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
