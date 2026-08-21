"""Where the subject is, reduced to one number per clip.

P1.2. The pipeline crops landscape source into a vertical frame, and when
it crops it has always cropped dead centre - so a speaker standing in the
left third of the frame gets beheaded. `framing_pan_x` moves the crop
window, and the renderer applies it (`resolve_build_timeline._apply_conform`
-> `SetProperty("Pan", ...)`), but nothing ever computed a value: it came
only from a spine-block key no planner writes.

This module computes it, from the only subject geometry the pipeline
actually measures: the horizontal centre of the largest detected face,
sampled at 5Hz by `step_1_04_temporal_index.compute_face_presence` as
`face_center_x`. The v3 vision pass measures shot size, identity and time
ranges, none of which is a position; `object_segmentation` and
`ocr_extraction` do produce boxes but are not wired into the DAG.

Two deliberate limits.

**It returns a POSITION, not a pan.** Turning a subject position into a
pixel offset needs the source size, the fit scale, the zoom and the target
width, and `compile_manifest._conform_fields` already holds all four.
Duplicating that arithmetic here is how the two would drift apart, so the
conversion stays at the one site that does the geometry.

**Silence is a real answer.** No detections, too few detections, a subject
already near the centre, or the OpenCV-less fallback all return None,
which means "no pan" - the crop window stays in the middle of the source.
A fabricated centre would be worse than none, because it reads as a
measurement.
"""

import os
from typing import List, Optional, Sequence

# A clip needs a few detections before its median means anything. At 5Hz
# this is 0.8s of face. Below it, one false positive would swing the crop.
MIN_SAMPLES = 4

# Fraction of samples in the clip's range that must carry a detection.
# A face visible for a tenth of the shot should not reframe the whole shot.
MIN_DETECTION_RATIO = 0.34

# How far off centre the subject must be before the crop moves at all,
# as a fraction of the source width. Inside this, centring is already the
# right answer and moving would be visible churn for nothing. It also
# keeps clips that were fine before byte-identical.
CENTRE_DEADBAND = 0.04

# Beyond this the detection is more likely spurious than real - a face
# hard against the frame edge is usually a background extra or a false
# positive on a bright rectangle.
MIN_PLAUSIBLE_CX = 0.05
MAX_PLAUSIBLE_CX = 0.95


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def subject_center_x(
    face_presence: dict,
    source_in: float,
    source_out: float,
) -> Optional[float]:
    """Normalised horizontal centre of the subject over a clip's range.

    Args:
        face_presence: the `face_presence` block of a clip's temporal
            index - `{"sample_rate_hz": int, "face_center_x": [...]}`.
        source_in / source_out: the clip's range in SOURCE seconds, which
            is what `face_center_x` is indexed by. Passing timeline
            seconds silently reframes on the wrong part of the shot.

    Returns:
        0.0 (left edge) to 1.0 (right edge), or None when the footage does
        not support an answer. None means "leave the framing centred".
    """
    if not face_presence:
        return None

    centers = face_presence.get("face_center_x") or []
    if not centers:
        return None

    # The rate is what maps clip seconds onto sample indices, so it must be
    # read, not assumed. `or 5` was wrong here: a rate of 0 is falsy, so a
    # malformed index silently became a 5Hz one and every clip got framed
    # off the wrong samples.
    try:
        rate = float(face_presence["sample_rate_hz"])
    except (KeyError, TypeError, ValueError):
        return None
    if rate <= 0:
        return None

    if source_out <= source_in:
        return None

    start = max(0, int(source_in * rate))
    end = min(len(centers), int(source_out * rate) + 1)
    if end <= start:
        return None

    window = centers[start:end]
    detected: List[float] = []
    for value in window:
        if value is None:
            continue
        try:
            cx = float(value)
        except (TypeError, ValueError):
            continue
        if MIN_PLAUSIBLE_CX <= cx <= MAX_PLAUSIBLE_CX:
            detected.append(cx)

    if len(detected) < MIN_SAMPLES:
        return None
    if len(detected) / float(len(window)) < MIN_DETECTION_RATIO:
        return None

    # Median, not mean: one frame of false positive on a bright rectangle
    # at the far edge should not drag the crop across the shot.
    cx = _median(detected)

    if abs(cx - 0.5) < CENTRE_DEADBAND:
        return None

    return round(cx, 4)


def subject_centers_by_clip(
    temporal_index: dict,
) -> dict:
    """Per-clip `face_presence` blocks, keyed however the index keys them.

    `compile_manifest` joins on clip id, and the temporal index is keyed
    by clip id in some runs and by file stem in others - the same split
    `semantic_index` exists to bridge. Both spellings are returned so the
    caller can look up either without a `.get()` fallback chain.

    **The shape step 1.04 really emits is a LIST.** Its return value is
    ``{"temporal_event_indices": [...], "full_indices": [...], ...}``,
    where `full_indices` carries the whole per-clip index - `face_presence`
    included - as a list of dicts each naming its own `clip_id`. This
    function used to read only a mapping (`indices`, or the top level
    keyed by clip), so on every real run it matched nothing and returned
    `{}`: the pan was never computed, and a `framing_intent` of 1.0 would
    have been the blind centre crop the whole mechanism exists to avoid.
    Its tests all fed it invented mappings, so it passed. Same class as
    every other key-name mismatch in this pipeline: nothing raised.
    """
    out = {}
    if not temporal_index:
        return out

    def _record(key, entry):
        if not isinstance(entry, dict):
            return
        face = entry.get("face_presence")
        if isinstance(face, dict) and key:
            out[key] = face

    for listing in ("full_indices", "indices", "temporal_event_indices"):
        entries = temporal_index.get(listing)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    _record(entry.get("clip_id"), entry)
                    src = entry.get("source_file") or entry.get("path")
                    if src:
                        stem = os.path.splitext(os.path.basename(src))[0]
                        _record(stem, entry)
        elif isinstance(entries, dict):
            for key, entry in entries.items():
                _record(key, entry)

    # A bare mapping of clip_id -> index, which is how the tests and some
    # older exports spell it.
    for key, entry in temporal_index.items():
        _record(key, entry)

    return out
