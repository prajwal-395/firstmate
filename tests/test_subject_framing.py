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
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

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

    def test_the_centre_deadband(self):
        """Already centred means no pan, so today's output is unchanged."""
        assert subject_center_x(face_track([0.5] * 20), 0.0, 4.0) is None
        just_inside = 0.5 + CENTRE_DEADBAND / 2
        assert subject_center_x(face_track([just_inside] * 20), 0.0, 4.0) is None
        just_outside = 0.5 + CENTRE_DEADBAND * 2
        got = subject_center_x(face_track([just_outside] * 20), 0.0, 4.0)
        assert got == pytest.approx(just_outside)


class TestSubjectCenterRefusesToGuess:
    """Every path where the honest answer is None.

    A fabricated position is worse than none: it reads as a measurement
    and it moves the picture.
    """

    def test_no_or_too_few_or_edge_detections(self):
        assert subject_center_x(face_track([None] * 20), 0.0, 4.0) is None
        centers = [None] * 20
        for i in range(MIN_SAMPLES - 1):
            centers[i] = 0.3
        assert subject_center_x(face_track(centers), 0.0, 4.0) is None
        # The variance heuristic (OpenCV absent) knows presence, never
        # position.
        assert subject_center_x(face_track([]), 0.0, 4.0) is None
        # Detections hard against the frame edge are dropped.
        assert subject_center_x(face_track([0.01] * 20), 0.0, 4.0) is None
        assert subject_center_x(face_track([0.99] * 20), 0.0, 4.0) is None

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
    """The join reads the per-clip index FILES, never pipeline state.

    An earlier spelling of `subject_centers_by_clip` walked the in-state
    `full_indices` mapping while step 1.04 emits a LIST, so it returned
    `{}` on every real run while tests fed it invented mappings and
    passed. These tests therefore build the on-disk index the step
    really writes and read it back - no invented state shapes.
    """

    @staticmethod
    def _index_dir(tmp_path):
        d = (tmp_path / "pipeline_output" / "steps"
             / "1_04_temporal_index" / "index")
        d.mkdir(parents=True)
        return d

    @staticmethod
    def _write_clip(index_dir, name, entry):
        import json
        (index_dir / name).write_text(json.dumps(entry), encoding="utf-8")

    def test_reads_the_files_step_1_04_writes(self, tmp_path):
        index_dir = self._index_dir(tmp_path)
        self._write_clip(index_dir, "clip_011.json", {
            "clip_id": "clip_011",
            "source_file": "/footage/IMG_1816.MOV",
            "face_presence": face_track([0.3] * 10),
        })
        got = subject_centers_by_clip(str(tmp_path))
        assert "clip_011" in got
        # Keyed by file stem too, because the temporal index and the
        # catalog disagree about which is the id - the same split
        # semantic_index bridges.
        assert "IMG_1816" in got
        assert subject_center_x(got["clip_011"], 0.0, 2.0) == pytest.approx(0.3)

    def test_entries_without_face_presence_are_skipped(self, tmp_path):
        index_dir = self._index_dir(tmp_path)
        self._write_clip(index_dir, "clip_001.json",
                         {"clip_id": "clip_001", "energy": {}})
        (index_dir / "clip_002.json").write_text("not json{",
                                                 encoding="utf-8")
        assert subject_centers_by_clip(str(tmp_path)) == {}



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

    def test_the_pan_actually_centres_the_subject(self):
        """The arithmetic, checked rather than assumed.

        At full fill the crop window is `target_w` wide over a source
        displayed `width * fit * zoom` wide. Panning by the returned value
        must put the subject in the middle of that window.
        """
        # Subject left: the crop window travels left, so the PICTURE
        # travels right (positive pan); subject right, the reverse.
        for cx, sign in ((0.30, 1), (0.70, -1)):
            got = conform(framing_intent=1.0, subject_center_x=cx)
            assert got["framing_pan_x"] * sign > 0
            zoomed_w = 1920 * FIT * got["fill_zoom"]
            # Where the subject sits along the displayed image, before
            # panning, against the centre of the crop window after it.
            subject_disp_px = cx * zoomed_w
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

    def test_letterbox_intent_ignores_the_subject_and_the_crop_factor(self):
        got = conform(framing_intent=0.0, subject_center_x=0.30,
                      framing_crop_factor=1.3)
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
        """A clip the detector could not locate crops from the centre, at
        the standard fill ratio (1920x1080 into 1080x1920 is 3.1605x)."""
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
    from tests.test_framing_parameter import _item
    from library.steps.step_6_01_render.resolve_build_timeline import (
        _apply_conform,
    )
    from library.tools.resolve_transform import (
        FALLBACK_DRAW_GAIN,
        fit_base_scale,
        units_for_shift,
    )

    clip = conform(framing_intent=1.0, subject_center_x=0.30)
    clip["label"] = "subject_tracked"
    assert clip["framing_pan_x"] > 0

    item = _item(source_size=(3840, 2160))
    results = {"warnings": []}
    _apply_conform(item, clip, results, frame_size=(1080, 1920))

    assert item.refused == [], f"Resolve would refuse {item.refused}"
    # The manifest carries delivery pixels; Resolve takes units.  On a
    # 3840x2160 source in a 1080x1920 frame the geometry factor is
    # exactly 1 (the fit is width-bound), so the conversion IS the
    # measured draw gain: 682.67 px is Pan 341.335, not 682.67.
    # Driven against the law rather than restated, so the next
    # calibration moves this with the code instead of against it.
    base = fit_base_scale(3840, 2160, 1080, 1920)
    assert item.GetProperty("Pan") == pytest.approx(
        units_for_shift(clip["framing_pan_x"], 3840, 1080, base),
        abs=0.01)
    assert item.GetProperty("Pan") == pytest.approx(
        clip["framing_pan_x"] / FALLBACK_DRAW_GAIN, abs=0.01)
    assert item.GetProperty("ZoomX") == clip["fill_zoom"]
    assert not results["warnings"]
