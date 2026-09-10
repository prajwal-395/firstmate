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

**It returns a POSITION and a SIZE, never a pan or a zoom.** Turning
either into pixels needs the source size, the fit scale, the zoom and the
target width, and `compile_manifest._conform_fields` already holds all four.
Duplicating that arithmetic here is how the two would drift apart, so the
conversion stays at the one site that does the geometry.

**Silence is a real answer.** No detections, too few detections, a subject
already near the centre, or the OpenCV-less fallback all return None,
which means "no pan" - the crop window stays in the middle of the source.
A fabricated centre would be worse than none, because it reads as a
measurement.

Position was not enough
-----------------------

Aiming a crop at the subject only helps while the crop is big enough to
hold them.  Measured on project 001's own render (2026-08-26): at
t=44.0 s the Haar face box spans x 285..1008 of a 1920-wide source - 37.7%
of the width - and the fill conform keeps x 450..1056, which is 31.6%.
The pan was computed correctly and applied correctly; the window it aimed
was simply smaller than the thing it was aiming at, and 165 px of the
speaker's face sat outside the delivered frame on the most important line
of the edit.

Nothing caught it because nothing measured the subject's SIZE.
`compute_face_presence` had the number in hand - the cascade returns
(x, y, w, h) - and kept only the centre.  It now records `face_width` too,
and :func:`subject_box` reduces it per clip the same way
:func:`subject_center_x` reduces the centre.


Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**Subject position comes from `face_center_x`, not from the vision pass.**
`vision_pipeline_v3` measures shot size, identity and time ranges - never a position - and `object_segmentation`/`ocr_extraction` produce boxes but are not in the DAG.
`step_1_04_temporal_index.compute_face_presence` emits the horizontal centre of the largest detected face at 5Hz; `library/tools/subject_framing.py` reduces it per clip and returns a POSITION; `compile_manifest._conform_fields` owns the one copy of the geometry that turns it into a pan.
- The join is `subject_centers_by_clip`. Step 1.04's real output shape is `{"temporal_event_indices": [...], "full_indices": [...]}` - a LIST of per-clip dicts, not a mapping. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)
- **None means "frame centred" - do not replace it with a fabricated 0.5.** [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)

**A crop must be wide enough for the subject, and aiming it is not enough.**
`compute_face_presence` records `face_width` beside `face_center_x`; `subject_framing.subject_box` reduces both, and `SUBJECT_HEADROOM` is how much clear space the subject needs on each side.
- **No zoom both fills the frame and holds an over-wide subject** - every zoom below fill leaves bars - so `_conform_fields` SYNTHESISES the missing picture: shrink the source until the subject fits and put the same frame again, scaled to cover and blurred, behind it. That is the `framing_backdrop` route, drawn by `fx.subject_backdrop` and dispatched on `backdrop_picture_scale`.

`SUBJECT_HEADROOM` is how much clear space the subject needs on each side. [why](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)
- `_conform_fields` SYNTHESISES missing picture via the `framing_backdrop` route (`fx.subject_backdrop`).
- A backdrop clip carries **no `framing_pan_x`**.
- **The verdict is carried by the PLAN check**, `manifest_validator`'s P8. `render_qa.measure_face_intact` is the render-side backstop and is weaker on purpose: a face cropped hard enough stops being detectable at all.
- An explicit `framing_pan_x` still outranks the measurement, and the clip then keeps its crop with `subject_safe_zoom` recorded so P8 can say what that cost.
- `tests/test_subject_survives_the_conform.py`.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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


def load_face_cascade():
    """The frontal-face Haar cascade, or None if this OpenCV has none.

    Returning None matters as much as returning a classifier. OpenCV 5
    dropped Haar cascades: there is no `cv2.CascadeClassifier` and no XML
    in `cv2.data.haarcascades`. `requirements.txt` said `opencv-python>=4.8`,
    which resolves to 5.x, and the AttributeError that produced was caught
    by the broad `except Exception` around the whole function - so
    `face_presence` came back with EMPTY values on such a machine, rather
    than falling back to the variance heuristic. Empty is worse than
    approximate: it silently disabled the subject-absence usable-range
    rule in the vision pass as well as subject-aware framing.

    The requirement is now pinned below 5 so the cascade is really there;
    this function is the guard for anyone whose environment predates that.
    """
    try:
        import cv2
    except ImportError:
        return None

    classifier = getattr(cv2, "CascadeClassifier", None)
    if classifier is None:
        return None

    data = getattr(cv2, "data", None)
    haar_dir = getattr(data, "haarcascades", None) if data else None
    if not haar_dir:
        return None

    cascade_path = os.path.join(haar_dir, "haarcascade_frontalface_default.xml")
    if not os.path.exists(cascade_path):
        return None

    cascade = classifier(cascade_path)
    # A CascadeClassifier that failed to load its XML is not an error, it
    # is an object that detects nothing on every frame.
    if hasattr(cascade, "empty") and cascade.empty():
        return None
    return cascade


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


