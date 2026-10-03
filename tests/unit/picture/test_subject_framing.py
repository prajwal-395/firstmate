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
from __future__ import annotations
import os
import sys
import pytest
import json
from unittest.mock import patch
import shutil
import numpy as np


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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

def test_the_pan_reaches_a_property_resolve_accepts(tmp_path):
    """Producing a value is not delivering it.

    `framing_pan_x` sat in the manifest for the life of P1.1 while the
    renderer wrote it to `PanX`, which Resolve does not have. This drives
    the real `_apply_conform` with a fake that refuses unknown names.
    """
    from tests.unit.picture.test_framing_intent import _item
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
    _apply_conform(
        item, clip, results, frame_size=(1080, 1920),
        write_context={"project_folder": str(tmp_path),
                       "timeline_name": "subject-framing-test"})

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


# --------------------------------------------------------------------------
# From test_subject_probe_unavailable.py
#
# A probe with no detector raises; it never answers None.
#
# None means "frames were read and no face was measured" (genuine
# absence: the shot stays uncropped). A detector that could not be loaded
# raises `SubjectProbeUnavailable` before decoding, so the build refuses
# with the cause. Incident (Reel 09): `docs/evidence/subject_probe_unavailable.md`.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.subject_framing import measure_subject_in_window


def test_a_probe_with_no_detector_raises_rather_than_answering_none(
        monkeypatch):
    """Incapacity is not absence - the Reel 09 identical-rectangle cause.

    Before: this returned None when the cascade would not load, which
    reads as "no face in this shot".  Now it raises, before decoding.
    """
    import library.tools.subject_framing as framing

    monkeypatch.setattr(framing, "load_face_cascade", lambda: None)
    with pytest.raises(Exception, match="face detector"):
        measure_subject_in_window("/x/LC4932.MXF", 1421.571, 1426.656)


def test_genuine_absence_is_still_none_not_unavailable(monkeypatch, tmp_path):
    """A zero-length window has nothing to measure, and a detector that
    looked at undecodable footage found nothing: both are absence."""
    import library.tools.subject_framing as framing

    assert measure_subject_in_window(
        str(tmp_path / "absent.MXF"), 0.0, 5.0, cascade=object()) is None
    monkeypatch.setattr(framing, "load_face_cascade", lambda: None)
    assert measure_subject_in_window("/x/LC4932.MXF", 5.0, 5.0) is None


# --------------------------------------------------------------------------
# From test_subject_reading.py
#
# The two Nones in the aim are different facts, and the reel aim is recorded.
#
# `subject_framing.subject_center_x` returns None both when the subject
# was measured sitting at the centre and when nothing could be measured
# at all.  A caller that treats the second None the way it treats the
# first aims a crop on no measurement - the defect R5 names.  These tests
# pin the distinction (`subject_center_reading`) and the shelf the reel
# aim is filed on (the scratch sidecar): a recorded aim must survive a
# machine whose face detector is gone, because that is the failure that
# kept moving the picture.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.subject_framing import (
    SubjectPoint,
    read_recorded_subject,
    record_subject_measurement,
    subject_center_reading,
    subject_measurements_path,
)


class TestCentredIsNotUnmeasurable:
    """The confusion test: two Nones, two statuses, stated inputs."""

    def test_measured_centred(self):
        # Twenty samples at 0.50-0.52: every sample detects, the median
        # sits inside CENTRE_DEADBAND. Measured, and the answer is
        # "leave it centred".
        reading = subject_center_reading(
            face_track([0.50] * 10 + [0.52] * 10), 0.0, 4.0)
        assert reading.position is None
        assert reading.status == "centred"
        assert reading.reason == "measured_centred"

    def test_unmeasurable_sparse_or_bad_input(self):
        # Five detections scattered over sixty samples: the detector
        # saw something, but below MIN_DETECTION_RATIO. No measurement.
        centers = [None] * 60
        for i in range(5):
            centers[i * 3] = 0.50
        reading = subject_center_reading(face_track(centers), 0.0, 12.0)
        assert reading.position is None
        assert reading.status == "unmeasurable"
        assert reading.reason == "detection_ratio_below_floor"
        # Bad input is unmeasurable too - never "centred".
        for bad in ({}, {"sample_rate_hz": 0, "face_center_x": [0.3] * 20},
                    {"face_center_x": [0.3] * 20}):
            reading = subject_center_reading(bad, 0.0, 4.0)
            assert reading.position is None
            assert reading.status == "unmeasurable"

    def test_off_centre_carries_a_number(self):
        reading = subject_center_reading(
            face_track([0.30] * 20), 0.0, 4.0)
        assert reading.position == pytest.approx(0.30)
        assert reading.status == "off_centre"

def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return str(project)


def _footage(tmp_path, name="LC4932.MXF"):
    path = tmp_path / name
    path.write_bytes(b"fake footage")
    return str(path)


