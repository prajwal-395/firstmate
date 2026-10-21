"""A-06: the frame rate the engine cuts on is the container's own.

The audit measured three defects, and this file names each of them:

1. the catalog THREW AWAY the container's rational rate - `round(
   24000/1001, 3)` = 23.976 against the container's 23.976023976..., a
   relative error of 1.0e-06, and a rational that cannot be read back
   without a tolerance guess;
2. the migrated clock carried NO rate at all (`frame_rate: None`), so
   the exact frame/sample machinery in `library/tools/source_clocks.py`
   had no producer and refused every clock it was handed;
3. a VARIABLE-rate source was catalogued with a nominal rate, and
   turning its frame index into a time by dividing by that rate was
   wrong by 69 frames (1.15 s) on the fixture below - the audit
   measured 131 frames (2.18 s) on real footage.

The fixtures are built by ffmpeg under ``tmp_path`` - never the
captain's media, and never a real project.
"""
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from library.capabilities.footage import catalog as catalog_module
from library.tools import frame_rate as frame_rate_module
from library.tools import source_clocks

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe is not available here, so the measured-clocks "
        "fixtures cannot be built or measured. Runs anywhere ffmpeg and "
        "ffprobe are on PATH."),
)

# The seven rates the audit names: three whole, three NTSC rationals and
# the 25 fps PAL rate.
RATES = [Fraction(24, 1), Fraction(25, 1), Fraction(30, 1), Fraction(60, 1),
         Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)]


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    *args], check=True)


def _cfr_fixture(directory: Path, rate: Fraction, name: str = "") -> Path:
    path = directory / (name or f"cfr_{rate.numerator}_{rate.denominator}.mp4")
    _ffmpeg("-f", "lavfi", "-i",
            f"testsrc=size=160x90:rate={rate}:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(rate),
            str(path))
    return path


def _vfr_fixture(directory: Path) -> Path:
    """A 60 fps segment concatenated with a 12 fps one."""
    fast = directory / "vfr_fast.mp4"
    slow = directory / "vfr_slow.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x90:rate=60:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fast))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x90:rate=12:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(slow))
    listing = directory / "concat.txt"
    listing.write_text(f"file '{fast}'\nfile '{slow}'\n", encoding="utf-8")
    merged = directory / "vfr.mp4"
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
            str(merged))
    return merged


def _native_pts(path: Path) -> list:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-select_streams", "v:0", "-show_entries",
         "frame=best_effort_timestamp_time", str(path)],
        capture_output=True, encoding="utf-8", check=True)
    return [float(row["best_effort_timestamp_time"])
            for row in json.loads(out.stdout).get("frames", [])]


# ── 1. rate fidelity ─────────────────────────────────────────────────

@pytest.mark.parametrize("rate", RATES, ids=str)
def test_catalog_keeps_the_container_rational_exactly(tmp_path, rate):
    """The catalog carries the container's rational, not a rounded float.

    The defect: `round(num/den, 3)` turned 24000/1001 into 23.976, so
    the float was 2.4e-05 away from the container's own rate and the
    rational could only be recovered by a 0.001 tolerance guess.
    """
    path = _cfr_fixture(tmp_path, rate)
    entry = catalog_module.extract_metadata(str(path))

    rational = entry.get("frame_rate_rational")
    assert rational is not None, (
        "the catalog dropped the container's rational rate")
    assert (rational["numerator"], rational["denominator"]) == (
        rate.numerator, rate.denominator)

    # The float is that rational READ, so it is derivable from it
    # exactly - the audit's 1e-9 bar.
    derived = (rational["numerator"] / rational["denominator"])
    assert abs(entry["frame_rate"] - derived) <= 1e-9
    assert abs(entry["frame_rate"] - float(rate)) <= 1e-9


def test_catalog_project_rate_is_the_rational_not_a_guess(tmp_path):
    """The project's own rate keeps its rational too."""
    paths = [_cfr_fixture(tmp_path, Fraction(24000, 1001), "a.mp4"),
             _cfr_fixture(tmp_path, Fraction(24000, 1001), "b.mp4")]
    result = catalog_module.catalog_footage(
        [{"path": str(path), "filename": path.name,
          "clip_id": f"clip_{index:03d}",
          "size_bytes": path.stat().st_size}
         for index, path in enumerate(paths, start=1)])
    assert result["project_frame_rate_rational"] == {
        "numerator": 24000, "denominator": 1001}
    assert abs(result["project_fps"] - 24000 / 1001) <= 1e-9
    assert result["project_frame_rate_assumption"] is None


# ── 2. VFR honesty ───────────────────────────────────────────────────

