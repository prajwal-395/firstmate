"""Cross-format timebase and geometry contracts using pure helpers and media fixtures.

The fixture media lives under ``tests/fixtures`` and is probed read-only.  The
matrix deliberately combines source and timeline rates so conversion errors do
not hide behind a same-rate edit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from library.tools.frame_utils import frame_to_seconds, seconds_to_frame
from library.tools.resolve_transform import (
    fit_base_scale,
    mismatch_base_scales,
    native_conversion_refusal,
)
from library.tools.spine_contract import source_to_timeline, timeline_to_source

REPO = Path(__file__).resolve().parents[3]
MEDIA = REPO / "tests/fixtures/project_model_e2e/media"


@pytest.mark.parametrize(
    "source_rate,timeline_rate,source_frame,timeline_start_frame",
    [
        (Fraction(24, 1), Fraction(25, 1), 240, 125),
        (Fraction(25, 1), Fraction(30, 1), 250, 300),
        (Fraction(24000, 1001), Fraction(30000, 1001), 24000, 900),
        (Fraction(30000, 1001), Fraction(24000, 1001), 30000, 1200),
    ],
)
def test_source_to_timeline_frame_map_is_rate_aware(
    source_rate, timeline_rate, source_frame, timeline_start_frame
):
    """Source frames use the source clock; placement uses the timeline clock."""
    source_start_frame = 48
    source_start = float(Fraction(source_start_frame, 1) / source_rate)
    source_time = float(Fraction(source_frame, 1) / source_rate)
    timeline_start = float(Fraction(timeline_start_frame, 1) / timeline_rate)
    block = {
        "source_start": source_start,
        "timeline_start": timeline_start,
    }

    mapped = source_to_timeline(source_time, block)
    expected_offset = Fraction(source_frame - source_start_frame, 1) / source_rate
    expected_timeline_frame = timeline_start_frame + round(
        float(expected_offset * timeline_rate)
    )

    assert seconds_to_frame(mapped, float(timeline_rate)) == expected_timeline_frame
    assert timeline_to_source(mapped, block) == pytest.approx(source_time)


@pytest.mark.parametrize("rate", [Fraction(24000, 1001), Fraction(30000, 1001)])
def test_fractional_rate_frame_seconds_round_trip_without_drift(rate):
    """One hour of 23.976/29.97 frame addressing stays on its exact frame."""
    fps = float(rate)
    for frame in (0, 1, 29, 1800, 17982, round(fps * 3600)):
        assert seconds_to_frame(frame_to_seconds(frame, fps), fps) == frame


def test_audio_video_fixture_has_aligned_stream_starts():
    """The checked-in A-roll fixture's video and audio start on one clock."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        pytest.skip("ffprobe not on PATH")
    path = MEDIA / "person-ada-aroll.mp4"
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "stream=codec_type,start_time,duration,sample_rate,r_frame_rate,avg_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, encoding="utf-8", check=True, timeout=30,
    )
    streams = json.loads(result.stdout)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")

    assert Fraction(video["r_frame_rate"]) == Fraction(24, 1)
    assert Fraction(video["avg_frame_rate"]) == Fraction(24, 1)
    assert float(video["start_time"]) == pytest.approx(0.0, abs=1e-6)
    assert float(audio["start_time"]) == pytest.approx(0.0, abs=1e-6)
    # AAC packet padding may extend the audio stream; its first sample still
    # aligns with the first video frame.
    assert abs(float(audio["duration"]) - float(video["duration"])) < 0.05


@pytest.mark.parametrize(
    "source_size,timeline_size,fit,scales",
    [
        ((3840, 2160), (1080, 1920), 0.28125, (0.28125, 0.28125)),
        ((1920, 1080), (1920, 1080), 1.0, (1.0, 1.0)),
        ((1080, 1920), (1920, 1080), 0.5625, (0.5625, 0.5625)),
    ],
)
def test_resolution_mismatch_modes_have_explicit_geometry(
    source_size, timeline_size, fit, scales
):
    sw, sh = source_size
    tw, th = timeline_size
    assert fit_base_scale(sw, sh, tw, th) == pytest.approx(fit)
    assert mismatch_base_scales(sw, sh, tw, th, "scaleToFit") == pytest.approx(scales)
    assert mismatch_base_scales(sw, sh, tw, th, "scaleToCrop")[0] >= fit
    assert mismatch_base_scales(sw, sh, tw, th, "centerCrop") == (1.0, 1.0)


@pytest.mark.parametrize(
    "transform,reason",
    [
        ({"CropLeft": 0.1}, "nonzero crop is not qualified"),
        ({"CropBottom": 1}, "nonzero crop is not qualified"),
        ({"ZoomX": 1.2, "ZoomY": 1.0}, "independent ZoomX/ZoomY is not qualified"),
        ({"RotationAngle": 5}, "rotation pivot/order is not qualified"),
    ],
)
def test_unqualified_transform_geometry_refuses_with_a_reason(transform, reason):
    assert native_conversion_refusal(transform) == reason

