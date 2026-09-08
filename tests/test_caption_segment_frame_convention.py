"""The caption segment frame convention: media fps == timeline fps.

vep-caption-segment-off-by-one-frame. The 2026-09-08 rebuild placed content
CLEAN and still drew 15 x F2 with delta -1, every segment exactly one frame
short in the same direction. That is a convention mismatch, not noise: the
reel builder rendered caption media at ``int(round(fps))`` = 24fps for a
24000/1001 timeline, and Resolve's time-mapping of the 24fps source onto
23.976 drops the fractional frame on every segment this size (a 135-frame
24fps source reads back as 134 timeline frames). The verifier counts
``round(span * 24000/1001)`` - what the timeline should carry - and is right.

The fix renders caption media at the exact timeline fps, which maps 1:1
under any snap direction. These tests pin that contract from both ends:
the builder counts what the verifier expects, and the verifier still
catches a genuine one-frame shortfall.
"""

from __future__ import annotations

import pytest

from library.tools import operations
from library.tools import reel_spine
from library.tools.reel_build import reel_subtitle_segments
from library.tools.reel_conformance_verifier import (
    FindingClass,
    PlannedCaption,
    TimelineItem,
    check_caption_duration,
)


FPS = 24000 / 1001  # the reel timeline rate, exact

# A span where counting at 24fps and at 24000/1001 disagree by a frame:
# round(2.5625 * 24) = 62, round(2.5625 * 24000/1001) = 61.
SPAN_START = 20.0
SPAN = 2.5625
SPAN_END = SPAN_START + SPAN
EXPECTED_FRAMES = max(int(round(SPAN * FPS)), 1)
assert EXPECTED_FRAMES == 61


def _entries():
    return [
        {
            "timeline_start": SPAN_START,
            "timeline_end": SPAN_END,
            "text": "search did not change",
            "spine_block_position": "body_7",
            "speaker": "Craig",
            "words": [],
        }
    ]


def _captured_props(monkeypatch, tmp_path):
    """Drive the real reel caption path with stubbed plan/render operations.

    The spine producer and both registry operations are replaced, but
    `generate_subtitle_props_per_block` - the frame counting under test -
    runs unmodified, called exactly as `reel_subtitle_segments` calls it.
    Returns the props dicts the (stubbed) renderer was handed.
    """
    captured = []

    def fake_spine(moment, transcript, keep_ranges, lead_seconds=0.0):
        return {}

    class FakePlan:
        def run(self, spine, **kwargs):
            return {"subtitle_plan": {
                "subtitle_entries": _entries(),
                "style": {"fontFamily": "Montserrat"},
            }}

    class FakeRender:
        def run(self, props, out_dir, name, progress="", reuse=False):
            captured.append(props)
            return {"overlay_path": "/s/seg.mov"}

    def fake_get(name):
        if name == "subtitles.plan":
            return FakePlan()
        if name == "subtitles.render_segment":
            return FakeRender()
        raise AssertionError(f"unexpected operation {name!r}")

    monkeypatch.setattr(reel_spine, "spine_for_reel", fake_spine)
    monkeypatch.setattr(operations, "get", fake_get)

    import unittest.mock as mock

    reel_subtitle_segments(
        mock.MagicMock(), {"segments": []}, [(0.0, 60.0)],
        str(tmp_path), FPS, 1080, 1920, timeline_name="Reel 01")
    return captured


def _item(start_frame: int, duration_frames: int) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=3,
        start_frame=start_frame,
        end_frame=start_frame + duration_frames,
        duration_frames=duration_frames,
        source_start_frame=0, source_end_frame=duration_frames,
        source_file="/s/seg.mov", speaker="Craig", name="seg",
    )


def test_reel_caption_media_is_counted_in_timeline_frames(monkeypatch, tmp_path):
    """The builder's segment length equals the verifier's expectation.

    Fails while the reel path renders at int(round(fps)): the props count
    62 media frames for a span the verifier grades as 61, which is the
    mismatch that read back as F2 delta -1 on every rebuilt segment.
    """
    (props,) = _captured_props(monkeypatch, tmp_path)
    assert props["fps"] == pytest.approx(FPS)
    trimmed = props["_source_out_frame"] - props["_source_in_frame"]
    assert trimmed == EXPECTED_FRAMES, (
        f"segment counted {trimmed} frames for a {SPAN}s span the verifier "
        f"grades as {EXPECTED_FRAMES} - the placer and the verifier count "
        f"at different frame rates")


def test_placed_segment_passes_the_duration_gate(monkeypatch, tmp_path):
    """The contract end to end: what the builder counts draws no F2.

    The placed item is built from the props the fixed call site produces,
    at the record frame the builder places. Before the fix this draws F2
    (placed 62 against expected 61); after, it is clean.
    """
    (props,) = _captured_props(monkeypatch, tmp_path)
    trimmed = props["_source_out_frame"] - props["_source_in_frame"]
    record = int(round(SPAN_START * FPS))
    planned = (PlannedCaption(
        start_seconds=SPAN_START, end_seconds=SPAN_END,
        text="search did not change", speaker="Craig",
        frames=EXPECTED_FRAMES, block_position="body_7"),)
    findings = check_caption_duration(
        "Reel 01", planned, (_item(record, trimmed),), FPS)
    assert findings == [], (
        f"a faithfully placed segment must draw no finding; got "
        f"{[(f.finding_class, f.message) for f in findings]}")


def test_a_genuinely_one_frame_short_segment_still_fails():
    """The gate stays exact: one frame short is still F2 with delta -1.

    This is the anti-tolerance test. The fix moves the builder onto the
    verifier's counting; it must not teach the verifier to stop noticing
    single-frame errors. Unaffected by the fix - passes before and after.
    """
    planned = (PlannedCaption(
        start_seconds=SPAN_START, end_seconds=SPAN_END,
        text="search did not change", speaker="Craig",
        frames=EXPECTED_FRAMES, block_position="body_7"),)
    record = int(round(SPAN_START * FPS))
    short = check_caption_duration(
        "Reel 01", planned, (_item(record, EXPECTED_FRAMES - 1),), FPS)
    assert len(short) == 1
    assert short[0].finding_class == FindingClass.F2
    assert short[0].detail["delta"] == -1
    assert short[0].detail["planned_frames"] == EXPECTED_FRAMES
    assert short[0].detail["actual_frames"] == EXPECTED_FRAMES - 1

    exact = check_caption_duration(
        "Reel 01", planned, (_item(record, EXPECTED_FRAMES),), FPS)
    assert exact == []