class TestRecordedAim:

    def test_recorded_absence_is_a_hit_not_a_miss(self, tmp_path):
        """"Looked, no face" must not be re-probed on every rebuild."""
        project = _project(tmp_path)
        source = _footage(tmp_path)
        record_subject_measurement(project, source, 10.0, 15.0, None)

        back, hit = read_recorded_subject(project, source, 10.0, 15.0)
        assert back is None
        assert hit is not None and hit["basis"] == "recorded"

    def test_changed_footage_other_window_or_corrupt_sidecar_misses(
            self, tmp_path):
        project = _project(tmp_path)
        source = _footage(tmp_path)
        point = SubjectPoint(center_x=0.48, center_y=0.32, width=0.10,
                             samples=12, detected=11, others=0)
        record_subject_measurement(project, source, 10.0, 15.0, point)
        with open(source, "ab") as f:
            f.write(b"more bytes")

        _back, hit = read_recorded_subject(project, source, 10.0, 15.0)
        assert hit is None
        assert _back is None

        source = _footage(tmp_path, "OTHER.MXF")
        record_subject_measurement(project, source, 10.0, 15.0, point)
        _, hit = read_recorded_subject(project, source, 20.0, 25.0)
        assert hit is None

        path = subject_measurements_path(project)
        path.write_text("{not json", encoding="utf-8")
        _back, hit = read_recorded_subject(project, source, 10.0, 15.0)
        assert hit is None
        assert _back is None

    def test_reprobe_replaces_the_record(self, tmp_path):
        project = _project(tmp_path)
        source = _footage(tmp_path)
        first = SubjectPoint(center_x=0.48, center_y=0.32, width=0.10,
                             samples=12, detected=11, others=0)
        second = SubjectPoint(center_x=0.55, center_y=0.30, width=0.09,
                              samples=12, detected=12, others=0)
        record_subject_measurement(project, source, 10.0, 15.0, first)
        record_subject_measurement(project, source, 10.0, 15.0, second)

        back, _ = read_recorded_subject(project, source, 10.0, 15.0)
        assert back == second
        with open(subject_measurements_path(project),
                   encoding="utf-8") as f:
            assert len(json.load(f)["entries"]) == 1


class TestRecordedFirstChain:
    """The reel default: records warm the aim, misses probe once."""

    def test_hit_never_touches_the_probe(self, tmp_path, monkeypatch):
        from library.tools import subject_framing
        from library.tools.reel_build import _recorded_first_measure

        project = _project(tmp_path)
        source = _footage(tmp_path)
        point = SubjectPoint(center_x=0.48, center_y=0.32, width=0.10,
                             samples=12, detected=11, others=0)
        record_subject_measurement(project, source, 10.0, 15.0, point)

        def _probe_must_not_run(*args):
            raise AssertionError("probe ran on a recorded window")

        monkeypatch.setattr(subject_framing, "measure_subject_in_window",
                            _probe_must_not_run)
        measure = _recorded_first_measure(project)
        assert measure(source, 10.0, 15.0) == point
        assert measure.last_provenance["basis"] == "recorded"

    def test_miss_probes_once_then_records(self, tmp_path, monkeypatch):
        from library.tools import subject_framing
        from library.tools.reel_build import _recorded_first_measure

        project = _project(tmp_path)
        source = _footage(tmp_path)
        point = SubjectPoint(center_x=0.48, center_y=0.32, width=0.10,
                             samples=12, detected=11, others=0)
        calls = []

        def _fake_probe(video_path, source_in, source_out, **kw):
            calls.append((video_path, source_in, source_out))
            return point

        monkeypatch.setattr(subject_framing, "measure_subject_in_window",
                            _fake_probe)
        measure = _recorded_first_measure(project)
        assert measure(source, 10.0, 15.0) == point
        assert len(calls) == 1
        assert measure.last_provenance["basis"] == "probed"

        back, hit = read_recorded_subject(project, source, 10.0, 15.0)
        assert back == point and hit["basis"] == "recorded"


# --------------------------------------------------------------------------
# From test_subject_survives_the_conform.py
#
# The subject survives being cropped into the delivery frame.
#
# `test_subject_framing.py` covers aiming the crop at the subject.  This
# covers the thing aiming could never fix: a crop that is NARROWER than the
# subject.  Filling a 1080x1920 frame from 1920x1080 source keeps 31.6% of
# the source width, project 001's speaker measures 37.7% of it, and the
# delivered frame therefore cut his face on the most important line of the
# edit with every gate in the pipeline passing.
#
# Four layers, and each can go quiet on its own:
#
# 1. `subject_framing.subject_box` - reducing the 5Hz face track to a
#    position AND a width, and refusing when the footage cannot support one.
# 2. `compile_manifest._conform_fields` - deciding whether the crop holds
#    the subject, and switching to the backdrop composition when it does not.
# 3. `fusion.comp_builder.build_effect_comp` - drawing that composition.
#    A parameter nothing reads produces a comp without the effect and no
#    warning (AGENTS.md section 10.2).
# 4. The two checks: `manifest_validator` on the plan, `render_qa` on the
#    render.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import render_qa
from library.tools.manifest_validator import validate_manifest_semantics
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.subject_framing import (
    SUBJECT_HEADROOM,
    subject_box,
)

