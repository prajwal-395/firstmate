"""Caption quality (gap E4): legibility and obscuring, as render-side checks.

The captain's bar for captions is "legible, natural, grouped, not
obscuring".  `subtitle_qa` proves a caption is inside the frame and in
the lower half; these two measure what the viewer gets - whether the
type is tall enough to read at the delivered resolution, and whether the
card covers the speaker's face.  Both are deterministic and free, and
both REPORT rather than gate: whether a failing caption blocks delivery
is a pending captain call, the same ruling P2 and P3 carry.

The defects these tests name:

* A caption rendered too small to read at the delivered resolution
  passes every gate today - `render_watch` asks a VLM about text size
  and `perceptual_qa.text_legible` is not calibrated to catch it.
* A caption box that covers the speaker's face passes every gate today -
  the safe-area insets are platform UI zones, not subject-aware.

Nothing here asserts a registry or enumeration contains named entries;
each test drives the check against a named defect and reads the verdict
the reader would give it.
"""
from __future__ import annotations
from unittest.mock import patch
import numpy as np
import pytest
from library.tools import qa_findings, render_qa
from library.tools.render_qa import (
    CAPTION_MIN_HEIGHT_FRACTION,
    OverlaySegment,
    measure_caption_legibility,
    measure_caption_obscuring,
)

FRAME = (1080, 1920)
# `_master_face_sample_size(1080, 1920)` - short side bounded at 480.
SAMPLE = (480, 854)


def _segment(path="/x/sub_a.mov", start=1.0, end=3.0, kind="subtitle_overlay"):
    return OverlaySegment(path, start, end, 0.0, kind=kind)


def _probe(canvas=(1080, 1920), ink=(100, 1500, 500, 1700), min_height=300):
    return {
        "canvas": canvas,
        "ink": ink,
        "min_frame_ink_height": min_height,
        "frames": 10,
    }


class _FakeCascade:
    """A cascade that finds exactly the box the test wants it to."""

    def __init__(self, box):
        self.box = box

    def empty(self):
        return False

    def detectMultiScale(self, *_a, **_kw):
        return [] if self.box is None else [self.box]


_UNSET = object()


def _legibility(segments, probe=_UNSET, frame=FRAME):
    with patch.object(render_qa, "_probe_overlay_ink",
                      return_value=_probe() if probe is _UNSET else probe), \
            patch.object(render_qa, "_probe_video_size", return_value=frame):
        return measure_caption_legibility("/x/master.mp4", segments)


def _obscuring(segments, probe=_UNSET, face_box=None, frame=FRAME):
    with patch.object(render_qa, "_probe_overlay_ink",
                      return_value=_probe() if probe is _UNSET else probe), \
            patch.object(render_qa, "_probe_video_size", return_value=frame), \
            patch.object(render_qa, "load_face_cascade",
                         return_value=_FakeCascade(face_box)), \
            patch.object(render_qa, "_stream_raw_frames",
                         return_value=iter(
                             [np.zeros((1, SAMPLE[1], SAMPLE[0]), dtype=np.uint8)])):
        return measure_caption_obscuring("/x/master.mp4", segments)


class TestCaptionLegibility:
    def test_a_caption_too_small_to_read_is_reported_not_gated(self):
        """The defect: a caption rendered at 10px passes every gate.

        It is measured, named, and reported - and it does NOT fail the
        build, because whether an illegible caption blocks delivery is a
        pending captain call.
        """
        result = _legibility([_segment()], probe=_probe(min_height=10))
        assert result.passed is True
        assert result.severity == "warning"
        assert result.value["illegible"]
        row = result.value["illegible"][0]
        assert row["smallest_card_height"] < result.value["floor"]
        assert "REPORTED ONLY" in result.detail

    def test_a_legible_caption_is_not_flagged(self):
        result = _legibility([_segment()], probe=_probe(min_height=300))
        assert result.passed is True
        assert result.severity == "info"
        assert result.value["illegible"] == []

    def test_only_subtitle_overlays_are_judged(self):
        """A motion-graphic overlay is not a caption and is left alone."""
        result = _legibility([_segment(kind="motion_graphics_overlay")],
                             probe=_probe(min_height=10))
        assert result.value["segments"] == 0
        assert "not measured" in result.detail

    def test_an_undecodable_overlay_is_reported_not_silently_passed(self):
        result = _legibility([_segment()], probe=None)
        assert result.passed is True
        assert result.value["illegible"]
        assert "could not be decoded" in result.value["illegible"][0]["reason"]

    def test_the_floor_is_a_fraction_of_the_frame_height(self):
        """The floor scales with the delivery frame, not a hardcoded px."""
        result = _legibility([_segment()], probe=_probe(min_height=10))
        assert result.value["floor"] == pytest.approx(
            FRAME[1] * CAPTION_MIN_HEIGHT_FRACTION)


