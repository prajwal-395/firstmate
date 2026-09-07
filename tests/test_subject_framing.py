"""P1.2: the subject bounding box driving framing_pan_x.

Three layers, tested separately because each has its own way of going
quiet:

1. `subject_framing.subject_center_x` - reducing a 5Hz face-centre track
   to one position per clip, and refusing to answer when the footage
   cannot support one.
2. `compile_manifest._conform_fields` - turning that position into a pixel
   pan. This is the only site that owns the geometry.
3. The contract with the renderer: what it produces must be a value
   `_apply_conform` writes to a property Resolve accepts.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (PROJECT_ROOT,
           os.path.join(PROJECT_ROOT, "library"),
           os.path.join(PROJECT_ROOT, "library", "steps", "step_5_04_compile_manifest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from library.tools.subject_framing import (
    CENTRE_DEADBAND,
    MIN_DETECTION_RATIO,
    MIN_SAMPLES,
    subject_center_x,
    subject_centers_by_clip,
)
from library.steps.step_5_04_compile_manifest.step import _conform_fields

RATE = 5


def face_track(centers, rate=RATE):
    return {"sample_rate_hz": rate, "face_center_x": list(centers)}


# ─────────────────────────────────────────────────────────
# 1. Reducing the track to a position
# ─────────────────────────────────────────────────────────

class TestSubjectCenter:

    def test_steady_subject_left_of_centre(self):
        track = face_track([0.30] * 20)
        assert subject_center_x(track, 0.0, 4.0) == pytest.approx(0.30)

    def test_steady_subject_right_of_centre(self):
        track = face_track([0.72] * 20)
        assert subject_center_x(track, 0.0, 4.0) == pytest.approx(0.72)

    def test_only_the_clip_range_is_considered(self):
        """A subject who moves must not be located from the wrong shot.

        Left for the first two seconds, right for the next two. A clip cut
        from the second half must report the second half's position.
        """
        track = face_track([0.25] * 10 + [0.78] * 10)
        assert subject_center_x(track, 0.0, 1.9) == pytest.approx(0.25)
        assert subject_center_x(track, 2.0, 3.9) == pytest.approx(0.78)

    def test_median_survives_a_spurious_detection(self):
        """One false positive at the far edge must not drag the crop."""
        centers = [0.30] * 12
        centers[5] = 0.93          # a bright rectangle, not a face
        assert subject_center_x(face_track(centers), 0.0, 4.0) == pytest.approx(0.30)

    def test_subject_near_centre_returns_none(self):
        """Already centred means no pan, so today's output is unchanged."""
        assert subject_center_x(face_track([0.5] * 20), 0.0, 4.0) is None
        just_inside = 0.5 + CENTRE_DEADBAND / 2
        assert subject_center_x(face_track([just_inside] * 20), 0.0, 4.0) is None

    def test_subject_just_outside_the_deadband_answers(self):
        just_outside = 0.5 + CENTRE_DEADBAND * 2
        got = subject_center_x(face_track([just_outside] * 20), 0.0, 4.0)
        assert got == pytest.approx(just_outside)


class TestSubjectCenterRefusesToGuess:
    """Every path where the honest answer is None.

    A fabricated position is worse than none: it reads as a measurement
    and it moves the picture.
    """

    def test_no_detections(self):
        assert subject_center_x(face_track([None] * 20), 0.0, 4.0) is None

    def test_too_few_detections(self):
        centers = [None] * 20
        for i in range(MIN_SAMPLES - 1):
            centers[i] = 0.3
        assert subject_center_x(face_track(centers), 0.0, 4.0) is None

    def test_face_visible_for_too_small_a_fraction_of_the_shot(self):
        """A face in a tenth of the shot must not reframe the whole shot."""
        centers = [None] * 60
        for i in range(MIN_SAMPLES + 1):
            centers[i] = 0.25
        got = subject_center_x(face_track(centers), 0.0, 12.0)
        assert got is None
        # Sanity: the same detections over a short clip DO answer.
        assert (MIN_SAMPLES + 1) / 6.0 >= MIN_DETECTION_RATIO
        assert subject_center_x(face_track(centers), 0.0, 1.1) == pytest.approx(0.25)

    def test_opencv_absent_fallback_yields_nothing(self):
        """The variance heuristic knows presence, never position."""
        assert subject_center_x(face_track([]), 0.0, 4.0) is None

    def test_detections_hard_against_the_frame_edge_are_dropped(self):
        assert subject_center_x(face_track([0.01] * 20), 0.0, 4.0) is None
        assert subject_center_x(face_track([0.99] * 20), 0.0, 4.0) is None

    def test_missing_or_malformed_input(self):
        assert subject_center_x({}, 0.0, 4.0) is None
        assert subject_center_x(None, 0.0, 4.0) is None
        assert subject_center_x(face_track([0.3] * 20), 0.0, 0.0) is None
        assert subject_center_x(face_track([0.3] * 20), 4.0, 1.0) is None
        # A rate of 0 or a missing rate makes seconds-to-index meaningless,
        # so it must refuse rather than assume 5Hz and frame off the wrong
        # samples.
        assert subject_center_x(
            {"sample_rate_hz": 0, "face_center_x": [0.3] * 20}, 0.0, 4.0) is None
        assert subject_center_x(
            {"face_center_x": [0.3] * 20}, 0.0, 4.0) is None
        assert subject_center_x(
            {"sample_rate_hz": "fast", "face_center_x": [0.3] * 20}, 0.0, 4.0) is None

    def test_non_numeric_samples_are_skipped_not_crashed(self):
        centers = ["nonsense", None, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3]
        assert subject_center_x(face_track(centers), 0.0, 2.0) == pytest.approx(0.3)