def test_variable_rate_source_measures_its_own_frame_times(tmp_path):
    """A variable-rate source carries the container's own timestamps.

    The defect: the source was catalogued with a nominal rate, and
    dividing its frame index by that rate was wrong by 69 frames
    (1.15 s) on this fixture.
    """
    path = _vfr_fixture(tmp_path)
    entry = catalog_module.extract_metadata(str(path))
    assert entry["is_vfr"] is True

    measured = entry.get("measured_frame_timestamps")
    assert measured, (
        "a variable-rate source carries no measured frame times, so a "
        "frame index of it can only be divided by a rate it does not "
        "have")
    stamps = measured["timestamps_seconds"]
    native = _native_pts(path)
    assert len(stamps) == len(native)

    # Every measured frame is within ONE frame of the native timestamp -
    # and, measured this way, exact.
    rate = entry["frame_rate_nominal"]
    for index, stamp in enumerate(stamps):
        assert abs(stamp - native[index]) <= 1.0 / rate

    # And the old way - dividing the frame index by the nominal rate -
    # is off by many frames, which is why the table is measured.
    worst = max(abs(index / rate - stamp) for index, stamp in
                enumerate(native))
    assert worst * rate > 1.0, (
        "this fixture no longer shows the defect it exists to name")


def test_variable_rate_source_refuses_a_frame_the_measurement_missed(
        tmp_path):
    """A frame past the measured table is refused BY NAME."""
    path = _vfr_fixture(tmp_path)
    entry = catalog_module.extract_metadata(str(path))
    stamps = entry["measured_frame_timestamps"]["timestamps_seconds"]
    clock = source_clocks.SourceClock(
        digest="vfr", fps_num=60, fps_den=1, variable_frame_rate=True,
        frame_timestamps=tuple(stamps))
    with pytest.raises(source_clocks.VariableFrameRateError) as refusal:
        source_clocks.frame_to_seconds(clock, len(stamps) + 10)
    assert "measured" in str(refusal.value)


# ── 3. the migrated clock carries the measured rate ───────────────────

def test_migrated_clock_carries_the_containers_rational(tmp_path):
    """The migration writes the measured rational into the clock row.

    The defect: the clock row carried `frame_rate: None`, so
    `source_clocks.from_model_clock` REFUSED every migrated clock and
    the exact frame/sample machinery had no producer.
    """
    from library.tools import project_model_evidence as evidence

    digest = "digest-frames-001"
    memory = tmp_path / "memory"
    project = tmp_path / "proj"
    (memory / digest).mkdir(parents=True)
    (memory / digest / "source.json").write_text(json.dumps({
        "content_digest": digest, "size_bytes": 100,
        "duration_seconds": 10.0, "video_streams": [{"index": 0}],
    }), encoding="utf-8")
    catalog = [{"clip_id": "clip_001", "source_file": "/nonexistent/a.mov",
                "path": "/nonexistent/a.mov",
                "frame_rate": 23.976023976023978,
                "frame_rate_rational": {"numerator": 24000,
                                        "denominator": 1001},
                "avg_frame_rate": 23.976023976023978,
                "avg_frame_rate_rational": {"numerator": 24000,
                                            "denominator": 1001},
                "is_vfr": False}]
    (project / "pipeline_data.json").parent.mkdir(parents=True, exist_ok=True)
    (project / "pipeline_data.json").write_text(json.dumps({
        "capability_outputs": {"footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {"clip_001": {
            "path": "/nonexistent/a.mov", "content_digest": digest}},
    }), encoding="utf-8")

    model, _report = evidence.migrate_project_evidence(
        str(project), memory_root=str(memory), catalog=catalog)

    clock = next(row for row in model["clocks"])
    assert clock["frame_rate"] == {"numerator": 24000, "denominator": 1001}

    # And the exact machinery now has a producer: the clock reads back.
    built = source_clocks.from_model_clock(digest, clock)
    assert (built.fps_num, built.fps_den) == (24000, 1001)


# ── 4. the round trip ────────────────────────────────────────────────

@pytest.mark.parametrize("rate", RATES, ids=str)
def test_frame_span_ticks_round_trips_to_its_own_frame(rate):
    """`tick_to_frame(frame_span_ticks(f)) == f`, at every rate.

    The defect: the span started at the FLOOR of the frame's start
    edge, so its first tick belonged to the PREVIOUS frame - the round
    trip shifted a frame at 24, 30, 60, 24000/1001, 30000/1001 and
    60000/1001.
    """
    clock = source_clocks.SourceClock(
        digest="d", fps_num=rate.numerator, fps_den=rate.denominator)
    for frame in range(300):
        start, end = source_clocks.frame_span_ticks(clock, frame)
        assert source_clocks.tick_to_frame(clock, start) == frame
        assert source_clocks.tick_to_frame(clock, end - 1) == frame
        assert end > start


