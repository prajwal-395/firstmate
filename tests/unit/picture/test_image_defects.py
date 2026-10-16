"""Known-signal fixtures for VI-03's sampled image defect measurements."""

import shutil
import subprocess

import numpy as np
import pytest

from library.tools import source_memory
from library.tools.analysis.image_defects import measure_source, measure_track
from library.tools.resource_scheduler import profiles


def _track(frames, rate=5):
    return {"frames": frames, "sample_rate_hz": rate,
            "sample_size": [frames[0].shape[1], frames[0].shape[0]]}


def test_known_black_frozen_and_alternating_flicker_signals():
    black = np.zeros((32, 32), dtype=np.uint8)
    bright = np.full((32, 32), 180, dtype=np.uint8)
    result = measure_track(_track([black, black, bright, black, bright,
                                   black, bright, black]))
    signals = result["signals"]
    assert signals["black_frame_sample_indices"] == [0, 1, 3, 5, 7]
    assert signals["frozen_frame_sample_indices"][0] == 1
    assert signals["flicker_sample_indices"]


def test_temporal_noise_is_measured_and_does_not_claim_a_cause():
    rng = np.random.default_rng(7)
    base = np.tile(np.arange(64, dtype=np.uint8), (64, 1))
    frames = [np.clip(base.astype(np.int16) + rng.integers(-8, 9, base.shape),
                      0, 255).astype(np.uint8) for _ in range(6)]
    result = measure_track(_track(frames))
    noise = result["signals"]["temporal_noise"]["values"]
    assert len(noise) == len(frames) - 1
    assert min(noise) > 0
    assert "global-luma-compensated" in result["signals"]["temporal_noise"]["meaning"]


def test_compression_proxy_and_sharpness_proxy_are_separate_signals():
    # A staircase aligned to codec block boundaries has excess boundary energy.
    blocks = np.repeat(np.arange(8, dtype=np.uint8) * 32, 8)
    blocky = np.tile(blocks, (64, 1))
    smooth = np.tile(np.arange(64, dtype=np.uint8) * 4, (64, 1))
    result = measure_track(_track([blocky, smooth]))
    signals = result["signals"]
    assert len(signals["compression_blockiness"]["values"]) == 2
    assert signals["compression_blockiness"]["values"][0] > \
        signals["compression_blockiness"]["values"][1]
    assert "sharpness_proxy_laplacian_variance" in signals
    assert result["limits"]["sharpness_is_focus_or_blur_cause"] is False
    assert result["sampling"]["coverage"] == "sampled"


def test_clipping_fractions_record_highlight_and_shadow_loss():
    black = np.zeros((32, 32), dtype=np.uint8)
    white = np.full((32, 32), 255, dtype=np.uint8)
    mid = np.full((32, 32), 128, dtype=np.uint8)
    result = measure_track(_track([black, mid, white]))
    signals = result["signals"]
    assert signals["clipping_underexposed_fraction"]["values"] == [1.0, 0.0, 0.0]
    assert signals["clipping_overexposed_fraction"]["values"] == [0.0, 0.0, 1.0]
    assert "not an exposure recommendation" in signals[
        "clipping_overexposed_fraction"]["meaning"]
    assert "not an exposure recommendation" in signals[
        "clipping_underexposed_fraction"]["meaning"]


def test_sampler_profile_has_no_declared_memory_ceiling():
    assert profiles()["temporal.index:image_defects"] == {}


def test_source_memory_slot_round_trips_under_the_content_digest(tmp_path):
    record = {"content_digest": "fixture-digest", "method": "fixture"}
    source_memory.write_image_defects("fixture-digest", record, tmp_path)
    stored = source_memory.read_image_defects("fixture-digest", tmp_path)
    assert {key: value for key, value in stored.items()
            if key != "evidence_envelope"} == record
    source_memory.validate_evidence_envelope(stored["evidence_envelope"])


def test_source_memory_refuses_a_mismatched_digest(tmp_path):
    with pytest.raises(ValueError, match="digest"):
        source_memory.write_image_defects("expected", {
            "content_digest": "other"}, tmp_path)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or
                    shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not installed")
def test_streaming_sampler_reports_black_fixture_and_sampled_coverage(tmp_path):
    source = tmp_path / "black.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=c=black:s=320x180:r=10:d=1", "-c:v", "ffv1", "-y",
        str(source)], capture_output=True, encoding="utf-8", check=True)
    record = measure_source(str(source), duration_s=1.0)
    assert record["sampling"]["sample_count"] == 5
    assert record["sampling"]["coverage"] == "sampled"
    assert record["signals"]["black_frame_sample_indices"] == [0, 1, 2, 3, 4]