class TestSubjectCentersByClip:

    def test_reads_a_flat_index(self):
        idx = {"clip_001": {"face_presence": face_track([0.3] * 10)}}
        assert "clip_001" in subject_centers_by_clip(idx)

    def test_reads_a_nested_index(self):
        idx = {"indices": {"clip_001": {"face_presence": face_track([0.3] * 10)}}}
        assert "clip_001" in subject_centers_by_clip(idx)

    def test_entries_without_face_presence_are_skipped(self):
        idx = {"clip_001": {"energy": {}}, "clip_002": "not a dict"}
        assert subject_centers_by_clip(idx) == {}

    def test_empty_input(self):
        assert subject_centers_by_clip({}) == {}
        assert subject_centers_by_clip(None) == {}

    def test_reads_the_shape_step_1_04_actually_emits(self):
        """`temporal_event_indices` is a LIST, and it is the only shape a real run
        produces (library/steps/step_1_04_temporal_index/step.py).

        Every test above this one feeds an invented mapping, so this
        function returned `{}` on every real project while passing its
        whole suite - and with no face track there is no pan, which makes
        a fill a blind centre crop.
        """
        idx = {
            "temporal_event_indices": [
                {"clip_id": "clip_011", "index_path": "/tmp/clip_011.json"},
            ],
            "temporal_event_indices": [
                {"clip_id": "clip_011",
                 "source_file": "/footage/IMG_1816.MOV",
                 "face_presence": face_track([0.3] * 10)},
            ],
            "total_indexed": 1,
            "index_dir": "/tmp",
        }
        got = subject_centers_by_clip(idx)
        assert "clip_011" in got
        # Keyed by file stem too, because the temporal index and the
        # catalog disagree about which is the id - the same split
        # semantic_index bridges.
        assert "IMG_1816" in got
        assert subject_center_x(got["clip_011"], 0.0, 2.0) == pytest.approx(0.3)

    def test_list_entries_without_face_presence_are_skipped(self):
        idx = {"temporal_event_indices": [{"clip_id": "clip_001"}, "not a dict"]}
        assert subject_centers_by_clip(idx) == {}


# ─────────────────────────────────────────────────────────
# 2. Position -> pixel pan, in _conform_fields
# ─────────────────────────────────────────────────────────

# 1920x1080 landscape into a 1080x1920 vertical timeline.
LANDSCAPE = {"clip_001": {"width": 1920, "height": 1080}}
VERTICAL = [1080, 1920]

# fit = 0.5625, fill = 1.7778, max_zoom = 3.1605
FIT = min(1080 / 1920, 1920 / 1080)
FULL_ZOOM = round(max(1080 / 1920, 1920 / 1080) / FIT, 4)


def conform(**kw):
    return _conform_fields(LANDSCAPE, "clip_001", VERTICAL, **kw)


