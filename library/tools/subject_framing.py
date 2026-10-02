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
`vision_pipeline_v3` measures shot size, identity and time ranges - never a position - and `object_segmentation`/`ocr_extraction` produce boxes (1.06 runs matte-triggered; 1.07 is wired and deselected by default).
`step_1_04_temporal_index.compute_face_presence` emits the horizontal centre of the largest detected face at 5Hz; `library/tools/subject_framing.py` reduces it per clip and returns a POSITION; `compile_manifest._conform_fields` owns the one copy of the geometry that turns it into a pan.
- The join is `subject_centers_by_clip`, and it reads the per-clip index FILES, not pipeline state. An earlier spelling walked the in-state `full_indices` mapping while step 1.04 emits a LIST, so it returned `{}` on every real run while its tests - all fed invented mappings - passed. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)
- **None means "frame centred" - do not replace it with a fabricated 0.5.**
  That None is two facts - measured-centred and unmeasurable - and
  :func:`subject_center_reading` tells them apart. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)

**A crop must be wide enough for the subject, and aiming it is not enough.**
`compute_face_presence` records `face_width` beside `face_center_x`; `subject_framing.subject_box` reduces both, and `SUBJECT_HEADROOM` is how much clear space the subject needs on each side.
- **No zoom both fills the frame and holds an over-wide subject** - every zoom below fill leaves bars - so `_conform_fields` SYNTHESISES the missing picture: shrink the source until the subject fits and put the same frame again, scaled to cover and blurred, behind it. That is the `framing_backdrop` route, drawn by `fx.subject_backdrop` and dispatched on `backdrop_picture_scale`.

`SUBJECT_HEADROOM` is how much clear space the subject needs on each side. [why](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)
- `_conform_fields` SYNTHESISES missing picture via the `framing_backdrop` route (`fx.subject_backdrop`).
- A backdrop clip carries **no `framing_pan_x`**.
- **The verdict is carried by the PLAN check**, `manifest_validator`'s P8. `render_qa.measure_face_intact` is the render-side backstop and is weaker on purpose: a face cropped hard enough stops being detectable at all.
- An explicit `framing_pan_x` still outranks the measurement, and the clip then keeps its crop with `subject_safe_zoom` recorded so P8 can say what that cost.
- `tests/unit/picture/test_subject_framing.py`.
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


@dataclass(frozen=True)
class SubjectReading:
    """A subject-centre answer with its worth attached.

    ``position`` is exactly what :func:`subject_center_x` returns - a
    normalised 0..1 centre, or None. ``status`` says which of the two
    Nones that None is: ``"off_centre"`` (a number is carried),
    ``"centred"`` (measured, and the subject sits inside
    ``CENTRE_DEADBAND``), or ``"unmeasurable"`` (no detection floor was
    met, or the input itself was unusable). ``reason`` is the detail:
    ``"measured_off_centre"``, ``"measured_centred"``,
    ``"no_face_track"``, ``"no_sample_rate"``, ``"empty_window"``,
    ``"too_few_detections"`` or ``"detection_ratio_below_floor"``.

    Any caller that branches on "did we measure" rather than on "is
    there a number" must read this, not the bare position: the bare
    position cannot tell a measured centre from a shrug, and treating
    the shrug as consent to aim is the defect this exists to close.
    """

    position: Optional[float]
    status: str
    reason: str