VERTICAL_2 = (1080, 1920)
PORTRAIT = {"clip_001": {"width": 1080, "height": 1920}}
FIT_2 = 1080 / 1920

# 001's own numbers, measured on IMG_1818 at t=44.0s of the master.
SUBJECT_001 = 0.3766
CENTRE_001 = 0.3926


def track(centers, widths, rate=RATE):
    return {"sample_rate_hz": rate,
            "face_center_x": list(centers),
            "face_width": list(widths)}


def conform_2(**kw):
    return _conform_fields(LANDSCAPE, "clip_001", VERTICAL_2, **kw)


# ─────────────────────────────────────────────────────────
# 1. Reducing the track to a box
# ─────────────────────────────────────────────────────────

class TestSubjectBox:

    def test_position_and_width_together(self):
        box = subject_box(track([0.4] * 10, [0.3] * 10), 0.0, 2.0)
        assert box.center_x == pytest.approx(0.4)
        assert box.width == pytest.approx(0.3)

    def test_width_is_a_high_percentile_not_the_median(self):
        """The frame that matters is the one they lean closest on."""
        widths = [0.20] * 8 + [0.40] * 2
        box = subject_box(track([0.5] * 10, widths), 0.0, 2.0)
        assert box.width > 0.20, "a median window crops the closest frame"

    def test_required_crop_carries_headroom_on_both_sides(self):
        box = subject_box(track([0.5] * 10, [0.30] * 10), 0.0, 2.0)
        assert box.required_crop_width() == pytest.approx(
            0.30 * (1 + 2 * SUBJECT_HEADROOM))

    def test_an_unsupported_width_answers_none_not_a_guess(self):
        # Every run before this measurement existed: no widths at all.
        old = {"sample_rate_hz": RATE, "face_center_x": [0.4] * 10}
        assert subject_box(old, 0.0, 2.0) is None
        widths = [None] * 10
        widths[0] = 0.3
        assert subject_box(track([0.4] * 10, widths), 0.0, 2.0) is None
        # A box covering the whole frame would collapse the conform.
        assert subject_box(track([0.5] * 10, [0.95] * 10), 0.0, 2.0) is None

    def test_only_the_clips_own_range_is_read(self):
        widths = [0.20] * 10 + [0.45] * 10
        box = subject_box(track([0.5] * 20, widths), 0.0, 1.8)
        assert box.width == pytest.approx(0.20)


# ─────────────────────────────────────────────────────────
# 2. The conform decision
# ─────────────────────────────────────────────────────────

