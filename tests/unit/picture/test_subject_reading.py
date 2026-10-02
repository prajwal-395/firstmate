"""The two Nones in the aim are different facts, and the reel aim is recorded.

`subject_framing.subject_center_x` returns None both when the subject
was measured sitting at the centre and when nothing could be measured
at all.  A caller that treats the second None the way it treats the
first aims a crop on no measurement - the defect R5 names.  These tests
pin the distinction (`subject_center_reading`) and the shelf the reel
aim is filed on (the scratch sidecar): a recorded aim must survive a
machine whose face detector is gone, because that is the failure that
kept moving the picture.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.subject_framing import (
    SubjectPoint,
    read_recorded_subject,
    record_subject_measurement,
    subject_center_reading,
    subject_measurements_path,
)

RATE = 5


def face_track(centers, rate=RATE):
    return {"sample_rate_hz": rate, "face_center_x": list(centers)}


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
