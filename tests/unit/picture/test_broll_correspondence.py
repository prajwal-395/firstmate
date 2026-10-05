"""Tests for the B-roll correspondence check (gap E3).

Each test names a behavioral defect: a cutaway that does not illustrate
the speech it covers, a cutaway that does, a reply nobody parsed, a
verdict that was cached, and a check that reports without gating.
"""

import json
import os
from unittest.mock import patch

import pytest

from library.tools import broll_correspondence as bc


class StubModel:
    """A VLM stub: returns a canned reply, records what it was asked."""

    MODEL_ID = "stub-model"

    def __init__(self, reply='{"illustrates": "yes", "reason": "ok"}'):
        self.reply = reply
        self.calls = []

    def analyze_images(self, image_paths, prompt, max_tokens=120):
        self.calls.append({
            "image_paths": list(image_paths),
            "prompt": prompt,
            "max_tokens": max_tokens,
        })
        return self.reply


def _manifest_with_cutaway(timeline_in=0.0, timeline_out=4.0,
                           source_in=100.0, source_out=104.0):
    return {
        "tracks": {
            "V1": {"clips": [{
                "clip_name": "a_roll",
                "source_file": "/footage/a.mov",
                "source_in": 0.0,
                "source_out": 20.0,
                "timeline_in_frame": 0.0,
                "timeline_out_frame": 20.0,
            }]},
            "V2": {"clips": [{
                "clip_name": "cutaway_1",
                "source_file": "/footage/b.mov",
                "source_in": source_in,
                "source_out": source_out,
                "timeline_in_frame": timeline_in,
                "timeline_out_frame": timeline_out,
            }]},
        },
    }


def _timeline_with_audio():
    return {
        "metadata": {"fps": 1.0, "name": "test"},
        "tracks": [{
            "type": "audio",
            "name": "A1",
            "clips": [{
                "file_path": "/footage/a.mov",
                "record_in": 0,
                "record_out": 20,
                "source_in": 0.0,
            }],
        }],
    }


def _transcript_with_words(words, source_file="/footage/a.mov"):
    return {
        "segments": [{
            "source_file": source_file,
            "source_start": 0.0,
            "timeline_start": 0.0,
            "words": [{"word": w, "start": i * 1.0, "end": i * 1.0 + 0.9}
                      for i, w in enumerate(words)],
        }],
    }


def _mock_draw_strip(source_file, times, out_path):
    with open(out_path, "wb") as handle:
        handle.write(b"\xff\xd8\xff\xd9")
    return True


@pytest.fixture
def mock_frames():
    with patch("library.tools.window_frames.draw_strip",
               side_effect=_mock_draw_strip):
        yield


def test_non_illustrating_cutaway_is_reported(mock_frames):
    """A cutaway whose picture does not illustrate the speech is named.

    The defect: B-roll that shows the wrong thing passes every gate today.
    """
    model = StubModel('{"illustrates": "no", "reason": "shows a wall"}')
    result = bc.measure_broll_correspondence(
        "video.mp4", _manifest_with_cutaway(),
        _timeline_with_audio(), _transcript_with_words(
            ["the", "launch", "failed", "spectacularly"]),
        model=model, project_folder="")

    assert result.passed is True
    assert result.value["cutaways"] == 1
    assert len(result.value["non_illustrating"]) == 1
    assert result.value["non_illustrating"][0]["illustrates"] is False
    assert result.severity == "warning"
    assert "REPORTED ONLY" in result.detail


def test_illustrating_cutaway_passes(mock_frames):
    """A cutaway whose picture illustrates the speech passes."""
    model = StubModel('{"illustrates": "yes", "reason": "rocket visible"}')
    result = bc.measure_broll_correspondence(
        "video.mp4", _manifest_with_cutaway(),
        _timeline_with_audio(), _transcript_with_words(
            ["the", "launch", "failed", "spectacularly"]),
        model=model, project_folder="")

    assert result.passed is True
    assert result.value["cutaways"] == 1
    assert len(result.value["non_illustrating"]) == 0
    assert result.value["verdicts"][0]["illustrates"] is True
    assert result.severity == "info"


def test_unparsed_vlm_reply_is_reported(mock_frames):
    """A VLM reply that cannot be parsed is reported, not guessed into a yes."""
    model = StubModel("I cannot answer that question.")
    result = bc.measure_broll_correspondence(
        "video.mp4", _manifest_with_cutaway(),
        _timeline_with_audio(), _transcript_with_words(
            ["the", "launch", "failed"]),
        model=model, project_folder="")

    assert result.passed is True
    assert result.value["verdicts"][0]["illustrates"] is None
    assert len(result.value["unparsed"]) == 1


def test_verdict_is_cached_by_frame_hash(mock_frames, tmp_path):
    """The same cutaway with the same frames and words costs no second call."""
    model = StubModel('{"illustrates": "yes", "reason": "ok"}')
    manifest = _manifest_with_cutaway()
    timeline = _timeline_with_audio()
    transcript = _transcript_with_words(["the", "launch", "failed"])
    project_folder = str(tmp_path)

    result1 = bc.measure_broll_correspondence(
        "video.mp4", manifest, timeline, transcript,
        model=model, project_folder=project_folder)
    assert len(model.calls) == 1
    assert result1.value["verdicts"][0]["cached"] is False

    result2 = bc.measure_broll_correspondence(
        "video.mp4", manifest, timeline, transcript,
        model=model, project_folder=project_folder)
    assert len(model.calls) == 1
    assert result2.value["verdicts"][0]["cached"] is True


def test_cutaway_with_no_speech_at_span_is_reported(mock_frames):
    """A cutaway placed over silence is reported as having no speech."""
    model = StubModel()
    result = bc.measure_broll_correspondence(
        "video.mp4", _manifest_with_cutaway(timeline_in=5.0,
                                            timeline_out=7.0),
        _timeline_with_audio(), _transcript_with_words(
            ["the", "launch", "failed"]),
        model=model, project_folder="")

    assert result.passed is True
    assert result.value["verdicts"][0]["illustrates"] is None
    assert "no speech" in result.value["verdicts"][0]["reason"]
    assert len(model.calls) == 0


def test_report_only_does_not_gate(mock_frames):
    """A non-illustrating cutaway does not fail the render.

    The defect: whether a non-illustrating cutaway blocks delivery is a
    pending captain call, so the check reports and does not gate.
    """
    assert bc.BROLL_CORRESPONDENCE_GATES is False

    model = StubModel('{"illustrates": "no", "reason": "unrelated"}')
    result = bc.measure_broll_correspondence(
        "video.mp4", _manifest_with_cutaway(),
        _timeline_with_audio(), _transcript_with_words(
            ["the", "launch", "failed"]),
        model=model, project_folder="")

    assert result.passed is True
    assert result.severity == "warning"


def test_no_cutaways_reports_cleanly():
    """A manifest with no V2 cutaways reports that nothing was measured."""
    model = StubModel()
    result = bc.measure_broll_correspondence(
        "video.mp4", {"tracks": {"V1": {"clips": []}}},
        _timeline_with_audio(), _transcript_with_words(["hello"]),
        model=model, project_folder="")

    assert result.passed is True
    assert result.value["cutaways"] == 0
    assert result.severity == "info"
    assert len(model.calls) == 0