class TestSubjectDrivesThePan:

    def test_subject_left_produces_a_positive_pan(self):
        """Crop window travels left, so the PICTURE travels right."""
        got = conform(framing_intent=1.0, subject_center_x=0.30)
        assert got["framing_pan_x"] > 0

    def test_subject_right_produces_a_negative_pan(self):
        got = conform(framing_intent=1.0, subject_center_x=0.70)
        assert got["framing_pan_x"] < 0

    def test_the_pan_actually_centres_the_subject(self):
        """The arithmetic, checked rather than assumed.

        At full fill the crop window is `target_w` wide over a source
        displayed `width * fit * zoom` wide. Panning by the returned value
        must put the subject in the middle of that window.
        """
        cx = 0.30
        got = conform(framing_intent=1.0, subject_center_x=cx)
        zoom = got["fill_zoom"]
        zoomed_w = 1920 * FIT * zoom
        # Where the subject sits along the displayed image, before panning.
        subject_disp_px = cx * zoomed_w
        # Centre of the crop window after the pan.
        window_centre = zoomed_w / 2.0 - got["framing_pan_x"]
        assert subject_disp_px == pytest.approx(window_centre, abs=1.0)

    def test_explicit_pan_beats_the_measurement(self):
        """A per-clip creative choice outranks the face detector."""
        got = conform(framing_intent=1.0, framing_pan_x=0.5,
                      subject_center_x=0.30)
        zoomed_w = 1920 * FIT * FULL_ZOOM
        max_pan = (zoomed_w - 1080) / 2.0
        assert got["framing_pan_x"] == pytest.approx(round(0.5 * max_pan, 2))

    def test_pan_is_clamped_inside_the_source(self):
        """An extreme subject cannot pan the crop off the picture."""
        for cx in (0.06, 0.94):
            got = conform(framing_intent=1.0, subject_center_x=cx)
            zoomed_w = 1920 * FIT * got["fill_zoom"]
            max_pan = (zoomed_w - 1080) / 2.0
            assert abs(got["framing_pan_x"]) <= max_pan + 0.01

    def test_partial_intent_pans_proportionally_less(self):
        """Less zoom means less slack, so less pan is available."""
        full = conform(framing_intent=1.0, subject_center_x=0.30)
        half = conform(framing_intent=0.5, subject_center_x=0.30)
        assert 0 < half["framing_pan_x"] < full["framing_pan_x"]


class TestSubjectFramingRespectsTheDeclaration:
    """Subject tracking moves the crop window; it never overrides the
    declared framing.

    This class used to assert "letterbox stays the default" and that an
    unset intent ignored the subject entirely. Both were true and both
    were the defect: the default letterboxed every talking-head clip, so
    the tracking never ran anywhere. The default is fill now
    (library/tools/framing_intent.py) and a project that wants bars
    declares 0.0 - which these first two cases still prove works.
    """

    def test_letterbox_intent_ignores_the_subject(self):
        got = conform(framing_intent=0.0, subject_center_x=0.30)
        assert got == {"needs_conform": False, "framing_intent": 0.0,
                       "framing_delivered": 0.0}

    def test_unset_intent_tracks_the_subject(self):
        """No declaration means the default, and the default fills - so
        the pan runs rather than sitting idle."""
        with_subject = conform(framing_intent=None, subject_center_x=0.30)
        without = conform(framing_intent=None)
        assert with_subject["needs_conform"] is True
        assert with_subject["framing_pan_x"] > 0
        assert "framing_pan_x" not in without

    def test_no_subject_means_no_pan_key_at_all(self):
        """A clip the detector could not locate crops from the centre."""
        got = conform(framing_intent=1.0, subject_center_x=None)
        assert "framing_pan_x" not in got
        assert got["fill_zoom"] == FULL_ZOOM

    def test_matching_aspect_never_pans(self):
        square = {"clip_001": {"width": 1080, "height": 1920}}
        got = _conform_fields(square, "clip_001", VERTICAL,
                              framing_intent=1.0, subject_center_x=0.2)
        assert got == {"needs_conform": False, "framing_intent": 1.0,
                       "framing_delivered": 1.0}


# ─────────────────────────────────────────────────────────
# 3. The contract with the renderer
# ─────────────────────────────────────────────────────────

def test_the_pan_reaches_a_property_resolve_accepts():
    """Producing a value is not delivering it.

    `framing_pan_x` sat in the manifest for the life of P1.1 while the
    renderer wrote it to `PanX`, which Resolve does not have. This drives
    the real `_apply_conform` with a fake that refuses unknown names.
    """
    from tests.test_framing_parameter import FakeTimelineItem
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "library", "steps", "step_6_01_render"))
    from resolve_build_timeline import _apply_conform

    clip = conform(framing_intent=1.0, subject_center_x=0.30)
    clip["label"] = "subject_tracked"
    assert clip["framing_pan_x"] > 0

    item = FakeTimelineItem()
    results = {"warnings": []}
    _apply_conform(item, clip, results)

    assert item.refused == [], f"Resolve would refuse {item.refused}"
    assert item.properties["Pan"] == clip["framing_pan_x"]
    assert item.properties["ZoomX"] == clip["fill_zoom"]
    assert not results["warnings"]