@pytest.mark.parametrize("rate", RATES, ids=str)
def test_frame_spans_still_tile_the_grid(rate):
    """Consecutive frames tile: no gap, no overlap."""
    clock = source_clocks.SourceClock(
        digest="d", fps_num=rate.numerator, fps_den=rate.denominator)
    spans = [source_clocks.frame_span_ticks(clock, frame)
             for frame in range(50)]
    assert spans[0][0] == 0
    for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
        assert first_end == second_start


# ── 5. assumption honesty ────────────────────────────────────────────

def test_a_missing_rate_is_a_named_assumption_not_a_silent_thirty():
    """No measurable rate is an ASSUMPTION, and it says so."""
    resolved = frame_rate_module.resolve(None, site="unit")
    assert resolved.measured is False
    assert resolved.fps == frame_rate_module.ASSUMED_FPS
    assert resolved.assumption["assumption"] == "frame_rate"
    assert resolved.assumption["site"] == "unit"
    assert resolved.assumption["reason"]


def test_a_measured_rate_carries_no_assumption():
    resolved = frame_rate_module.resolve(
        {"catalog": {"numerator": 24000, "denominator": 1001}},
        site="unit")
    assert resolved.measured is True
    assert resolved.assumption is None
    assert resolved.fps == 24000 / 1001


def test_the_strip_sampling_names_the_rate_it_assumed():
    """`window_frames` used to reach for 30 fps in silence.

    A strip's LAST sample is one frame before the window ends, so the
    rate decides which frame that is; with no measured rate the engine's
    standing assumption stands, but it is NAMED.
    """
    from library.tools import window_frames

    assumed = window_frames.sampling_rate(0)
    assert assumed.measured is False
    assert assumed.assumption["site"] == "window_frames.sample_times"
    assert assumed.assumption["assumed_frame_rate"] == 30.0

    measured = window_frames.sampling_rate(24000 / 1001)
    assert measured.measured is True
    assert measured.assumption is None
    assert abs(measured.fps - 24000 / 1001) <= 1e-12


def test_the_vfx_step_records_the_rate_it_assumed():
    """Step 4.03 used to time its effects against a silent 30 fps.

    The step now records the assumption in its own output, so the run
    record can name the grid the effects were timed on.
    """
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[3]
    payload = {
        "a_roll_assignments": [{
            "spine_block_position": 7,
            "video_segments": [{"clip_id": "clip_2",
                                "source_file": "/tmp/IMG_1.mov"}]}],
        "timed_spine": {"structure": [
            {"position": 7, "block_type": "speech", "clip_id": "clip_2",
             "timeline_start": 2.4, "timeline_end": 18.4}]},
        "vfx_creative": [],
    }
    proc = subprocess.run(
        [sys.executable, "-m", "library.steps.step_4_03_plan_vfx.post_bridge"],
        input=json.dumps(payload), capture_output=True, encoding="utf-8",
        cwd=str(repo), check=False)
    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)
    assumption = (output.get("enhancement_spec") or {}).get(
        "frame_rate_assumption")
    assert assumption, (
        "step 4.03 timed its effects against an unnamed 30 fps")
    assert assumption["assumed_frame_rate"] == 30.0
    assert assumption["site"] == "step_4_03_plan_vfx"

    # With a measured rate, there is nothing to assume and nothing
    # recorded.
    payload["frame_rate"] = 25.0
    proc = subprocess.run(
        [sys.executable, "-m", "library.steps.step_4_03_plan_vfx.post_bridge"],
        input=json.dumps(payload), capture_output=True, encoding="utf-8",
        cwd=str(repo), check=False)
    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)
    assert "frame_rate_assumption" not in output["enhancement_spec"]


def test_a_trim_stated_in_frames_still_refuses_without_a_timebase():
    """Step 2.02 used to reach for 30 fps where a grid was needed.

    A trim stated in FRAMES has no meaning without the timebase it was
    counted on, so the passage refuses - which it already did, but it
    also carried a silent 30 fps beside the refusal.
    """
    import importlib

    module = importlib.import_module(
        "library.steps.step_2_02_speech_sequence.post_bridge")
    with pytest.raises(module.PassageAlignmentError):
        module._resolve_stated_trim(
            {"trim_head_frames": 12}, "passage 1", None)
