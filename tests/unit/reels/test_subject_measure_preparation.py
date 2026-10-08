from types import SimpleNamespace

import pytest

from library.tools import reel_build


def _placement(path="/footage/a.mov", source_in=1.0, source_out=2.0):
    return {
        "clip": SimpleNamespace(track_type="video", source_file=path),
        "source_in": source_in,
        "source_out": source_out,
    }


def test_subject_measurement_runs_during_preparation_and_is_reused(
        monkeypatch):
    subject = object()
    calls = []

    def measure(path, start, end):
        calls.append((path, start, end))
        measure.last_provenance = {"basis": "probed"}
        return subject

    measure.last_provenance = None
    monkeypatch.setattr(
        reel_build, "_recorded_first_measure", lambda _folder: measure)
    prepared = reel_build._prepare_subject_measurements(
        [_placement(), _placement()], "/project")

    assert calls == [("/footage/a.mov", 1.0, 2.0)]
    assert prepared("/footage/a.mov", 1.0, 2.0) is subject
    assert prepared.last_provenance == {"basis": "probed"}
    assert calls == [("/footage/a.mov", 1.0, 2.0)]


def test_subject_probe_failure_is_cached_outside_the_lease(monkeypatch):
    from library.tools.subject_framing import SubjectProbeUnavailable

    calls = []

    def measure(path, start, end):
        calls.append((path, start, end))
        raise SubjectProbeUnavailable("detector unavailable")

    measure.last_provenance = None
    monkeypatch.setattr(
        reel_build, "_recorded_first_measure", lambda _folder: measure)
    prepared = reel_build._prepare_subject_measurements(
        [_placement()], "/project")

    assert calls == [("/footage/a.mov", 1.0, 2.0)]
    with pytest.raises(SubjectProbeUnavailable, match="detector unavailable"):
        prepared("/footage/a.mov", 1.0, 2.0)
    assert calls == [("/footage/a.mov", 1.0, 2.0)]