class TestTheConformHoldsTheSubject:

    def test_a_subject_that_fits_keeps_the_plain_crop(self):
        """Nothing changes for footage the fill crop can already hold."""
        got = conform_2(framing_intent=1.0, subject_center_x=0.30,
                      subject_width=0.20)
        assert "framing_backdrop" not in got
        assert got["framing_pan_x"] > 0
        assert got["fill_zoom"] == pytest.approx(3.1605, abs=1e-3)

    def test_001s_own_subject_does_not_fit_and_switches_route(self):
        got = conform_2(framing_intent=1.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        assert "framing_backdrop" in got, (
            "a 37.7% subject cannot survive a 31.6% crop")
        assert "framing_pan_x" not in got, (
            "the backdrop shows the whole width - there is nothing to aim, "
            "and a pan slides the composition out of the frame")
        # ...and the visible width really holds the subject.
        visible = got["framing_backdrop"]["visible_source_width"]
        assert visible >= SUBJECT_001 * (1 + 2 * SUBJECT_HEADROOM) - 1e-4

    def test_the_composition_centres_the_subject_in_the_frame(self):
        """The arithmetic, checked rather than assumed.

        Resolve crops the central ``1 / fill_zoom`` column of the comp
        canvas.  The subject must land in the middle of THAT column.
        """
        got = conform_2(framing_intent=1.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        bd = got["framing_backdrop"]
        # Where the subject sits along the canvas after the comp's
        # Transform, in canvas-width units.
        placed = bd["picture_center_x"] + (CENTRE_001 - 0.5) * bd["picture_scale"]
        assert placed == pytest.approx(0.5, abs=1e-3)

    def test_the_picture_always_covers_the_cropped_column(self):
        """However extreme the subject, no bare frame edge slides in."""
        for cx in (0.06, 0.5, 0.94):
            got = conform_2(framing_intent=1.0, subject_center_x=cx,
                          subject_width=0.60)
            bd = got["framing_backdrop"]
            column = 1.0 / got["fill_zoom"]
            for scale, centre in ((bd["picture_scale"], bd["picture_center_x"]),
                                  (bd["backdrop_scale"], bd["backdrop_center_x"])):
                left = centre - scale / 2.0
                right = centre + scale / 2.0
                assert left <= 0.5 - column / 2.0 + 1e-3
                assert right >= 0.5 + column / 2.0 - 1e-3

    def test_an_explicit_pan_still_outranks_the_measurement(self):
        """A per-clip creative choice says "aim here" and keeps the crop."""
        got = conform_2(framing_intent=1.0, framing_pan_x=0.5,
                      subject_center_x=CENTRE_001, subject_width=SUBJECT_001)
        assert "framing_backdrop" not in got
        assert got["framing_pan_x"] > 0
        assert got["subject_safe_zoom"] < got["fill_zoom"], (
            "the measurement is still recorded, so the plan check can "
            "report what the creative choice cost")

    def test_portrait_source_is_never_width_limited(self):
        """A vertical clip in a vertical frame crops height, not width."""
        got = _conform_fields(PORTRAIT, "clip_001", (1080, 1080),
                              framing_intent=1.0, subject_center_x=0.4,
                              subject_width=0.9)
        assert "framing_backdrop" not in got
        assert "subject_safe_zoom" not in got

    def test_no_measurement_changes_nothing(self):
        """Every project whose index predates `face_width`."""
        with_none = conform_2(framing_intent=1.0, subject_center_x=CENTRE_001)
        assert "framing_backdrop" not in with_none
        assert "subject_width" not in with_none


# ─────────────────────────────────────────────────────────
# 3. The comp that draws it
# ─────────────────────────────────────────────────────────

def backdrop_effects(**over):
    effects = {
        "backdrop_picture_scale": 0.6463,
        "backdrop_picture_center_x": 0.5694,
        "backdrop_scale": 1.0,
        "backdrop_center_x": 0.6074,
        "vignette": False,
    }
    effects.update(over)
    return effects


class TestTheBackdropReachesThePicture:

    def test_it_draws_both_branches_from_the_same_media_in(self):
        """One frame, shown twice - not a second clip."""
        comp = build_effect_comp(backdrop_effects(), clip_dur=300,
                                 source_res=(1920, 1080))
        assert "Blur {" in comp
        assert comp.count("Transform {") >= 2
        assert "Merge {" in comp
        assert comp.count('SourceOp = "MediaIn1"') == 2


    def test_the_backdrop_is_upstream_of_the_ken_burns_drift(self):
        """The drift must move the composed picture, not the plate."""
        comp = build_effect_comp(
            backdrop_effects(zoom_start=1.0, zoom_mid=1.015, zoom_end=1.03),
            clip_dur=300, source_res=(1920, 1080))
        assert 'SourceOp = "Merge1"' in comp, (
            "the drift Transform must take the Merge as its input")

    def test_a_comp_without_the_parameter_draws_no_backdrop(self):
        comp = build_effect_comp({"vignette": False, "zoom_start": 1.0,
                                  "zoom_mid": 1.0, "zoom_end": 1.0},
                                 clip_dur=300, source_res=(1920, 1080))
        assert "Blur {" not in comp

    def test_the_conforms_own_output_is_what_the_builder_reads(self):
        """The two halves are joined by compile_manifest; this asserts the
        names really match rather than trusting that they do."""
        got = conform_2(framing_intent=1.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        bd = got["framing_backdrop"]
        comp = build_effect_comp({
            "backdrop_picture_scale": bd["picture_scale"],
            "backdrop_picture_center_x": bd["picture_center_x"],
            "backdrop_scale": bd["backdrop_scale"],
            "backdrop_center_x": bd["backdrop_center_x"],
            "vignette": False,
        }, clip_dur=300, source_res=(1920, 1080))
        assert "Blur {" in comp
        assert str(bd["picture_scale"]) in comp


# ─────────────────────────────────────────────────────────
# 4a. The plan check - the half that carries the verdict
# ─────────────────────────────────────────────────────────

def manifest_with(clip):
    base = {"clip_id": "clip_001", "label": "speech_15_seg0",
            "source_file": "/x/a.mov", "source_in": 1.0, "source_out": 3.0,
            "timeline_in": 0.0, "timeline_out": 2.0}
    base.update(clip)
    return {"tracks": {"V1": {"clips": [base]}, "V2": {"clips": []}}}


def subject_errors(manifest):
    return [e for e in validate_manifest_semantics(manifest)
            if "crops the subject" in e]


class TestThePlanCheck:

    def test_a_crop_narrower_than_the_subject_fails(self):
        errors = subject_errors(manifest_with({
            "needs_conform": True, "fill_zoom": 3.1605,
            "subject_width": SUBJECT_001, "framing_pan_x": 366.59,
        }))
        assert errors, "001's shipped geometry must not pass"
        assert "speech_15_seg0" in errors[0]

    def test_a_wide_enough_or_unmeasured_crop_passes(self):
        assert not subject_errors(manifest_with({
            "needs_conform": True, "fill_zoom": 2.0,
            "subject_width": 0.20,
        }))
        assert not subject_errors(manifest_with({
            "needs_conform": True, "fill_zoom": 3.1605,
        }))

    def test_the_compilers_own_output_passes_its_own_check(self):
        clip = {"label": "speech_15_seg0", "source_file": "/x/a.mov",
                "source_in": 1.0, "source_out": 3.0,
                "timeline_in": 0.0, "timeline_out": 2.0}
        clip.update(conform_2(framing_intent=1.0, subject_center_x=CENTRE_001,
                            subject_width=SUBJECT_001))
        assert not subject_errors(manifest_with(clip))


# ─────────────────────────────────────────────────────────
# 4b. The render check - the backstop
# ─────────────────────────────────────────────────────────

class FakeCascade:
    """A cascade that finds exactly the box the test wants it to."""

    def __init__(self, box):
        self.box = box

    def empty(self):
        return False

    def detectMultiScale(self, *_a, **_kw):
        return [] if self.box is None else [self.box]


def face_intact_with(box, w=480, h=854, frames=6):
    import numpy as np
    stream = iter([np.zeros((1, h, w), dtype=np.uint8) for _ in range(frames)])
    with patch.object(render_qa, "_probe_video_size", return_value=(1080, 1920)), \
            patch.object(render_qa, "_stream_raw_frames", return_value=stream), \
            patch.object(render_qa, "load_face_cascade",
                         return_value=FakeCascade(box)):
        return render_qa.measure_face_intact("/x/master.mp4")


class TestTheRenderCheck:

    def test_a_face_against_the_frame_edge_fails(self):
        result = face_intact_with((0, 200, 300, 300))
        assert result.passed is False
        assert result.severity == "error"
        assert "left" in result.value["examples"][0]["edges"]

    def test_a_face_clear_of_every_edge_passes(self):
        result = face_intact_with((90, 200, 300, 300))
        assert result.passed is True
        assert result.value["cropped_frames"] == 0

    def test_a_render_with_no_face_passes_and_says_so(self):
        result = face_intact_with(None)
        assert result.passed is True
        assert result.value["face_frames"] == 0
        assert "nothing to crop" in result.detail

    def test_a_detection_too_small_to_be_the_speaker_is_ignored(self):
        """A passer-by at the frame edge must not fail a correct render."""
        result = face_intact_with((0, 200, 40, 40))
        assert result.passed is True
        assert result.value["face_frames"] == 0


# --------------------------------------------------------------------------
# From test_body_pose_framing.py
#
# Both speakers centred on body pose, bounded by the face - captain, 2026-10-01.
#
# See `docs/evidence/body_pose_framing.md` for the incident and invariant.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.subject_framing import (
    BODY_JOINT_MIN_LANDMARKS,
    _aim_center_x,
    _body_centroid_x,
    _nearest_body_joints,
    _probe_body_centroids,
)


class TestAimCenterX:
    """The aim favours the body, never past the face's own headroom."""

    def test_body_within_headroom_wins_outright(self):
        # SpeakerOne's measured case (report.md 2.3): body sits ~0.022 of
        # source width right of her face, well inside a 0.3-wide face's
        # SUBJECT_HEADROOM (0.15 * 0.3 = 0.045) budget.
        aim, basis = _aim_center_x(face_cx=0.40, face_width=0.30,
                                   body_cx=0.422)
        assert basis == "body_pose"
        assert aim == 0.422

    def test_body_past_headroom_is_clamped_not_ignored(self):
        face_cx, face_width = 0.40, 0.10
        bound = SUBJECT_HEADROOM * face_width
        aim, basis = _aim_center_x(face_cx=face_cx, face_width=face_width,
                                   body_cx=0.80)
        assert basis == "body_pose"
        assert aim == face_cx + bound

    def test_no_body_measurement_keeps_the_old_face_aim(self):
        aim, basis = _aim_center_x(face_cx=0.52, face_width=0.20,
                                   body_cx=None)
        assert (aim, basis) == (0.52, "face")

class TestBodyCentroid:
    def test_needs_the_minimum_landmark_count(self):
        joints = {
            "left_shoulder_1_joint": [0.40, 0.5, 0.9],
            "right_shoulder_1_joint": [0.60, 0.5, 0.9],
        }
        assert len(joints) < BODY_JOINT_MIN_LANDMARKS + 2
        assert _body_centroid_x(joints) is None  # only 2 of 4 joints

    def test_low_confidence_joints_do_not_count(self):
        # Three of the four joints clear the confidence floor - still
        # enough to average - but the low-confidence forearm must be
        # excluded from the mean, not merely ignored as "missing".
        joints = {
            "left_shoulder_1_joint": [0.40, 0.5, 0.9],
            "right_shoulder_1_joint": [0.60, 0.5, 0.9],
            "left_forearm_joint": [0.0, 0.6, 0.1],  # below the floor
            "right_forearm_joint": [0.80, 0.6, 0.9],
        }
        assert _body_centroid_x(joints) == (0.40 + 0.60 + 0.80) / 3

    def test_mean_of_the_four_upper_body_joints(self):
        joints = {
            "left_shoulder_1_joint": [0.40, 0.5, 0.9],
            "right_shoulder_1_joint": [0.60, 0.5, 0.9],
            "left_forearm_joint": [0.35, 0.6, 0.9],
            "right_forearm_joint": [0.65, 0.6, 0.9],
            "head_joint": [0.50, 0.1, 0.9],  # not one of the four - ignored
        }
        assert _body_centroid_x(joints) == 0.5


class TestNearestBody:
    def test_picks_the_body_whose_neck_is_closest_to_the_speaker(self):
        bodies = [
            {"joints": {"neck_1_joint": [0.80, 0.4, 0.9]}},  # background
            {"joints": {"neck_1_joint": [0.42, 0.4, 0.9]}},  # the speaker
        ]
        joints = _nearest_body_joints(bodies, near_x=0.40)
        assert joints == bodies[1]["joints"]

    def test_a_body_with_no_neck_is_skipped(self):
        bodies = [{"joints": {"left_shoulder_1_joint": [0.5, 0.5, 0.9]}}]
        assert _nearest_body_joints(bodies, near_x=0.5) is None


class TestProbeBodyCentroids:
    def test_vision_unavailable_returns_none_not_a_crash(self, monkeypatch):
        import library.steps.step_1_04_temporal_index.vision_measure as vm

        monkeypatch.setattr(vm, "ensure_helper", lambda: (None, "no swiftc"))
        assert _probe_body_centroids(["/x/f0.png"], [0.5]) is None

    def test_per_frame_alignment_with_the_nearest_face(self, monkeypatch):
        import library.steps.step_1_04_temporal_index.vision_measure as vm

        monkeypatch.setattr(vm, "ensure_helper", lambda: ("/bin/true", None))

        def _fake_measure_frames(paths, helper_path):
            return [
                {"bodies": [{"joints": {
                    "neck_1_joint": [0.40, 0.3, 0.9],
                    "left_shoulder_1_joint": [0.36, 0.45, 0.9],
                    "right_shoulder_1_joint": [0.46, 0.45, 0.9],
                    "left_forearm_joint": [0.34, 0.6, 0.9],
                    "right_forearm_joint": [0.48, 0.6, 0.9],
                }}]},
                {"bodies": []},  # no body this frame -> None, not a crash
            ]

        monkeypatch.setattr(vm, "measure_frames", _fake_measure_frames)
        out = _probe_body_centroids(["/x/f0.png", "/x/f1.png"], [0.40, 0.50])
        assert out[0] == (0.36 + 0.46 + 0.34 + 0.48) / 4
        assert out[1] is None


# --------------------------------------------------------------------------
# From test_behind_subject.py
#
# A title composited BEHIND the subject, precomposited under its matte.
#
# A behind_subject segment grounds against a real tracked subject and
# precomposites the rendered title under its inverted matte into a
# premultiplied `qtrle` overlay, placed as a normal clip on an overlay
# row above the picture. Anything that cannot be grounded REFUSES by
# name (`BehindSubjectRefused`) - never dropped with a reason, never
# drawn on top.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import behind_subject
from library.tools.analysis.object_segmentation import (
    FACE_SEED_LABEL,
    encode_rle,
)
from library.tools.overlay_carriage import (
    OVERLAY_FORMAT_NAME,
    assert_transparent_region_unchanged,
)
from library.tools.ren_refusal import RenRefusal


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg and ffprobe - CI installs both")


def _seg_result(frames=4, shape=(24, 32)):
    masks_rle = {}
    for f in range(frames):
        mask = np.zeros(shape, dtype=np.uint8)
        mask[6:18, 10:22] = 1
        masks_rle[str(f)] = encode_rle(mask)
    return {
        "video_path": "/footage/clip_001.mp4",
        "frame_count": frames,
        "resolution": list(shape),
        "sample_fps": 2.0,
        "seed_note": "face_seeded: one subject target",
        "objects": [{
            "object_id": "obj_1",
            "label": FACE_SEED_LABEL,
            "category": "person",
            "frames": list(range(frames)),
            "masks_rle": masks_rle,
            "bboxes": {str(f): [10, 6, 12, 12] for f in range(frames)},
            "avg_area_ratio": 0.18,
        }],
    }


def _clip(label="clip1", cid="c1", path="/footage/c1.mp4",
          tl_in=0.0, tl_out=10.0, source_in=0.0):
    return {"label": label, "clip_id": cid, "source_file": path,
            "timeline_in": tl_in, "timeline_out": tl_out,
            "source_in": source_in}


def _title_sequence(directory, stem="t_behind", count=40,
                    size=(32, 24), color=(255, 255, 255, 255)):
    """A 4.06-shaped behind title: numbered RGBA PNGs plus the record."""
    from PIL import Image

    os.makedirs(directory, exist_ok=True)
    pattern = os.path.join(directory, f"{stem}_%05d.png")
    for i in range(count):
        Image.fromarray(
            np.full((size[1], size[0], 4), color,
                    dtype=np.uint8), mode="RGBA").save(pattern % i)
    return {
        "segment_id": "t1",
        "overlay_path": pattern % 0,
        "timeline_start": 2.0,
        "timeline_end": 6.0,
        "total_frames": count,
        "sequence": {"pattern": pattern, "first_frame": pattern % 0,
                     "frame_count": count},
    }


# ── Grounding ───────────────────────────────────────────────────────

def test_what_cannot_ground_says_why():
    grounded, refusal = behind_subject.ground_segment(
        {"segment_id": "t1"}, None, clip_id="c1")
    assert grounded is None
    assert refusal["reason"] == "no_segmentation"
    seg = _seg_result()
    seg["objects"][0]["label"] = "auto_object_1"
    grounded, refusal = behind_subject.ground_segment(
        {"segment_id": "t1"}, seg, clip_id="c1")
    assert grounded is None
    assert refusal["reason"] == "subject_not_tracked"


def test_a_tracked_subject_grounds():
    grounded, refusal = behind_subject.ground_segment(
        {"segment_id": "t1"}, _seg_result(), clip_id="c1")
    assert refusal is None
    assert grounded["object_id"] == "obj_1"


# ── The refusal: no usable matte, by name ───────────────────────────

def test_what_cannot_be_precomposited_refuses_by_name(tmp_path):
    """No matte, no title file, no clip under the span, a title that runs
    dry, a matte or a title canvas at the wrong size: each raises
    `BehindSubjectRefused` (a `RenRefusal`), never silence."""
    def refuse(name, segment=None, seg=None, clips=None, meta=(32, 24)):
        segment = segment or _title_sequence(str(tmp_path / name))
        clips = [_clip()] if clips is None else clips
        with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
            behind_subject.apply_behind_subject(
                [segment], {"c1": seg},
                clip_at=lambda s, e: clips,
                matte_dir=str(tmp_path / name / "mattes"),
                precomp_dir=str(tmp_path / name / "precomp"),
                timeline_fps=10.0,
                clip_metadata={"c1": {"width": meta[0], "height": meta[1]}})
        assert isinstance(exc.value, RenRefusal)
        return str(exc.value)

    assert "no usable matte" in refuse("no_matte", seg=None)
    gone = _title_sequence(str(tmp_path / "gone_title"))
    gone["sequence"]["pattern"] = str(tmp_path / "gone" / "t_%05d.png")
    refuse("gone_title", segment=gone, seg=_seg_result())
    refuse("no_clip", seg=_seg_result(), clips=[])
    dry = _title_sequence(str(tmp_path / "dry"), count=10)
    assert "runs dry" in refuse("dry", segment=dry, seg=_seg_result())
    refuse("matte_size", seg=_seg_result(), meta=(64, 48))
    small = _title_sequence(str(tmp_path / "canvas"), size=(16, 16))
    assert "wrong pixels" in refuse("canvas", segment=small,
                                    seg=_seg_result())


# ── The precomposite ────────────────────────────────────────────────

@needs_ffmpeg
def test_a_grounded_segment_precomposites_under_the_matte(tmp_path):
    segment = _title_sequence(str(tmp_path / "titles"))
    placed, mattes, per_segment = behind_subject.apply_behind_subject(
        [segment],
        {"c1": _seg_result()},
        clip_at=lambda s, e: [_clip()],
        matte_dir=str(tmp_path / "mattes"),
        precomp_dir=str(tmp_path / "precomp"), timeline_fps=10.0,
        clip_metadata={"c1": {"width": 32, "height": 24}})
    assert len(mattes) == 1
    assert len(mattes[0]["files"]) == 40
    assert len(placed) == 1
    entry = placed[0]
    assert entry["layer"] == "behind_subject"
    assert entry["timeline_start"] == 2.0
    assert entry["timeline_end"] == 6.0
    assert entry["total_frames"] == 40
    assert entry["format"] == OVERLAY_FORMAT_NAME
    assert entry["has_alpha"] is True
    assert os.path.isfile(entry["overlay_path"])
    assert per_segment[0]["clips"] == ["c1"]
    assert per_segment[0]["precomps"][0]["overlay_path"] == \
        entry["overlay_path"]

    # The punch itself, read off the placed file: the subject's box
    # is transparent black, the title draws everywhere else.
    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", entry["overlay_path"],
         "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120, check=False)
    assert proc.returncode == 0
    frames = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(
        40, 24, 32, 4)
    mid = frames[20]
    assert (mid[6:18, 10:22, 3] == 0).all()
    assert (mid[6:18, 10:22, :3] == 0).all()
    assert (mid[0:6, :, 3] == 255).all()
    assert (mid[0:6, :, :3] == 255).all()

    # And the acceptance check from the scout report: composited over
    # a plate, the plate comes through untouched wherever the
    # overlay's own alpha is zero.
    plate = np.full((24, 32, 3), 128, dtype=np.uint8)
    alpha = mid[..., 3].astype(np.float64)
    comp = (mid[..., :3].astype(np.float64)
            + plate.astype(np.float64) * (255 - alpha)[..., None]
            / 255.0).astype(np.uint8)
    assert_transparent_region_unchanged(comp, plate, mid[..., 3],
                                        what="behind_subject precomp")


@needs_ffmpeg
def test_partial_alpha_and_partial_matte_multiply(tmp_path):
    from PIL import Image

    title = np.zeros((2, 2, 4), dtype=np.uint8)
    title[..., :] = (200, 100, 50, 128)
    title_path = str(tmp_path / "title.png")
    Image.fromarray(title, mode="RGBA").save(title_path)
    matte = np.array([[0, 128], [255, 0]], dtype=np.uint8)
    matte_path = str(tmp_path / "matte.png")
    Image.fromarray(matte, mode="L").save(matte_path)
    out = str(tmp_path / "precomp.mov")
    record = behind_subject.precomposite_title_under_matte(
        [title_path, title_path], [matte_path, matte_path], out,
        fps=10.0, segment_id="t", clip_id="c")
    assert record["frame_count"] == 2

    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", out,
         "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120, check=False)
    got = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(2, 2, 2, 4)
    # (200,100,50,128) over no matte: alpha stays 128, RGB scaled.
    assert got[0, 0, 0].tolist() == [100, 50, 25, 128]
    # Half matte: alpha 128 -> ~64, RGB follows the punched alpha.
    assert got[0, 0, 1, 3] == 64
    assert got[0, 0, 1, 0] == 50
    # Full matte: exact zeros - what the transparent check measures.
    assert got[0, 1, 0].tolist() == [0, 0, 0, 0]


@needs_ffmpeg
def test_two_titles_on_one_clip_punch_their_own_holes(tmp_path):
    first = _title_sequence(str(tmp_path / "titles"), stem="first")
    first["timeline_start"] = 1.0
    first["timeline_end"] = 3.0
    second = _title_sequence(str(tmp_path / "titles"), stem="second",
                             count=40)
    second["segment_id"] = "t2"
    second["timeline_start"] = 5.0
    second["timeline_end"] = 7.0
    placed, mattes, per_segment = behind_subject.apply_behind_subject(
        [first, second], {"c1": _seg_result()},
        clip_at=lambda s, e: [_clip()],
        matte_dir=str(tmp_path / "mattes"),
        precomp_dir=str(tmp_path / "precomp"), timeline_fps=10.0,
        clip_metadata={"c1": {"width": 32, "height": 24}})
    assert len(placed) == 2
    assert placed[0]["overlay_path"] != placed[1]["overlay_path"]
    assert all(os.path.isfile(p["overlay_path"]) for p in placed)
    assert [p["timeline_start"] for p in placed] == [1.0, 5.0]
    assert [s["segment_id"] for s in per_segment] == ["t1", "t2"]


# ── The registration guard ────────────────────────────────────────

def _behind_windows():
    return {"clip1": [("t1", 2.0, 6.0)]}


def test_a_geometric_op_on_the_behind_clip_refuses_by_name():
    """A zoom, a backdrop reframe or a stabilize moves the picture off
    the registered matte."""
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(),
            per_clip_effects={"clip1": {"zoom_start": 1.0,
                                        "zoom_end": 1.06}})
    assert "t1" in str(exc.value)
    assert "clip1" in str(exc.value)
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(),
            per_clip_effects={"clip1": {
                "backdrop_picture_scale": 0.8}})
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(), per_clip_effects={},
            stabilized_labels=["clip1"])
    assert "stabilize" in str(exc.value)


