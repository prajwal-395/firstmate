"""Frame-exact addressing: the frame grid, the dense sample, the
refinement.

The defects these name:

* a container's rate read through a float loses the fraction that
  makes it exact (``30000/1001`` is not ``29.97``);
* a source with no measurable rate gets a SILENT 30 fps instead of a
  refusal;
* a frame index computed through a float second jumps a frame at the
  boundary, which is the whole error the lane exists to remove;
* a time that does not sit on the source's grid is reported as an
  exact frame index when it is an approximation;
* a refinement that cannot measure anything reports a boundary;
* a refinement whose interval cut the answer off reports it as final.
"""
import shutil
import subprocess

import pytest

from library.tools import frame_exact
from library.tools.frame_exact import FrameExactError, SourceTimebase

HAS_MEDIA = bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))
MEDIA_REASON = "needs ffmpeg and ffprobe (AGENTS.md 9)"

NTSC = SourceTimebase(fps_num=30000, fps_den=1001, time_base_num=1,
                       time_base_den=90000)
PAL = SourceTimebase(fps_num=25, fps_den=1, time_base_num=1,
                     time_base_den=1000)


def _clip(tmp_path, seconds=2.0, fps=30):
    """A short CFR clip: `fps` frames a second, from frame 0."""
    path = tmp_path / "clip.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size=320x180:rate={fps}:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
         str(path)],
        check=True, capture_output=True)
    return path


def test_the_container_rate_is_read_as_the_exact_rational():
    # "30000/1001" is a rational, not a float: 29.97002997... is the
    # nearest double to it, and the difference is a frame every 33
    # seconds of footage.
    timebase = frame_exact.timebase_from_stream(
        {"avg_frame_rate": "30000/1001", "time_base": "1/90000"})
    assert (timebase.fps_num, timebase.fps_den) == (30000, 1001)
    assert timebase.fps == frame_exact.source_clocks.Fraction(30000, 1001)


def test_a_measured_float_rate_keeps_the_one_reading():
    # 23.976 is the NTSC rate, and source_clocks is what names it.
    timebase = frame_exact.timebase_from_stream(
        {"avg_frame_rate": "23.976", "time_base": "1/1000"})
    assert (timebase.fps_num, timebase.fps_den) == (24000, 1001)


def test_a_source_with_no_measurable_rate_is_refused():
    with pytest.raises(FrameExactError) as excinfo:
        frame_exact.timebase_from_stream({"time_base": "1/1000"})
    assert "no measured frame rate" in str(excinfo.value)


def test_a_frame_index_never_jumps_to_the_next_frame():
    # The hair either side of a frame boundary is the difference
    # between a frame and the frame after it.
    for timebase in (NTSC, PAL):
        for frame in range(400):
            start = timebase.seconds_at(frame)
            assert timebase.frame_at(start) == frame
            assert timebase.frame_at(start + 1e-9) == frame
            assert timebase.frame_at(timebase.seconds_at(frame + 1)) == frame + 1


def test_a_time_off_the_grid_is_not_called_exact():
    # A source whose frames are not evenly spaced: the index computed
    # from the average rate is an approximation, and the sample says so.
    assert NTSC.on_grid(NTSC.seconds_at(10))
    assert not NTSC.on_grid(NTSC.seconds_at(10) + 0.0004)


def test_the_first_frame_at_or_after_a_time_is_not_the_one_containing_it():
    timebase = SourceTimebase(fps_num=30, fps_den=1, time_base_num=1,
                               time_base_den=15360)
    assert timebase.frame_at(0.02) == 0
    assert timebase.frame_at_or_after(0.02) == 1
    assert timebase.frame_at_or_after(0.0) == 0


def _fake_sample(frame_indices, start=0.0):
    frames = [frame_exact.SampledFrame(
        frame_index=index, pts_seconds=index / 30.0, file=f"/tmp/f{index}.jpg",
        on_grid=True) for index in frame_indices]
    return {"source_file": "/tmp/clip.mov",
            "timebase": SourceTimebase(30, 1, 1, 15360).to_dict(),
            "frames": [f.to_dict() for f in frames],
            "coverage": {"start_seconds": start, "end_seconds": start + 1,
                         "frames_decoded": len(frames),
                         "frames_expected": len(frames),
                         "first_frame_index": frames[0].frame_index,
                         "last_frame_index": frames[-1].frame_index,
                         "all_on_grid": True},
            "cost": {"decode_seconds": 1.0, "decode_cpu_seconds": 0.5,
                     "frames_decoded": len(frames)}}


def test_a_coarse_time_maps_to_the_coarse_sample_s_own_frame():
    m2 = {"frames": [{"t": 0.0, "frame_index": 0},
                     {"t": 33.233, "frame_index": 997},
                     {"t": 47.0, "frame_index": 1410}]}
    assert frame_exact.coarse_frame_index(m2, 33.233) == 997
    assert frame_exact.coarse_frame_index(m2, 33.4) == 997
    assert frame_exact.coarse_frame_index(m2, 47.0) == 1410