def subject_center_reading(
    face_presence: dict,
    source_in: float,
    source_out: float,
) -> "SubjectReading":
    """The subject's horizontal centre AND what the answer is worth.

    :func:`subject_center_x` returns None for two different facts - "the
    subject was measured and sits at the centre" and "nothing here could
    be measured" - and a caller holding that None cannot tell which it
    got. This is the same function with the distinction kept: `position`
    is exactly what :func:`subject_center_x` returns, and `status` says
    whether that None (or that number) is a measurement or a shrug.

    `status` is one of three words, and `reason` is the machine-readable
    detail underneath it:

    - ``"off_centre"`` / ``"measured_off_centre"`` - detections clear
      both floors and the median sits outside ``CENTRE_DEADBAND``.
      `position` is the aim.
    - ``"centred"`` / ``"measured_centred"`` - detections clear both
      floors and the median sits inside ``CENTRE_DEADBAND``. `position`
      is None, and that None means "leave the framing centred" - a
      measured verdict, not a missing one.
    - ``"unmeasurable"`` / one of ``"no_face_track"``,
      ``"no_sample_rate"``, ``"empty_window"``, ``"too_few_detections"``
      or ``"detection_ratio_below_floor"`` - the footage (or the index)
      does not support an answer. `position` is None, and that None
      means "no measurement", which a caller must not treat as consent
      to aim anywhere.

    A test that feeds a centred track and a sparse track and asserts the
    two Nones carry different statuses fails the moment anyone collapses
    them again (``tests/unit/picture/test_subject_framing.py``).
    """
    if not face_presence:
        return SubjectReading(None, "unmeasurable", "no_face_track")

    centers = face_presence.get("face_center_x") or []
    if not centers:
        return SubjectReading(None, "unmeasurable", "no_face_track")

    try:
        rate = float(face_presence["sample_rate_hz"])
    except (KeyError, TypeError, ValueError):
        return SubjectReading(None, "unmeasurable", "no_sample_rate")
    if rate <= 0:
        return SubjectReading(None, "unmeasurable", "no_sample_rate")

    if source_out <= source_in:
        return SubjectReading(None, "unmeasurable", "empty_window")

    start = max(0, int(source_in * rate))
    end = min(len(centers), int(source_out * rate) + 1)
    if end <= start:
        return SubjectReading(None, "unmeasurable", "empty_window")

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
        return SubjectReading(None, "unmeasurable", "too_few_detections")
    if len(detected) / float(len(window)) < MIN_DETECTION_RATIO:
        return SubjectReading(
            None, "unmeasurable", "detection_ratio_below_floor")

    cx = _median(detected)

    if abs(cx - 0.5) < CENTRE_DEADBAND:
        return SubjectReading(None, "centred", "measured_centred")

    return SubjectReading(round(cx, 4), "off_centre", "measured_off_centre")


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

    The two Nones this can return - measured-centred and unmeasurable -
    are told apart by :func:`subject_center_reading`, which this
    delegates to. Anything that needs to know WHICH None it got must
    call that instead.
    """
    return subject_center_reading(
        face_presence, source_in, source_out).position


def subject_centers_by_clip(project_dir: str) -> Dict[str, dict]:
    """Per-clip face_presence blocks, loaded from the per-clip index FILES.

    This reads ``pipeline_output/steps/1_04_temporal_index/index/clip_*.json``
    directly - the same files ``vision_pipeline_v3.load_temporal_index`` reads
    and every other temporal-index consumer already takes.

    An earlier spelling of this function read the in-state ``full_indices``
    copy out of ``pipeline_data.json`` instead. That path was dead: step 1.04
    emits a LIST of per-clip dicts while the reader walked a MAPPING, so on
    every real run it matched nothing and returned ``{}`` - and its tests
    passed because they fed it invented mappings. The in-state copy is gone
    and so is the mapping reader; this file-backed read is the only join.

    Returns a dict mapping both ``clip_id`` and the source-file stem to the
    clip's ``face_presence`` block, because the temporal index is keyed by
    clip id in some runs and by file stem in others - the same split
    ``semantic_index`` exists to bridge. Both spellings are returned so the
    caller can look up either without a ``.get()`` fallback chain.
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

        # Also key by source-file stem.
        src = entry.get("source_file") or entry.get("path")
        if src:
            stem = os.path.splitext(os.path.basename(src))[0]
            if stem:
                out[stem] = face

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
    aim_basis: str = "face"
    """What decided `center_x`: ``"body_pose"`` or ``"face"``.

    See :func:`measure_subject_in_window` for the rule. Carried through
    so a caller or a report can say which aim a shot got without
    re-deriving it, and so an older cached record (face-only, no
    `aim_basis` key) reads back as ``"face"`` rather than guessing.
    """