def test_an_overlapping_speed_ramp_refuses_but_a_disjoint_one_passes():
    with pytest.raises(behind_subject.BehindSubjectRefused):
        behind_subject.assert_no_geometric_overlap(
            _behind_windows(), per_clip_effects={},
            speed_ops=[{"label": "clip1", "effect_type": "speed_ramp",
                        "timeline_start": 5.0, "timeline_end": 8.0}])
    behind_subject.assert_no_geometric_overlap(
        _behind_windows(), per_clip_effects={},
        speed_ops=[{"label": "clip1", "effect_type": "speed_ramp",
                    "timeline_start": 6.0, "timeline_end": 8.0}])
    behind_subject.assert_no_geometric_overlap(
        _behind_windows(), per_clip_effects={},
        speed_ops=[{"label": "other", "effect_type": "speed_ramp",
                    "timeline_start": 2.0, "timeline_end": 6.0}])


def test_photometric_keys_do_not_trip_the_guard():
    behind_subject.assert_no_geometric_overlap(
        _behind_windows(),
        per_clip_effects={"clip1": {"grade_gain": 1.1,
                                    "glow_gain": 0.5,
                                    "subject_grade_matte": "/m.png"}})
    behind_subject.assert_no_geometric_overlap({})


# ── No comp path anymore ────────────────────────────────────────────

def test_stale_behind_keys_draw_no_loader_comp():
    comp = build_effect_comp(
        {"behind_title_media": "/titles/t.mov",
         "behind_title_trim_in": 3,
         "behind_title_trim_out": 30,
         "behind_subject_matte": "/mattes/m_00000.png"},
        300, source_res=(1080, 1920))
    assert "Loader" not in comp
    assert "BehindTitle" not in comp
    assert "SubjectOver" not in comp


# ── The validator half ──────────────────────────────────────────────

def test_a_behind_matte_missing_files_fails_validation(tmp_path):
    from library.tools import manifest_validator as mv
    record = {
        "clip_stem": "c1_clip1_behind", "object_id": "obj_1",
        "files": [str(tmp_path / "missing_00000.png")],
        "frame_count": 10, "resolution": [24, 32],
    }
    errors = mv.validate_manifest_semantics(
        {"tracks": {"V1": {"clips": []}},
         "behind_subject_mattes": [record]})
    assert any("behind_subject_mattes" in e for e in errors)
