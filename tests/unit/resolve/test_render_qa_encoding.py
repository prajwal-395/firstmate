"""The delivered file's ENCODING, its CHANNELS and its CLIPPING.

Three technical facts about a rendered file that had no check, and that
the captain's final-output acceptance criteria name outright:

- QA-004 "output format, frame rate, dimensions, duration, and
  encoding" - the encoding half. A file can carry the right pixels at
  the right rate for the right length and still be the wrong codec, the
  wrong pixel format or the wrong container, and a platform that refuses
  it does not read the picture first.
- QA-007 "loudness, peaks, clipping, and channel configuration" - the
  two halves that were missing. Loudness and the true peak were already
  measured (`measure_lufs`); clipping and the channel configuration were
  not.

Every test here names a defect. No ffmpeg, no Resolve: the probes are
stubbed, and the two measurements that read a file's own bytes (the
container, the decoded PCM) read real bytes written to `tmp_path`.
"""

from __future__ import annotations

import numpy as np
import pytest

from library.tools import pool_stream_meta, render_qa
from library.tools.render_qa import (
    CLIP_RUN_SAMPLES,
    detect_clipping,
    measure_container,
    verify_channel_configuration,
    verify_encoding,
)


def _mp4_bytes(brand=b"isom") -> bytes:
    """An ISO base media header with the given major brand."""
    return b"\x00\x00\x00\x18" + b"ftyp" + brand + b"\x00\x00\x02\x00"


def _quicktime_bytes() -> bytes:
    return _mp4_bytes(brand=b"qt  ")


def _stub_stream(monkeypatch, codec="h264", pixel_format="yuv420p",
                 channels=None):
    """The two probes the encoding check makes, stubbed."""
    monkeypatch.setattr(
        pool_stream_meta, "disk_stream",
        lambda path: {"width": 1080, "height": 1920, "codec": codec})
    monkeypatch.setattr(
        render_qa, "_probe_pixel_format", lambda path: pixel_format)
    if channels is not None:
        monkeypatch.setattr(
            render_qa, "_probe_audio_streams",
            lambda path: [(index, count) for index, count in enumerate(
                channels)])


# ── QA-004: the encoding ──


def test_the_encoding_check_refuses_a_file_carrying_another_codec(
        tmp_path, monkeypatch):
    """The defect: the delivery declared H264 and the encoder wrote
    ProRes. The file's codec is the evidence, and the check names both
    sides."""
    _stub_stream(monkeypatch, codec="prores")
    result = verify_encoding(str(tmp_path / "x.mp4"),
                             expected_codec="H264")
    assert result.passed is False
    assert result.severity == "error"
    assert "prores" in result.detail
    assert "H264" in result.detail


def test_the_container_is_measured_from_the_files_own_bytes(
        tmp_path):
    """The defect a filename would hide: a QuickTime file named .mp4 is
    a QuickTime file, and the container is read from the `ftyp` brand
    rather than from the extension."""
    quicktime = tmp_path / "named.mp4"
    quicktime.write_bytes(_quicktime_bytes())
    assert measure_container(str(quicktime)) == "quicktime"

    mp4 = tmp_path / "named.mov"
    mp4.write_bytes(_mp4_bytes())
    assert measure_container(str(mp4)) == "mp4"


def test_a_wrong_container_fails_the_encoding_check(tmp_path, monkeypatch):
    """The declaration names mp4 and the file's own bytes say QuickTime:
    the file is refused, and the detail says where the answer came
    from."""
    _stub_stream(monkeypatch, codec="h264")
    quicktime = tmp_path / "export.mp4"
    quicktime.write_bytes(_quicktime_bytes())
    result = verify_encoding(str(quicktime), expected_container="mp4")
    assert result.passed is False
    assert "quicktime" in result.detail
    assert "mp4" in result.detail


def test_the_pixel_format_is_graded_when_it_is_declared(
        tmp_path, monkeypatch):
    """A 10-bit file against a declared 8-bit delivery is a different
    file, and the declaration says so."""
    _stub_stream(monkeypatch, pixel_format="yuv422p10le")
    result = verify_encoding(str(tmp_path / "x.mp4"),
                             expected_pixel_format="yuv420p")
    assert result.passed is False
    assert "yuv422p10le" in result.detail


def test_an_undeclared_expectation_is_reported_not_graded(
        tmp_path, monkeypatch):
    """Nothing declared means nothing graded: the check reports what it
    measured and says which legs it did not check, rather than passing a
    value nobody named."""
    _stub_stream(monkeypatch, codec="prores")
    result = verify_encoding(str(tmp_path / "x.mp4"))
    assert result.passed is True
    assert result.value["codec"] == "prores"
    assert "not checked" in result.detail


