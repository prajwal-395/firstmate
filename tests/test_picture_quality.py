"""A usable range is a measurement, and an absent one reads as absent.

Two things are under test here.

The NON-NEGOTIABLE, and the reason the issue was raised: when nothing
measured a clip, `usable_ranges` must not claim the whole clip.  It used
to read `[[0, full_duration]]` beside `usable_ranges_method: "unmeasured"`
- one field contradicting the three next to it - and the B-roll selector
reads that field to decide which 2.5 seconds of a clip to cut.  On the
2026-08-26 run of project 001 it cut `clip_006` 1.65-4.15s, a whip pan out
of a moving car window, and the master at 25.0s is motion-blurred asphalt.

The MEASUREMENT that replaces the assertion: a sharpness estimate sampled
straight off the video file, which needs no temporal index and therefore
works on a first run - see `library/tools/analysis/picture_quality.py` for
what its sampling rate can and cannot resolve.

Calibration on project 001's own footage
────────────────────────────────────────
Sampled at 5 Hz with the short side bounded to 180px, 17 clips / 807s:

    clip      file           dur     thr   soft   soft ranges (s)
    clip_006  IMG_1811.MOV  22.87   166.3  16.6%  0.0-2.0, 2.8-3.6, 7.2-8.2
    clip_008  IMG_1813.MOV   9.07   512.9  48.5%  3.8-6.2, 6.8-7.8, 8.0-9.0
    clip_007  IMG_1812.MOV 139.13   352.6  15.5%  16 ranges
    clip_011  IMG_1816.MOV 188.58   206.6   2.8%  5 ranges
    ...8 of the 17 come back with no soft range at all.

Verified by eye against extracted frames: clip_006 @3.15s (the source
frame behind the master's 25.0s) and clip_008 @5.0s are both unreadable
motion blur and both fall inside a reported range; clip_006 @5.0s and
clip_008 @2.0s are sharp and legible and neither does.  Cost: 2.79s per
clip, 0.059x realtime.
"""

import json
import os
import shutil
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

# vision_pipeline_v3 imports mlx_vlm at module scope; the measurement
# under test does not, but the integration assertions below reach it.
mlx_mock = MagicMock()
mlx_mock.load.return_value = (MagicMock(), MagicMock())
mlx_mock.generate.return_value = MagicMock(text="[]")
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils
sys.modules.setdefault("mlx_vlm", mlx_mock)
sys.modules.setdefault("mlx_vlm.prompt_utils", mlx_prompt_utils)

