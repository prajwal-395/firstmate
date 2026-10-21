"""Per-deliverable QC judges one file against its own spec.

Covers library/tools/deliverable_qc.py, stubbing the instruments the
way the render_qa suite does (paths and strips without ffmpeg):
duration, black frames and loudness gate against the passed spec's own
numbers, while the grade and caption findings report without gating.
"""
from __future__ import annotations

import dataclasses

import pytest

from library.tools import deliverable_qc
from library.tools.delivery_spec import resolve_delivery_spec
from library.tools.render_qa import RenderQAResult


def _result(metric, passed=True):
    return RenderQAResult(metric, passed, {"v": 1}, {"t": 1}, "info", metric)


def _stubbed(monkeypatch, duration=True, black=True, loudness=True,
             grade=True, captions=(), silence=True, channels=True):
    from library.tools import render_qa
    from library.tools import subtitle_qa

    monkeypatch.setattr(render_qa, "verify_duration",
                        lambda path, expected: _result("duration", duration))
    monkeypatch.setattr(render_qa, "detect_black_frames",
                        lambda path, **kwargs: _result("black", black))
    seen = {}

    def fake_lufs(path, target_lufs=0.0, tolerance=0.0,
                  true_peak_ceiling=0.0):
        seen.update(target_lufs=target_lufs, tolerance=tolerance,
                    true_peak_ceiling=true_peak_ceiling)
        return _result("loudness", loudness)

    monkeypatch.setattr(render_qa, "measure_lufs", fake_lufs)
    monkeypatch.setattr(render_qa, "measure_grade_delivery",
                        lambda path, spans=None: _result("grade", grade))
    # The AUDIO SPEC's other two legs: the silence under picture that
    # catches missing dialogue, and the channel presence. They are
    # stubbed with the rest, because this suite judges the QC's
    # STRUCTURE (what gates, what reports), not the instruments.
    monkeypatch.setattr(
        render_qa, "measure_silence_under_picture",
        lambda path, **kwargs: _result("silence_under_picture", silence))
    monkeypatch.setattr(
        render_qa, "verify_channel_presence",
        lambda path: _result("channels", channels))
    monkeypatch.setattr(subtitle_qa, "verify_subtitle_timing",
                        lambda data, **kwargs: list(captions))
    return seen


def _file(tmp_path):
    video = tmp_path / "deliverable.mp4"
    video.write_bytes(b"\x00" * 64)
    return str(video)


def test_loudness_is_judged_against_the_passed_spec(tmp_path, monkeypatch):
    """The defect: loudness judged against a default that could drift
    from the deliverable's own spec. The spec's target, tolerance and
    ceiling all reach the instrument."""
    seen = _stubbed(monkeypatch)
    spec = dataclasses.replace(
        resolve_delivery_spec("master"),
        loudness_target_lufs=-16.0, loudness_tolerance_lufs=2.0,
        true_peak_ceiling_dbtp=-2.0)
    verdict = deliverable_qc.run_deliverable_qc(
        _file(tmp_path), spec, expected_duration=10.0)
    assert seen == {"target_lufs": -16.0, "tolerance": 2.0,
                    "true_peak_ceiling": -2.0}
    assert verdict.platform == "master"
    assert verdict.passed is True


def test_a_failing_file_check_fails_the_verdict(tmp_path, monkeypatch):
    """Duration, black frames and loudness gate: any one failing fails
    the deliverable."""
    for kwargs in ({"loudness": False}, {"duration": False},
                   {"black": False}):
        _stubbed(monkeypatch, **kwargs)
        verdict = deliverable_qc.run_deliverable_qc(
            _file(tmp_path), resolve_delivery_spec("tiktok"),
            expected_duration=10.0)
        assert verdict.passed is False


def test_grade_and_captions_report_without_gating(
        tmp_path, monkeypatch):
    """A failing grade or caption finding is said loudly, never failed
    on: the shortfall confounds grade, pipeline and encode, and the
    caption check is called as-is, not re-implemented as a second gate."""
    caption_fail = _result("subtitle_timing", False)
    _stubbed(monkeypatch, grade=False, captions=[caption_fail])
    verdict = deliverable_qc.run_deliverable_qc(
        _file(tmp_path), resolve_delivery_spec("tiktok"),
        expected_duration=10.0,
        subtitle_data=[{"timeline_start": 0.0, "timeline_end": 1.0,
                        "text": "hi"}])
    assert verdict.passed is True
    assert verdict.grade.passed is False
    assert verdict.captions == [caption_fail]


def test_no_caption_data_means_no_caption_finding(tmp_path, monkeypatch):
    """Nothing supplied, nothing judged: an absent caption document is
    not an empty one and is not passed to the check."""
    _stubbed(monkeypatch)
    verdict = deliverable_qc.run_deliverable_qc(
        _file(tmp_path), resolve_delivery_spec("tiktok"),
        expected_duration=10.0)
    assert verdict.captions is None
    assert verdict.passed is True


def test_an_absent_render_is_not_a_passing_one(tmp_path, monkeypatch):
    """QC on a file that was never rendered raises rather than
    reporting a pass."""
    _stubbed(monkeypatch)
    with pytest.raises(FileNotFoundError):
        deliverable_qc.run_deliverable_qc(
            str(tmp_path / "missing.mp4"),
            resolve_delivery_spec("tiktok"), expected_duration=10.0)
