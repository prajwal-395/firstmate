"""`verify_deliverable` checks the FILE's codec against the spec's.

Covers the codec half of `reel_deliver.verify_deliverable`: the
declaration was already checked at the preset, this checks what the
encoder actually wrote - a render that encoded the wrong codec ships
wrong even when the preset declared right. Instruments stubbed, no
ffmpeg, no Resolve.
"""
from __future__ import annotations

from library.tools import pool_stream_meta, reel_deliver
from library.tools import render_qa
from library.tools.render_qa import RenderQAResult


def _result(metric, passed=True):
    return RenderQAResult(metric, passed, {"v": 1}, {"t": 1}, "info", metric)


def _stubbed(monkeypatch, codec="h264"):
    monkeypatch.setattr(render_qa, "verify_duration",
                        lambda path, expected: RenderQAResult(
                            "duration", True, 10.0, 10.0, "info", "10s"))
    monkeypatch.setattr(render_qa, "verify_resolution",
                        lambda path, **kwargs: _result("resolution"))
    monkeypatch.setattr(render_qa, "verify_audio_streams",
                        lambda path, **kwargs: _result("audio"))
    monkeypatch.setattr(render_qa, "detect_black_frames",
                        lambda path, **kwargs: RenderQAResult(
                            "black", True, [], None, "info", "no black"))
    monkeypatch.setattr(pool_stream_meta, "disk_stream",
                        lambda path: {"width": 1080, "height": 1920,
                                      "codec": codec})


def _file(tmp_path):
    video = tmp_path / "deliverable.mp4"
    video.write_bytes(b"\x00" * 64)
    return str(video)


def test_a_matching_codec_passes_case_insensitively(tmp_path, monkeypatch):
    """`h264` on disk is the spec's `H264` - the check compares what
    the codec IS, not its capitalisation."""
    _stubbed(monkeypatch, codec="h264")
    verdict = reel_deliver.verify_deliverable(
        _file(tmp_path), 10.0, 1080, 1920, expected_codec="H264")
    assert verdict["codec"]["passed"] is True
    assert verdict["passed"] is True


def test_a_foreign_codec_fails_the_deliverable(tmp_path, monkeypatch):
    """The defect: the declaration checked, the file never did. A file
    carrying ProRes against an H264 spec fails, naming both."""
    _stubbed(monkeypatch, codec="prores")
    verdict = reel_deliver.verify_deliverable(
        _file(tmp_path), 10.0, 1080, 1920, expected_codec="H264")
    assert verdict["codec"]["passed"] is False
    assert "prores" in verdict["codec"]["detail"]
    assert "H264" in verdict["codec"]["detail"]
    assert verdict["passed"] is False


def test_no_expectation_means_unchecked(tmp_path, monkeypatch):
    """Existing callers pass no codec and see no behavior change: an
    empty expectation skips rather than inventing one."""
    _stubbed(monkeypatch, codec="prores")
    verdict = reel_deliver.verify_deliverable(
        _file(tmp_path), 10.0, 1080, 1920)
    assert verdict["codec"]["passed"] is True
    assert verdict["passed"] is True


def test_an_unmeasurable_file_is_skipped_not_flagged(tmp_path, monkeypatch):
    """No video stream to compare proves nothing: skipped, the same
    policy `_disk_resolution` applies."""
    _stubbed(monkeypatch, codec=None)
    verdict = reel_deliver.verify_deliverable(
        _file(tmp_path), 10.0, 1080, 1920, expected_codec="H264")
    assert verdict["codec"]["passed"] is True
    assert verdict["passed"] is True