def load_face_tracks_from_files(project_dir: str) -> Dict[str, dict]:
    """Per-clip face_presence blocks, loaded from the per-clip index FILES.

    This reads ``pipeline_output/steps/1_04_temporal_index/index/clip_*.json``
    directly - the same files ``vision_pipeline_v3.load_temporal_index`` reads
    and every other temporal-index consumer already takes.  It replaces the
    in-state ``full_indices`` path that ``subject_centers_by_clip`` used to
    walk, so the 2.35 MB state copy is no longer needed for subject framing.

    Returns a dict mapping both ``clip_id`` and the source-file stem to the
    clip's ``face_presence`` block, matching the shape
    ``subject_centers_by_clip`` returns.
    """
    from library.tools.project_layout import Area, ProjectLayout

    out: Dict[str, dict] = {}
    try:
        layout = ProjectLayout(project_dir)
        index_dir = layout.read_dir(Area.TEMPORAL_INDEX)
    except Exception:
        return out

    if not index_dir.is_dir():
        return out

    for path in sorted(index_dir.glob("clip_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        if not isinstance(entry, dict):
            continue
        face = entry.get("face_presence")
        if not isinstance(face, dict):
            continue

        clip_id = entry.get("clip_id")
        if clip_id:
            out[clip_id] = face

        # Also key by source-file stem, the same way
        # subject_centers_by_clip does for the in-state path.
        src = entry.get("source_file") or entry.get("path")
        if src:
            stem = os.path.splitext(os.path.basename(src))[0]
            if stem:
                out[stem] = face

    return out


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


# How much room the subject needs BEYOND their own face box, as a fraction
# of that box's width, on each side.  The crop must be at least
# ``width * (1 + 2 * SUBJECT_HEADROOM)`` wide or the speaker is jammed
# against the frame edges.
#
# 0.15 was set against 001's A-roll, where the frontal box at t=44.0 s
# (x 285..1008 of 1920) covers the visible head almost exactly - the cap
# brim on one side and the hair on the other both fall within a few pixels
# of it.  So this is breathing room rather than a correction for a box
# that under-covers the head: a talking head with 15% of its own width
# clear on each side reads as a close-up, and one with none reads as a
# crop.  It costs 001 a conform zoom of 2.04 instead of 3.16.
SUBJECT_HEADROOM = 0.15

# The crop must hold the subject at a percentile of their measured width,
# not at the median: the frame that matters is the one where they lean
# closest to the lens, and a median-sized window crops it.
SUBJECT_WIDTH_PERCENTILE = 0.9

# A box wider than this is not a face.  The cascade does produce them -
# a bright rectangle at low scale - and one would collapse the conform to
# no zoom at all.
MAX_PLAUSIBLE_WIDTH = 0.75


@dataclass(frozen=True)
class SubjectBox:
    """Where the subject is and how much room they take.

    ``center_x`` and ``width`` are fractions of the SOURCE width.
    ``width`` is the subject's own face box; the crop that has to hold it
    needs :data:`SUBJECT_HEADROOM` of that width again on each side, which
    is what :func:`required_crop_width` returns.
    """

    center_x: float
    width: float

    def required_crop_width(self) -> float:
        """Narrowest crop, as a fraction of source width, that holds them."""
        return min(1.0, self.width * (1.0 + 2.0 * SUBJECT_HEADROOM))


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = int(round(q * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, idx))]


def _detected_samples(
    face_presence: dict,
    source_in: float,
    source_out: float,
) -> Optional[Tuple[List[float], List[Optional[float]], int]]:
    """(centres, widths, window length) over a clip's range, or None.

    One reader for the two reductions below, so they can never disagree
    about which samples count.  ``widths`` is parallel to ``centres`` and
    carries None wherever the index predates `face_width`.
    """
    if not face_presence:
        return None

    centers = face_presence.get("face_center_x") or []
    if not centers:
        return None
    widths = face_presence.get("face_width") or []

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
    kept_c: List[float] = []
    kept_w: List[Optional[float]] = []
    for offset, value in enumerate(window):
        if value is None:
            continue
        try:
            cx = float(value)
        except (TypeError, ValueError):
            continue
        if not (MIN_PLAUSIBLE_CX <= cx <= MAX_PLAUSIBLE_CX):
            continue
        kept_c.append(cx)
        index = start + offset
        raw = widths[index] if index < len(widths) else None
        try:
            fw = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            fw = None
        if fw is not None and not (0.0 < fw <= MAX_PLAUSIBLE_WIDTH):
            fw = None
        kept_w.append(fw)

    if len(kept_c) < MIN_SAMPLES:
        return None
    if len(kept_c) / float(len(window)) < MIN_DETECTION_RATIO:
        return None
    return kept_c, kept_w, len(window)


def subject_box(
    face_presence: dict,
    source_in: float,
    source_out: float,
) -> Optional[SubjectBox]:
    """The subject's position AND width over a clip's range, or None.

    None means the footage does not support an answer - no detections, too
    few, or an index that predates `face_width` and so carries no widths
    at all.  A caller that gets None must leave the conform alone rather
    than invent a size, for the same reason
    :func:`subject_center_x` returns None rather than 0.5.

    ``center_x`` here is the plain median and carries no deadband. The
    deadband in :func:`subject_center_x` answers "is it worth moving the
    crop", which is a different question from "where is the subject".
    """
    samples = _detected_samples(face_presence, source_in, source_out)
    if samples is None:
        return None
    centers, widths, _window = samples

    measured = [w for w in widths if w is not None]
    if len(measured) < MIN_SAMPLES:
        return None

    return SubjectBox(
        center_x=round(_median(centers), 4),
        width=round(_percentile(measured, SUBJECT_WIDTH_PERCENTILE), 4),
    )


# ── Measuring the subject in a window this pipeline never indexed ────
#
# `subject_center_x` and `subject_box` read a clip's TEMPORAL INDEX, and
# a project that was ingested from an existing Resolve timeline has no
# preflight and therefore no index - which is every project the reels
# path serves.  So the reels punch-in had nothing to aim with, and a
# centred 2.3x zoom put the speaker out of shot (captain, 2026-09-09:
# "cropping into the bottom-left of the raw frame with Craig entirely
# out of shot").
#
# This measures the SAME thing off the frames the reel actually plays,
# with the same cascade, the same medians and the same thresholds, so
# there is one reading of where the subject is and not two.

SUBJECT_PROBE_SAMPLES = 12
"""How many frames of a played window are sampled.

Enough that `MIN_SAMPLES` (4) and `MIN_DETECTION_RATIO` (0.34) can both
be met by a shot where the speaker turns away for part of it, and few
enough that a three-shot reel decodes 36 frames rather than thousands.
"""


class SubjectProbeUnavailable(RuntimeError):
    """The subject could not even be looked for: no face detector.

    Raised - never returned as None - when this interpreter cannot load
    the Haar cascade (`load_face_cascade` answers None: no cv2, or an
    OpenCV 5 whose `CascadeClassifier` is gone).  None from
    `measure_subject_in_window` means "frames were read and no face was
    measured", which under the TV-frame look leaves the shot uncropped -
    and an uncropped shot inside the television's screen is black the
    F12 gate is guaranteed to refuse.  Returning None here turned an
    incapacitated probe into six identical identity transforms and a
    6x F12 gate failure that named the symptom (Reel 09, 2026-09-09:
    system python's cv2 5.0.0 shadowed the pinned 4.x, so every shot
    measured nothing).  A measurement that could not be taken must say
    so - the same line `render_qa.measure_face_intact` draws with its
    "No Haar cascade available" warning - so the caller can refuse the
    build instead of shipping staging the gate will delete.
    """


@dataclass(frozen=True)
class SubjectPoint:
    """Where the subject sits in the SOURCE frame, both axes, 0..1.

    `center_y` is here and is NOT in `SubjectBox`: the temporal index
    records `face_center_x` and `face_width` and no vertical term at
    all, so a reading taken from the index can only ever answer one
    axis.  A reading taken from the frames can answer both, and a
    punch-in that can only pan is a punch-in that cannot rescue a
    speaker sitting low in frame.
    """
    center_x: float
    center_y: float
    width: float
    samples: int
    detected: int
    others: int = 0
    """How many OTHER faces are consistently in this window.

    Above zero the shot is not a single-speaker close-up, and "the
    largest face" stops meaning "the speaker": in a two-shot the nearest
    person wins the size comparison whoever is talking, so a crop aimed
    that way can put the actual speaker outside the frame. The caller
    refuses the punch-in rather than aiming at a guess.
    """


def measure_subject_in_window(video_path: str, source_in: float,
                               source_out: float,
                               samples: int = SUBJECT_PROBE_SAMPLES,
                               cascade=None) -> Optional["SubjectPoint"]:
    """The subject's position over one played window, or None.

    None means the footage does not support an answer - no decodable
    frames, or too few detections - and a caller that gets None must NOT
    punch in.  That is the whole contract: an unaimed punch-in is a guess
    about where the person is, and the captain's ruling is to refuse
    rather than guess.

    A missing DETECTOR is not None: it raises `SubjectProbeUnavailable`.
    None would read as "no face in this shot" and the caller would leave
    the shot uncropped, which under the TV-frame look is black inside
    the screen on every item - the identical-rectangle F12 failure.  An
    environment that cannot look must say so before any frame is
    decoded, so the build refuses with the cause instead of shipping
    staging the gate deletes.

    Frames are decoded with ffmpeg at evenly spaced points inside the
    window rather than read from a cached index, because the index is
    what a reels project does not have.
    """
    import subprocess
    import tempfile

    if source_out <= source_in:
        return None
    cascade = load_face_cascade() if cascade is None else cascade
    if cascade is None:
        try:
            import cv2
            cv2_version = getattr(cv2, "__version__", "unknown")
            has_classifier = hasattr(cv2, "CascadeClassifier")
        except ImportError:
            cv2_version = "not installed"
            has_classifier = False
        raise SubjectProbeUnavailable(
            "no face detector in this interpreter "
            f"(cv2 {cv2_version}, "
            f"CascadeClassifier {'present' if has_classifier else 'absent'}): "
            "the reels punch-in cannot be aimed, and an unaimed shot "
            "under the TV-frame look leaves black inside the screen. "
            "Run the build under the project's .venv "
            "(opencv-python>=4.8,<5, which ships the Haar cascade).")
    try:
        import cv2
    except ImportError:
        return None

    span = source_out - source_in
    # Inside the window, never on its edges: the first and last frames of
    # a cut are the ones most likely to be a dissolve or a head turn.
    points = [source_in + span * (i + 0.5) / samples for i in range(samples)]

    centers_x: List[float] = []
    centers_y: List[float] = []
    widths: List[float] = []
    multi = 0
    with tempfile.TemporaryDirectory(prefix="subject_probe_") as tmp:
        for index, at in enumerate(points):
            frame_path = os.path.join(tmp, f"f{index:03d}.png")
            result = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", video_path,
                 "-frames:v", "1", "-vf", "scale=640:-2", frame_path],
                capture_output=True, encoding="utf-8", check=False)
            if result.returncode != 0 or not os.path.isfile(frame_path):
                continue
            image = cv2.imread(frame_path)
            if image is None:
                continue
            height, width = image.shape[:2]
            grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(grey, 1.1, 5)
            if faces is None or len(faces) == 0:
                continue
            # How many faces this frame really holds, counted before one
            # is chosen: a second person is the difference between "the
            # largest face is the speaker" and "the largest face is
            # whoever sits nearest the camera".
            plausible = [f for f in faces
                         if MIN_PLAUSIBLE_CX
                         <= (int(f[0]) + int(f[2]) / 2.0) / width
                         <= MAX_PLAUSIBLE_CX]
            if len(plausible) > 1:
                multi += 1
            x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
            cx = (x + w / 2.0) / width
            cy = (y + h / 2.0) / height
            if not (MIN_PLAUSIBLE_CX <= cx <= MAX_PLAUSIBLE_CX):
                continue
            centers_x.append(cx)
            centers_y.append(cy)
            widths.append(w / float(width))

    if len(centers_x) < MIN_SAMPLES:
        return None
    if len(centers_x) / float(samples) < MIN_DETECTION_RATIO:
        return None
    # A single frame with two detections is a false positive on a bright
    # rectangle; a shot that shows two people shows them throughout. The
    # same ratio that decides whether ONE face was seen often enough
    # decides whether a SECOND was.
    others = 1 if (multi / float(samples)) >= MIN_DETECTION_RATIO else 0
    return SubjectPoint(
        center_x=round(_median(centers_x), 4),
        center_y=round(_median(centers_y), 4),
        width=round(_median(widths), 4),
        samples=samples,
        detected=len(centers_x),
        others=others)