from library.tools.analysis import picture_quality
from library.tools.analysis.picture_quality import (
    MIN_SHARPNESS_SAMPLES,
    SIGNAL_NAME,
    SOFT_PICTURE_REASON,
    PictureQualityUnavailable,
    _sample_dimensions,
    measure_soft_picture,
    sample_sharpness,
    sharpness_threshold,
    soft_picture_ranges,
)
from library.tools.analysis.vision_pipeline_v3 import (
    _compute_usable_ranges,
    compute_deterministic_assessment,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

RATE = picture_quality.SAMPLE_RATE_HZ


def _track(values, rate=RATE):
    return {"sample_rate_hz": rate, "sample_short_side_px": 180,
            "values": list(values)}


# ═══════════════════════════════════════════════════════════════════════
#  The non-negotiable: an unmeasured clip claims nothing
# ═══════════════════════════════════════════════════════════════════════

class TestUnmeasuredClaimsNothing:
    def test_no_signal_at_all_yields_no_usable_range(self):
        """No temporal index and no picture sample -> no range asserted."""
        usable, _unusable, method, signals = _compute_usable_ranges(
            None, 188.578, "unknown", soft_picture_ranges=None)

        assert method == "unmeasured"
        assert signals == []
        assert usable == []
        assert usable != [[0, 188.578]]

    def test_the_whole_clip_is_never_the_unmeasured_answer(self):
        """The exact shape the 2026-08-26 run wrote, for every clip."""
        for duration in (3.567, 22.87, 139.13, 188.578):
            usable, _, method, _ = _compute_usable_ranges(
                None, duration, "unknown", soft_picture_ranges=None)
            assert method == "unmeasured"
            assert [0, round(duration, 3)] not in usable
            assert usable == []

    def test_deterministic_assessment_reports_the_absence(self):
        result = compute_deterministic_assessment(None, "", duration=188.578)

        assert result["usable_ranges"] == []
        assert result["unusable_ranges"] == []
        assert result["usable_ranges_method"] == "unmeasured"
        assert result["usable_ranges_signals"] == []
        # ...and the field beside it stays honestly unknown rather than
        # being filled in to match.
        assert result["camera_stability"] == "unknown"

    def test_an_unsampleable_clip_is_unmeasured_not_fine(self, tmp_path):
        """A file ffmpeg cannot read measures nothing, and says so."""
        broken = tmp_path / "not-a-video.mov"
        broken.write_bytes(b"not a video")

        assert measure_soft_picture(str(broken), 10.0) is None

        usable, _, method, signals = _compute_usable_ranges(
            None, 10.0, "unknown",
            soft_picture_ranges=measure_soft_picture(str(broken), 10.0))
        assert method == "unmeasured"
        assert signals == []
        assert usable == []


# ═══════════════════════════════════════════════════════════════════════
#  A measurement that found nothing soft is NOT an absent measurement
# ═══════════════════════════════════════════════════════════════════════

class TestMeasuredAndClean:
    def test_empty_soft_ranges_are_a_measurement(self):
        usable, unusable, method, signals = _compute_usable_ranges(
            None, 10.0, "unknown", soft_picture_ranges=[])

        assert method == "deterministic_v1"
        assert signals == [SIGNAL_NAME]
        assert unusable == []
        assert usable == [[0, 10.0]]

    def test_soft_ranges_narrow_the_usable_ranges(self):
        soft = [{"start": 0.0, "end": 2.0, "reason": SOFT_PICTURE_REASON}]
        usable, unusable, method, signals = _compute_usable_ranges(
            None, 10.0, "unknown", soft_picture_ranges=soft)

        assert method == "deterministic_v1"
        assert signals == [SIGNAL_NAME]
        assert unusable == soft
        assert usable == [[2.0, 10.0]]

    def test_the_picture_signal_joins_the_temporal_index_signals(self):
        """Both sources measured -> both named, ranges merged."""
        index = {
            "duration": 10.0,
            "motion_energy": {
                "sample_rate_hz": 30,
                "values": [0.0] * 300,
                "peak_mean_abs_diff": 0.01,
            },
        }
        soft = [{"start": 4.0, "end": 5.0, "reason": SOFT_PICTURE_REASON}]
        usable, unusable, method, signals = _compute_usable_ranges(
            index, 10.0, "unknown", soft_picture_ranges=soft)

        assert method == "deterministic_v1"
        assert set(signals) == {SIGNAL_NAME, "motion_energy"}
        assert unusable == soft
        assert usable == [[0.0, 4.0], [5.0, 10.0]]


# ═══════════════════════════════════════════════════════════════════════
#  soft_picture_ranges - the verdict, on synthetic tracks
# ═══════════════════════════════════════════════════════════════════════

class TestSoftPictureRanges:
    def test_a_run_below_the_threshold_is_reported(self):
        values = [1000.0] * 20 + [5.0] * 5 + [1000.0] * 20
        ranges = soft_picture_ranges(_track(values), 9.0)

        assert ranges == [{"start": 4.0, "end": 5.0,
                           "reason": SOFT_PICTURE_REASON}]

    def test_a_sharp_clip_reports_nothing_soft(self):
        ranges = soft_picture_ranges(_track([1000.0] * 40), 8.0)
        assert ranges == []

    def test_a_run_shorter_than_the_floor_is_not_reported(self):
        """Two soft samples is 0.4s - under MIN_SOFT_RUN_S, so dropped.

        This is the shortest window the method deliberately cannot see,
        and the module docstring states it.
        """
        values = [1000.0] * 20 + [5.0] * 2 + [1000.0] * 20
        assert soft_picture_ranges(_track(values), 8.4) == []

    def test_the_shortest_reported_window_is_the_floor(self):
        values = [1000.0] * 20 + [5.0] * 3 + [1000.0] * 20
        ranges = soft_picture_ranges(_track(values), 8.6)
        assert len(ranges) == 1
        assert ranges[0]["end"] - ranges[0]["start"] == pytest.approx(0.6)

    def test_too_few_samples_is_None_not_an_empty_list(self):
        """A reference percentile off three frames is noise, not a verdict."""
        short = [1000.0] * (MIN_SHARPNESS_SAMPLES - 1)
        assert soft_picture_ranges(_track(short), 2.0) is None

    def test_no_duration_measures_nothing(self):
        assert soft_picture_ranges(_track([1000.0] * 40), 0) is None

    def test_a_clip_blurred_end_to_end_is_caught_by_the_absolute_floor(self):
        """A relative threshold alone would rate its own mush as normal."""
        values = [12.0] * 40
        ranges = soft_picture_ranges(_track(values), 8.0)

        assert len(ranges) == 1
        assert ranges[0]["start"] == 0.0
        assert ranges[0]["end"] == pytest.approx(8.0)

    def test_a_low_texture_clip_is_not_condemned_by_the_absolute_floor(self):
        """A flat but sharp subject sits under no clip's own reference."""
        values = [120.0] * 40
        assert soft_picture_ranges(_track(values), 8.0) == []

    def test_ranges_are_clamped_to_the_clip_duration(self):
        values = [1000.0] * 5 + [5.0] * 20
        ranges = soft_picture_ranges(_track(values), 3.0)
        assert ranges[-1]["end"] == 3.0

    def test_the_threshold_is_the_greater_of_relative_and_absolute(self):
        assert sharpness_threshold([1000.0] * 40) == pytest.approx(200.0)
        assert sharpness_threshold([100.0] * 40) == pytest.approx(
            picture_quality.SHARPNESS_ABSOLUTE_FLOOR)
        assert sharpness_threshold([1.0] * (MIN_SHARPNESS_SAMPLES - 1)) is None


# ═══════════════════════════════════════════════════════════════════════
#  Sample geometry
# ═══════════════════════════════════════════════════════════════════════

def test_the_vision_pipeline_can_import_this_module_as_a_script():
    """Step 1.03 runs `vision_pipeline_v3.py` as a script, not as a module.

    `sys.path[0]` is then the analysis directory, so the file adds the
    repo root itself.  Under pytest conftest has already put the root on
    the path, so a wrong index still imports and the failure only shows
    up in a real run - which is why the index is asserted here.
    """
    from library.tools.analysis import vision_pipeline_v3 as vp

    assert vp.REPO_ROOT.name != "library"
    assert (vp.REPO_ROOT / "library" / "tools" / "analysis"
            / "picture_quality.py").is_file()
    assert vp.picture_quality is picture_quality


class TestSampleGeometry:
    def test_the_short_side_is_bounded_whichever_side_it_is(self):
        """Portrait and landscape are measured at the same detail scale.

        The absolute floor is tied to this geometry: bound the HEIGHT
        instead and a portrait clip is sampled at a third of the pixels
        of a landscape one, at a different spatial frequency, against the
        same number.
        """
        assert _sample_dimensions(1080, 1920, 180) == (180, 320)
        assert _sample_dimensions(1920, 1080, 180) == (320, 180)

    def test_a_small_source_is_never_upscaled(self):
        assert _sample_dimensions(320, 240, 180) == (240, 180)
        assert _sample_dimensions(160, 120, 180) == (160, 120)

    def test_dimensions_are_even(self):
        for w, h in (_sample_dimensions(1001, 1997, 180),
                     _sample_dimensions(333, 777, 180)):
            assert w % 2 == 0 and h % 2 == 0

    @needs_ffmpeg
    def test_an_unreadable_file_raises_rather_than_guessing(self, tmp_path):
        broken = tmp_path / "broken.mov"
        broken.write_bytes(b"\x00" * 64)
        with pytest.raises(PictureQualityUnavailable):
            sample_sharpness(str(broken))


# ═══════════════════════════════════════════════════════════════════════
#  End to end, against a real decode
# ═══════════════════════════════════════════════════════════════════════

def _synthesise(path, duration=5.0, blur_from=2.0, blur_to=3.2):
    """A clip that is sharp, then blurred for a known window, then sharp."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={duration}",
         "-vf", f"gblur=sigma=12:enable='between(t,{blur_from},{blur_to})'",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path), "-y"],
        check=True, capture_output=True, timeout=120)


@needs_ffmpeg
def test_a_real_decode_finds_the_window_that_is_actually_blurred(tmp_path):
    clip = tmp_path / "synthetic.mp4"
    _synthesise(clip)

    track = sample_sharpness(str(clip))
    assert track["sample_rate_hz"] == RATE
    assert len(track["values"]) > MIN_SHARPNESS_SAMPLES

    ranges = soft_picture_ranges(track, 5.0)
    assert len(ranges) == 1
    found = ranges[0]
    assert found["reason"] == SOFT_PICTURE_REASON
    # The blur runs 2.0-3.2s; sampling is 5 Hz, so the bounds land within
    # one sample interval of the truth and never outside the window.
    assert found["start"] == pytest.approx(2.0, abs=1.0 / RATE)
    assert found["end"] == pytest.approx(3.2, abs=1.0 / RATE)


@needs_ffmpeg
def test_the_blurred_window_is_excluded_from_the_usable_ranges(tmp_path):
    """The whole route: file -> measurement -> usable_ranges."""
    clip = tmp_path / "synthetic.mp4"
    _synthesise(clip)

    soft = measure_soft_picture(str(clip), 5.0)
    assert soft, "the measurement found nothing to exclude"

    result = compute_deterministic_assessment(
        None, "", duration=5.0, soft_picture_ranges=soft)

    assert result["usable_ranges_method"] == "deterministic_v1"
    assert result["usable_ranges_signals"] == [SIGNAL_NAME]
    assert result["usable_ranges"] != [[0, 5.0]]
    # Nothing in the blurred middle survives as usable.
    for start, end in result["usable_ranges"]:
        assert not (start < 3.0 < end), (
            f"[{start}, {end}] still offers the blurred window")


@needs_ffmpeg
def test_the_cli_reports_the_measurement_as_json(tmp_path):
    clip = tmp_path / "synthetic.mp4"
    _synthesise(clip)

    proc = subprocess.run(
        [sys.executable, "-m", "library.tools.analysis.picture_quality",
         str(clip)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        cwd=REPO_ROOT, check=False)

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["sample_rate_hz"] == RATE
    assert payload["soft_picture_ranges"]