def test_a_coarse_sample_with_no_index_maps_to_nothing():
    m2 = {"frames": [{"t": 33.233}]}
    assert frame_exact.coarse_frame_index(m2, 33.233) is None


def test_the_refinement_names_one_frame_and_the_cost_it_paid(monkeypatch):
    monkeypatch.setattr(frame_exact, "sample_interval",
                        lambda *a, **k: _fake_sample(range(100, 130)))
    monkeypatch.setattr(frame_exact, "measure_frames",
                        lambda paths: [{} for _ in paths])
    series = [1.0] * 10 + [0.2] + [1.0] * 19
    report = frame_exact.refine_boundary(
        "/tmp/clip.mov", SourceTimebase(30, 1, 1, 15360), 3.0, 4.0,
        "/tmp/out", series_fn=lambda measured: series)
    assert report["status"] == "boundary_measured"
    assert report["boundary"]["frame_index"] == 110
    assert report["boundary"]["uncertainty_seconds"] == pytest.approx(
        1 / 30, abs=1e-6)
    assert report["cost"]["decode_cpu_seconds"] == 0.5
    assert report["cost"]["frames_decoded"] == 30


def test_the_refinement_reports_an_interval_that_cut_the_answer_off(
        monkeypatch):
    monkeypatch.setattr(frame_exact, "sample_interval",
                        lambda *a, **k: _fake_sample(range(100, 130)))
    monkeypatch.setattr(frame_exact, "measure_frames",
                        lambda paths: [{} for _ in paths])
    series = [0.1] + [1.0] * 29
    report = frame_exact.refine_boundary(
        "/tmp/clip.mov", SourceTimebase(30, 1, 1, 15360), 3.0, 4.0,
        "/tmp/out", series_fn=lambda measured: series)
    assert report["status"] == "boundary_truncated"
    assert "widen the interval" in report["reason"]


def test_the_refinement_without_a_measurement_reports_no_boundary(
        monkeypatch):
    monkeypatch.setattr(frame_exact, "sample_interval",
                        lambda *a, **k: _fake_sample(range(100, 130)))
    monkeypatch.setattr(frame_exact, "measure_frames",
                        lambda paths: [{} for _ in paths])
    report = frame_exact.refine_boundary(
        "/tmp/clip.mov", SourceTimebase(30, 1, 1, 15360), 3.0, 4.0,
        "/tmp/out", series_fn=lambda measured: [None] * 30)
    assert report["status"] == "no_boundary_measured"
    assert report["boundary"] is None
    assert "no frame in the interval" in report["reason"]


def test_the_refinement_needs_a_predicate_for_the_first_hit_rule(monkeypatch):
    monkeypatch.setattr(frame_exact, "sample_interval",
                        lambda *a, **k: _fake_sample(range(100, 130)))
    monkeypatch.setattr(frame_exact, "measure_frames",
                        lambda paths: [{} for _ in paths])
    with pytest.raises(FrameExactError):
        frame_exact.refine_boundary(
            "/tmp/clip.mov", SourceTimebase(30, 1, 1, 15360), 3.0, 4.0,
            "/tmp/out", rule="first_hit")


def test_the_refinement_refuses_an_empty_interval():
    with pytest.raises(FrameExactError):
        frame_exact.refine_boundary(
            "/tmp/clip.mov", SourceTimebase(30, 1, 1, 15360), 4.0, 3.0,
            "/tmp/out")


@pytest.mark.skipif(not HAS_MEDIA, reason=MEDIA_REASON)
def test_a_dense_sample_holds_every_frame_of_the_interval(tmp_path):
    path = _clip(tmp_path)
    timebase = frame_exact.probe_timebase(str(path))
    report = frame_exact.sample_interval(str(path), timebase, 0.5, 1.0,
                                          tmp_path / "dense")
    indices = [f["frame_index"] for f in report["frames"]]
    assert indices == list(range(indices[0], indices[0] + len(indices)))
    assert indices[0] == 15
    assert report["coverage"]["frames_decoded"] == \
        report["coverage"]["frames_expected"]


@pytest.mark.skipif(not HAS_MEDIA, reason=MEDIA_REASON)
def test_the_refinement_names_the_frame_the_measurement_names(tmp_path):
    path = _clip(tmp_path)
    timebase = frame_exact.probe_timebase(str(path))
    # A measurement that peaks at the ninth frame of the interval.
    def series(measured):
        return [1.0] * 8 + [0.01] + [1.0] * 7
    report = frame_exact.refine_boundary(
        str(path), timebase, 0.5, 1.0, tmp_path / "refine",
        series_fn=series)
    assert report["status"] == "boundary_measured"
    assert report["boundary"]["frame_index"] == 23
    assert report["boundary"]["on_grid"] is True
