"""A usable range is a measurement, and an absent one reads as absent.

An unmeasured clip claims NO usable range (it used to claim the whole
clip, and the B-roll selector cut a whip pan from it); the replacement
is a sharpness measurement sampled off the video file. Incident and
calibration on project 001: `docs/evidence/picture_quality.md`.
"""

import os
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

# under test does not, but the integration assertions below reach it.
mlx_mock = MagicMock()
mlx_mock.load.return_value = (MagicMock(), MagicMock())
mlx_mock.generate.return_value = MagicMock(text="[]")
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils


from unittest.mock import patch
@pytest.fixture(autouse=True)
def mock_mlx_functions():
    with patch("library.tools.analysis.vision_pipeline_v3.load", mlx_mock.load), \
         patch("library.tools.analysis.vision_pipeline_v3.generate", mlx_mock.generate), \
         patch("library.tools.analysis.vision_pipeline_v3.apply_chat_template", mlx_prompt_utils.apply_chat_template):
        yield

from library.tools.analysis import picture_quality
from library.tools.analysis.picture_quality import (
    MIN_SHARPNESS_SAMPLES,
    SIGNAL_NAME,
    SOFT_PICTURE_REASON,
    PictureQualityUnavailable,
    _sample_dimensions,
    measure_soft_picture,
    sample_sharpness,
    soft_picture_ranges,
)
try:
    from library.tools.analysis.vision_pipeline_v3 import (
        _compute_usable_ranges,
        compute_deterministic_assessment,
    )
except ImportError:
    _compute_usable_ranges = None
    compute_deterministic_assessment = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

needs_mlx = pytest.mark.skipif(
    compute_deterministic_assessment is None,
    reason='could not import "mlx_vlm" - mlx is a macOS-only dependency',
)

RATE = picture_quality.SAMPLE_RATE_HZ


def _track(values, rate=RATE):
    return {"sample_rate_hz": rate, "sample_short_side_px": 180,
            "values": list(values)}


# ═══════════════════════════════════════════════════════════════════════
#  The non-negotiable: an unmeasured clip claims nothing
# ═══════════════════════════════════════════════════════════════════════

@needs_mlx
class TestUnmeasuredClaimsNothing:
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

@needs_mlx
class TestMeasuredAndClean:
    def test_empty_soft_ranges_are_a_measurement(self):
        usable, unusable, method, signals = _compute_usable_ranges(
            None, 10.0, "unknown", soft_picture_ranges=[])

        assert method == "deterministic_v1"
        assert signals == [SIGNAL_NAME]
        assert unusable == []
        assert usable == [[0, 10.0]]

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
        # Two soft samples is 0.4s - under MIN_SOFT_RUN_S, the shortest
        # window the method deliberately cannot see - so dropped.
        values = [1000.0] * 20 + [5.0] * 2 + [1000.0] * 20
        assert soft_picture_ranges(_track(values), 8.4) == []

    def test_too_few_samples_is_None_not_an_empty_list(self):
        """A reference percentile off three frames is noise, not a verdict."""
        short = [1000.0] * (MIN_SHARPNESS_SAMPLES - 1)
        assert soft_picture_ranges(_track(short), 2.0) is None

    def test_a_clip_blurred_end_to_end_is_caught_by_the_absolute_floor(self):
        """A relative threshold alone would rate its own mush as normal."""
        values = [12.0] * 40
        ranges = soft_picture_ranges(_track(values), 8.0)

        assert len(ranges) == 1
        assert ranges[0]["start"] == 0.0
        assert ranges[0]["end"] == pytest.approx(8.0)
        # ...while a flat but sharp subject sits under no clip's own
        # reference and is not condemned.
        assert soft_picture_ranges(_track([120.0] * 40), 8.0) == []

# ═══════════════════════════════════════════════════════════════════════
#  Sample geometry
# ═══════════════════════════════════════════════════════════════════════

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


@needs_mlx
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