# ── Body-pose aim ────
#
# Captain's ruling, 2026-10-01 (`vep-the-captain-composes-left-of-centre`):
# "i want both subjects to be framed in the center of the video", answering
# a choice between a per-speaker default and this one. The hand-edit taste
# scout (`data/vep-hand-edit-taste-scout/report.md` 2.3) measured why
# face-centring left Akshita off-centre on every one of the 22 shots he
# held: she turns toward Craig, so a face-centred crop pushes her body
# (both elbows, the chair) toward the frame edge. A pose centroid - the
# mean x of her shoulder and forearm joints - sat within 0.008 of source
# width of where he put the crop, against 0.024 for her face. The same
# test on Craig's 61 engine-framed, never-touched shots found his body is
# offset from his face by about as much as hers (he types at a laptop on
# his right) - so "centre the body" is not a per-speaker fact, it is a
# general one the captain had simply never been asked to confirm for a
# speaker he already liked face-centred.  The rule below is the same for
# both speakers: no `speaker` argument, no per-speaker table.
#
# Apple Vision's body-pose joint keys (`vision_helper.swift`, confirmed by
# running the compiled helper: `neck_1_joint`, `left_shoulder_1_joint`,
# `right_shoulder_1_joint`, `left_forearm_joint`, `right_forearm_joint`,
# among others) are matched by substring, the same way the scout's
# `bodyan.py` read them, because Vision's raw names carry numbered/typed
# suffixes that differ by joint and are not worth pinning exactly.

BODY_NECK_SUBSTRING = "neck"
"""Which body in a multi-body frame is the subject: nearest neck to the
face x already measured for that frame."""

BODY_CENTROID_JOINTS: Tuple[str, ...] = (
    "left_shoulder", "right_shoulder", "left_forearm", "right_forearm")
"""The upper-body joints averaged into the pose centroid.  Matches the
scout's measured method (`report.md` 2.3, `bodyan.py`): shoulders and
forearms read as "the figure", not the hands or hips."""

BODY_JOINT_MIN_CONFIDENCE = 0.3
"""A joint below this confidence is treated as not detected.  Same floor
the scout's `bodyan.py` used."""

BODY_JOINT_MIN_LANDMARKS = 3
"""A frame's body centroid needs at least this many of the four
`BODY_CENTROID_JOINTS` above the confidence floor.  Below it one stray
joint (an arm out of frame) would swing the average; the scout's
`bodyan.py` used the same floor (`len(xs) >= 3`)."""


def _nearest_body_joints(bodies: Sequence[dict],
                          near_x: float) -> Optional[dict]:
    """The `joints` dict of whichever body's neck sits nearest `near_x`.

    `near_x` is that frame's own measured face centre: in a two-person
    frame the nearest neck to the detected (largest) face is the same
    person, which is what lets the centroid follow the speaker rather
    than whoever else is in shot.
    """
    best_joints: Optional[dict] = None
    best_distance: Optional[float] = None
    for body in bodies:
        joints = body.get("joints") if isinstance(body, dict) else None
        if not joints:
            continue
        neck_x = None
        for name, triple in joints.items():
            if BODY_NECK_SUBSTRING in name:
                try:
                    neck_x = float(triple[0])
                except (TypeError, IndexError, ValueError):
                    neck_x = None
                break
        if neck_x is None:
            continue
        distance = abs(neck_x - near_x)
        if best_distance is None or distance < best_distance:
            best_distance, best_joints = distance, joints
    return best_joints


def _body_centroid_x(joints: dict) -> Optional[float]:
    """Mean x of the detected `BODY_CENTROID_JOINTS`, or None.

    None when fewer than `BODY_JOINT_MIN_LANDMARKS` clear the confidence
    floor - the same "too thin to trust" answer the face reading gives
    below `MIN_SAMPLES`.
    """
    found: List[float] = []
    for substring in BODY_CENTROID_JOINTS:
        for name, triple in joints.items():
            if substring not in name:
                continue
            try:
                x, _y, confidence = triple
            except (TypeError, ValueError):
                continue
            if confidence is not None and confidence > BODY_JOINT_MIN_CONFIDENCE:
                found.append(float(x))
            break
    if len(found) < BODY_JOINT_MIN_LANDMARKS:
        return None
    return sum(found) / len(found)


