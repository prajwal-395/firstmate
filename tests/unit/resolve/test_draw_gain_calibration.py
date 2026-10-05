"""The qualified per-format draw-gain calibration record."""

import json

import pytest

from library.tools import draw_gain_calibration as cal
from library.tools import draw_gain_probe as probe
from library.tools.draw_gain_probe import calibrate
from tests.resolve_double import FakeResolve, make_project


def test_lookup_returns_none_when_no_calibration_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    assert cal.lookup(resolve, 1080, 1920) is None


def test_store_then_lookup_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    cal.store(resolve, 1080, 1920, 4.0)
    assert cal.lookup(resolve, 1080, 1920) == 4.0


def test_lookup_is_keyed_by_version(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    cal.store(resolve, 1080, 1920, 4.0)

    class OtherVersion(FakeResolve):
        def GetVersionString(self):
            return "22.0.0.0"

    assert cal.lookup(OtherVersion(), 1080, 1920) is None


def test_lookup_is_keyed_by_dimensions(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    cal.store(resolve, 1080, 1920, 4.0)
    assert cal.lookup(resolve, 1920, 1080) is None


def test_store_refuses_unsane_gain(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    import pytest
    with pytest.raises(ValueError, match="outside"):
        cal.store(resolve, 1080, 1920, 99.0)


def test_lookup_returns_none_for_unsane_stored_gain(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    path = tmp_path / "cal.json"
    path.write_text(json.dumps({
        "calibrations": {"21.1.0.14:1080x1920": {"gain": 99.0}}
    }), encoding="utf-8")
    resolve = FakeResolve()
    assert cal.lookup(resolve, 1080, 1920) is None


def test_calibrate_returns_calibration_record(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    cal.store(resolve, 1080, 1920, 4.0)
    project = make_project()
    record = calibrate(resolve, project, (1080, 1920))
    assert record["source"] == "calibration_record"
    assert record["gain"] == 4.0


def test_calibrate_refuses_when_no_calibration(tmp_path, monkeypatch):
    """A missing calibration is never filled with a guessed gain.

    The gain is renderer state that can move between Resolve versions
    and delivery geometries, so a missing entry refuses with the fix:
    measure in a disposable project.
    """
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    project = make_project()
    with pytest.raises(probe.DrawGainCalibrationMissing) as exc:
        calibrate(resolve, project, (1080, 1920))
    assert "no qualified draw-gain calibration for 1080x1920" in str(exc.value)
    assert "populate_calibration.py" in str(exc.value)


def test_calibrate_does_not_write_resolution(tmp_path, monkeypatch):
    """The probe must not mutate any timeline or project resolution."""
    monkeypatch.setattr(cal, "calibration_path", lambda: tmp_path / "cal.json")
    resolve = FakeResolve()
    cal.store(resolve, 1080, 1920, 4.0)
    project = make_project(width=1920, height=1080)
    calibrate(resolve, project, (1080, 1920))
    assert project.GetSetting("timelineResolutionWidth") == "1920"
    assert project.GetSetting("timelineResolutionHeight") == "1080"
    assert project.GetTimelineCount() == 0


def test_resolve_version_reads_from_resolve():
    resolve = FakeResolve()
    assert cal.resolve_version(resolve) == "21.1.0.14"


def test_resolve_version_unknown_on_failure():
    class Broken(FakeResolve):
        def GetVersionString(self):
            raise RuntimeError("no version")
    assert cal.resolve_version(Broken()) == "unknown"