def test_a_file_with_no_video_stream_skips_the_encoding_check(
        tmp_path, monkeypatch):
    """No picture to read proves nothing about the encoding. The check
    is skipped, and it says which check refuses a file with no
    picture."""
    _stub_stream(monkeypatch, codec=None, pixel_format=None)
    result = verify_encoding(str(tmp_path / "x.mp4"),
                             expected_codec="H264")
    assert result.passed is True
    assert "no video stream" in result.detail
    assert "verify_resolution" in result.detail


# ── QA-007: the channel configuration ──


def test_a_multichannel_bed_fails_the_channel_configuration(
        tmp_path, monkeypatch):
    """The defect: the export carries a six-channel bed. A delivered
    program mix is mono or stereo; a file carrying stems is not one."""
    _stub_stream(monkeypatch, channels=[6])
    result = verify_channel_configuration(str(tmp_path / "x.mp4"))
    assert result.passed is False
    assert "6" in result.detail


def test_a_stereo_export_passes_the_channel_configuration(
        tmp_path, monkeypatch):
    """The everyday case: a stereo program mix passes and the measured
    layout is on the record."""
    _stub_stream(monkeypatch, channels=[2])
    result = verify_channel_configuration(str(tmp_path / "x.mp4"))
    assert result.passed is True
    assert result.value[0]["channels"] == 2
    assert result.value[0]["layout"] == "stereo"


def test_a_declared_channel_count_is_matched_exactly(
        tmp_path, monkeypatch):
    """The declaration wins: a mono delivery is not satisfied by a
    stereo file."""
    _stub_stream(monkeypatch, channels=[2])
    result = verify_channel_configuration(str(tmp_path / "x.mp4"),
                                          expected_channels=1)
    assert result.passed is False
    assert "1" in result.detail


def test_a_file_with_no_audio_skips_the_channel_configuration(
        tmp_path, monkeypatch):
    """No audio to read proves nothing: skipped, and the detail says
    which check refuses a file with no audio."""
    _stub_stream(monkeypatch, channels=[])
    result = verify_channel_configuration(str(tmp_path / "x.mp4"))
    assert result.passed is True
    assert "no audio stream" in result.detail


# ── QA-007: clipping ──


def _pcm(values, channels=1) -> np.ndarray:
    return np.asarray(values, dtype="<i2")


def test_a_flat_top_at_full_scale_fails_the_clipping_check(
        tmp_path, monkeypatch):
    """The defect: three identical samples at the ceiling is a clipped
    waveform, and the check names the second it happens at."""
    values = [0, 1000, -1000] + [32767] * CLIP_RUN_SAMPLES + [0, -2000]
    monkeypatch.setattr(
        render_qa, "_decode_pcm16_channels",
        lambda path, rate=48000: (_pcm(values), 1))
    result = detect_clipping(str(tmp_path / "x.mp4"))
    assert result.passed is False
    assert result.value["clipped_samples"] == CLIP_RUN_SAMPLES
    assert result.value["runs"][0]["start"] == pytest.approx(
        3 / 48000, abs=1e-6)


def test_a_loud_but_intact_signal_is_not_clipping(
        tmp_path, monkeypatch):
    """The false positive this avoids: a signal that reaches the ceiling
    once per cycle is loud, not damaged. The true-peak half of
    `measure_lufs` answers 'too loud'; this one answers 'damaged'."""
    values = [0, 32767, 0, -32768, 0, 12345, -12345, 0]
    monkeypatch.setattr(
        render_qa, "_decode_pcm16_channels",
        lambda path, rate=48000: (_pcm(values), 1))
    result = detect_clipping(str(tmp_path / "x.mp4"))
    assert result.passed is True
    assert result.value["clipped_samples"] == 0


def test_a_clip_on_one_channel_of_a_stereo_pair_is_found(
        tmp_path, monkeypatch):
    """The defect: only the right channel is clipped, and summing the
    channels would hide it."""
    values = ([0, 100] * 3) + ([0, 32767] * CLIP_RUN_SAMPLES) + [0, 0]
    monkeypatch.setattr(
        render_qa, "_decode_pcm16_channels",
        lambda path, rate=48000: (_pcm(values), 2))
    result = detect_clipping(str(tmp_path / "x.mp4"))
    assert result.passed is False
    assert result.value["runs"][0]["channel"] == 1


def test_a_file_with_no_audio_skips_the_clipping_check(
        tmp_path, monkeypatch):
    """No audio to decode proves nothing: skipped, and it says which
    check refuses a file with no audio."""
    monkeypatch.setattr(
        render_qa, "_decode_pcm16_channels",
        lambda path, rate=48000: (None, 0))
    result = detect_clipping(str(tmp_path / "x.mp4"))
    assert result.passed is True
    assert "no audio" in result.detail