def _probe_body_centroids(frame_paths: Sequence[str],
                           face_xs: Sequence[float]) -> Optional[List[Optional[float]]]:
    """Per-frame body-pose centroid x, aligned to `frame_paths`/`face_xs`.

    Returns None when Vision itself is unavailable on this machine or
    this interpreter (no helper, compile failure, run failure) - that is
    the same "no measurement" silence `measure_subject_in_window` already
    gives for a face it cannot detect, not an error. Returns a list the
    same length as `frame_paths` otherwise, with None where that frame's
    body could not be read.
    """
    from library.steps.step_1_04_temporal_index.vision_measure import (
        VisionUnavailable, ensure_helper, measure_frames)

    if not frame_paths:
        return None
    helper, _reason = ensure_helper()
    if helper is None:
        return None
    try:
        docs = measure_frames(list(frame_paths), helper)
    except VisionUnavailable:
        return None

    out: List[Optional[float]] = []
    for doc, face_x in zip(docs, face_xs):
        bodies = doc.get("bodies") or []
        joints = _nearest_body_joints(bodies, face_x)
        out.append(_body_centroid_x(joints) if joints else None)
    return out


def _aim_center_x(face_cx: float, face_width: Optional[float],
                   body_cx: Optional[float]) -> Tuple[float, str]:
    """The aim, and which rule decided it: body pose, bounded by the face.

    Centres on the body-pose centroid when one is measured, but never
    past `SUBJECT_HEADROOM` of the face's own measured width from the
    face centre - the same breathing room the engine already budgets
    around a face box (`SUBJECT_HEADROOM`'s docstring above). That bound
    is what keeps the captain's "face still in frame" requirement true
    without knowing the crop's eventual zoom at measurement time: even
    spent in full, the aim cannot move further from the face than the
    margin the engine already treats as safe clearance around it.

    Falls back to the face centre, unchanged from before this rule
    existed, when no body was measured or the face width is unknown -
    the same shrug `subject_center_x` gives for "could not be measured".
    """
    if body_cx is None or not face_width:
        return face_cx, "face"
    bound = SUBJECT_HEADROOM * face_width
    delta = max(-bound, min(bound, body_cx - face_cx))
    return face_cx + delta, "body_pose"


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
    face_frame_paths: List[str] = []
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
            face_frame_paths.append(frame_path)

        if len(centers_x) < MIN_SAMPLES:
            return None
        if len(centers_x) / float(samples) < MIN_DETECTION_RATIO:
            return None
        # A single frame with two detections is a false positive on a
        # bright rectangle; a shot that shows two people shows them
        # throughout. The same ratio that decides whether ONE face was
        # seen often enough decides whether a SECOND was.
        others = 1 if (multi / float(samples)) >= MIN_DETECTION_RATIO else 0

        # Body pose is read from the SAME frames, while they still exist
        # on disk - aiming at a second decode would risk disagreeing
        # with the face reading above about which instant was sampled.
        body_xs = _probe_body_centroids(face_frame_paths, centers_x)

    body_cx = None
    if body_xs is not None:
        measured = [x for x in body_xs if x is not None]
        if (len(measured) >= MIN_SAMPLES
                and len(measured) / float(len(body_xs)) >= MIN_DETECTION_RATIO):
            body_cx = _median(measured)

    face_cx = _median(centers_x)
    face_width = _median(widths)
    aim_cx, aim_basis = _aim_center_x(face_cx, face_width, body_cx)

    return SubjectPoint(
        center_x=round(aim_cx, 4),
        center_y=round(_median(centers_y), 4),
        width=round(face_width, 4),
        samples=samples,
        detected=len(centers_x),
        others=others,
        aim_basis=aim_basis)