class TestCaptionObscuring:
    def test_a_caption_over_the_speakers_face_is_reported_not_gated(self):
        """The defect: a caption box covering the face passes every gate.

        The face is low in the frame - where a punch-in or a seated
        speaker puts it - and the caption row sits on it.
        """
        # A face the cascade detects low in the frame, under the caption.
        face_box = (50, 667, 150, 88)
        result = _obscuring([_segment()], face_box=face_box)
        assert result.passed is True  # report-only, never gates
        assert result.severity == "warning"
        assert result.value["obscuring"]
        assert result.value["obscuring"][0]["overlapping_faces"] == 1
        assert "REPORTED ONLY" in result.detail

    def test_a_caption_clear_of_the_face_is_not_flagged(self):
        """A face in the upper frame and a caption in the lower half."""
        face_box = (50, 50, 150, 150)
        result = _obscuring([_segment()], face_box=face_box)
        assert result.passed is True
        assert result.severity == "info"
        assert result.value["obscuring"] == []

    def test_a_face_too_small_to_be_the_subject_is_ignored(self):
        """A passer-by at the edge is not the speaker under the caption."""
        # Below MIN_SUBJECT_FACE_AREA of the sampled frame.
        face_box = (50, 667, 40, 40)
        result = _obscuring([_segment()], face_box=face_box)
        assert result.value["obscuring"] == []

    def test_only_subtitle_overlays_are_judged(self):
        result = _obscuring([_segment(kind="motion_graphics_overlay")],
                            face_box=(50, 667, 150, 88))
        assert result.value["segments"] == 0
        assert "not measured" in result.detail

    def test_an_undecodable_overlay_is_reported_not_silently_passed(self):
        result = _obscuring([_segment()], probe=None, face_box=(50, 667, 150, 88))
        assert result.passed is True
        assert result.value["obscuring"]
        assert "could not be decoded" in result.value["obscuring"][0]["reason"]


class TestTheReaderGivesThemAVerdict:
    """A report-only caption finding must reach a reader as ADVISORY.

    The defect this names is the one `qa_findings` exists to remove: a
    measurement nobody reads is indistinguishable from a measurement
    nobody took.  A passed-but-warning caption finding is read as
    ADVISORY - printed, owned, and counted - never as CLEAN and never as
    a blocking failure.
    """

    def test_an_illegible_caption_finding_reads_as_advisory(self):
        row = {"metric": "caption_legibility", "passed": True,
               "value": 1, "threshold": 38, "severity": "warning",
               "detail": "1 of 1 caption segment(s) ink under 38px"}
        finding = qa_findings.read_qa_report([row]).findings[0]
        assert finding.verdict == qa_findings.ADVISORY
        assert finding.blocks is False
        assert finding.owner == "plan_subtitles"
        assert finding.reader is not None

    def test_an_obscuring_caption_finding_reads_as_advisory(self):
        row = {"metric": "caption_obscuring", "passed": True,
               "value": 1, "threshold": 8, "severity": "warning",
               "detail": "1 of 1 caption segment(s) overlap a detected face"}
        finding = qa_findings.read_qa_report([row]).findings[0]
        assert finding.verdict == qa_findings.ADVISORY
        assert finding.blocks is False

    def test_a_clean_caption_finding_is_counted_not_printed(self):
        row = {"metric": "caption_legibility", "passed": True,
               "value": 0, "threshold": 38, "severity": "info",
               "detail": "0 of 1 caption segment(s) ink under 38px"}
        finding = qa_findings.read_qa_report([row]).findings[0]
        assert finding.verdict == qa_findings.CLEAN