# ── The aim, recorded ────
#
# A project cut from an existing Resolve timeline has no preflight and
# therefore no temporal index - which is every project the reels path
# serves (geo-podcast's `pipeline_data.json` carries `catalog`,
# `build_reels`, `select_reels` and no `temporal_index` at all). So the
# first build of a reel probes each played window with the Haar cascade
# above, and every rebuild re-decoded the same frames and re-ran the
# same detector - per build, in whatever interpreter the build happened
# to run under. That interpreter is undeclared, and the probe failed
# four separate times in two days (no cascade, cascade again, a venv
# missing `jsonschema`): each failure re-aimed or un-aimed every shot.
#
# The measurement is therefore RECORDED the first time it is taken, in
# one JSON sidecar under the project's scratch area, and every later
# build reads it instead of re-decoding. A warm cache means a rebuild
# needs no face detector at all - the read path never imports cv2 - so
# a broken build environment can no longer silently (or loudly) move
# the picture. Scratch is the honest place: the record is regenerable,
# and deleting it only costs a re-probe.
#
# Why this sidecar and not the banked `face_present_times` /
# `face_absent_times`: those were never produced for this project's
# footage (no preflight ran), and where they exist they are the wrong
# shape for the aim - timestamps of presence and absence carry no
# position, and the punch-in needs two axes plus a multiplicity count
# (`SubjectPoint.center_y`, `others`) that no banked signal carries.
# Wiring absence timestamps into the aim would gate what the probe's
# own None already refuses, with a staler measurement. The verdict is
# recorded here so nobody re-opens it: absence times answer "does the
# shot hold", nobody asks that of any gate, and the aim needs the
# centre, not the calendar.

SUBJECT_MEASUREMENTS_FILENAME = "reel_subject_measurements.json"
"""The sidecar file, under the project's scratch area."""


def subject_measurements_path(project_folder: str) -> Path:
    """Where this project's recorded aims live."""
    from library.tools.project_layout import Area, ProjectLayout

    return (Path(ProjectLayout(project_folder).read_dir(Area.SCRATCH))
            / SUBJECT_MEASUREMENTS_FILENAME)


def _window_key(source_in: float, source_out: float) -> Tuple[float, float]:
    return (round(float(source_in), 3), round(float(source_out), 3))


def _source_identity(source_file: str) -> Optional[dict]:
    """The footage this record is about, or None if it cannot be read.

    Size plus mtime is the staleness check: a record for different
    footage must never aim a window, so a mismatch reads as a cache
    miss and the window is re-probed.
    """
    try:
        stat = os.stat(source_file)
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _detector_identity() -> dict:
    """What looked at the frames, for provenance, never for gating.

    A record is reused on footage identity alone: a measurement taken
    under a working detector stays a measurement when the detector is
    gone, which is the whole point. The detector is still written down,
    so a changed environment is SAID rather than hidden.
    """
    try:
        import cv2
        cv2_version = str(getattr(cv2, "__version__", "unknown"))
        haar = bool(getattr(cv2, "CascadeClassifier", None))
    except ImportError:
        cv2_version = "not installed"
        haar = False
    return {"cv2": cv2_version, "haar_classifier": haar,
            "cascade": "haarcascade_frontalface_default.xml" if haar else None}


def _subject_to_record(point: Optional["SubjectPoint"]) -> Optional[dict]:
    if point is None:
        return None
    return {"center_x": point.center_x, "center_y": point.center_y,
            "width": point.width, "samples": point.samples,
            "detected": point.detected, "others": point.others,
            "aim_basis": point.aim_basis}


def _record_to_subject(record) -> Optional["SubjectPoint"]:
    if not isinstance(record, dict):
        return None
    try:
        return SubjectPoint(
            center_x=float(record["center_x"]),
            center_y=float(record["center_y"]),
            width=float(record["width"]),
            samples=int(record["samples"]),
            detected=int(record["detected"]),
            others=int(record.get("others", 0)),
            aim_basis=str(record.get("aim_basis") or "face"))
    except (KeyError, TypeError, ValueError):
        return None


def _read_measurements_file(path: Path) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            document = json.load(f)
    except (OSError, ValueError):
        return []
    entries = document.get("entries") if isinstance(document, dict) else None
    return entries if isinstance(entries, list) else []


def read_recorded_subject(
    project_folder: str,
    source_file: str,
    source_in: float,
    source_out: float,
) -> Tuple[Optional["SubjectPoint"], Optional[dict]]:
    """A recorded aim for one played window, or a miss.

    Returns ``(point_or_None, provenance_or_None)``. `provenance` None
    means MISS - nothing recorded, or the footage changed under the
    record, or the sidecar is absent or corrupt. A hit carries
    `provenance` even when the recorded answer was "no face": that is a
    measurement (frames were read, no face was found), not a miss, and
    the caller must not re-probe what was already looked at - it must
    play the shot uncropped and say the record said so.

    Never needs a face detector: the read path imports no cv2.
    """
    try:
        path = subject_measurements_path(project_folder)
    except Exception:
        return None, None
    window = _window_key(source_in, source_out)
    identity = _source_identity(source_file)
    if identity is None:
        return None, None
    wanted = os.path.abspath(source_file)
    for entry in _read_measurements_file(path):
        if not isinstance(entry, dict):
            continue
        if entry.get("source_file") != wanted:
            continue
        try:
            entry_window = (round(float(entry["source_in"]), 3),
                            round(float(entry["source_out"]), 3))
        except (KeyError, TypeError, ValueError):
            continue
        if entry_window != window:
            continue
        if entry.get("source") != identity:
            continue
        provenance = {"basis": "recorded",
                      "measured_at": entry.get("measured_at"),
                      "detector": entry.get("detector")}
        return _record_to_subject(entry.get("subject")), provenance
    return None, None


def record_subject_measurement(
    project_folder: str,
    source_file: str,
    source_in: float,
    source_out: float,
    point: Optional["SubjectPoint"],
) -> Optional[dict]:
    """File one window's aim. Returns its provenance, or None.

    Overwrites any earlier record for the same file and window: a
    re-probe is newer than whatever it replaces. A sidecar that cannot
    be written warns on stderr and returns None - the probe already
    answered, and a build must not fail over its own cache.
    """
    import sys
    from datetime import datetime, timezone

    try:
        from library.tools.project_layout import Area, ProjectLayout

        path = Path(ProjectLayout(project_folder).write_dir(Area.SCRATCH))
        path = path / SUBJECT_MEASUREMENTS_FILENAME
    except Exception as exc:
        print(f"  subject aim not recorded for "
              f"{os.path.basename(source_file)}: {exc}",
              file=sys.stderr)
        return None
    identity = _source_identity(source_file)
    if identity is None:
        return None
    window_in, window_out = _window_key(source_in, source_out)
    wanted = os.path.abspath(source_file)

    def _same_window(entry: dict) -> bool:
        try:
            return (entry.get("source_file") == wanted
                    and round(float(entry.get("source_in")), 3) == window_in
                    and round(float(entry.get("source_out")), 3) == window_out)
        except (TypeError, ValueError):
            return False

    entries = [e for e in _read_measurements_file(path)
               if not (isinstance(e, dict) and _same_window(e))]
    detector = _detector_identity()
    provenance = {"basis": "probed",
                  "measured_at": datetime.now(timezone.utc).isoformat(
                      timespec="seconds"),
                  "detector": detector}
    entries.append({"source_file": wanted,
                    "source_in": window_in, "source_out": window_out,
                    "source": identity, "detector": detector,
                    "measured_at": provenance["measured_at"],
                    "subject": _subject_to_record(point)})
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, indent=2)
            f.write("\n")
    except OSError as exc:
        print(f"  subject aim not recorded for "
              f"{os.path.basename(source_file)}: {exc}",
              file=sys.stderr)
        return None
    return provenance
