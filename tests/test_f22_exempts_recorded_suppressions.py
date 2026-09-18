"""F22 exempts a planned segment the build recorded as suppressed.

`do_not_draw` suppresses the PLACEMENT, never the plan - so the first
reel rebuilt after the captain's Reel 01 deletion (mg_geo-podcast_b46f83e1,
beat_accent) recorded the segment and placed nothing, and F22 failed the
build on the absence. The exemption fires only on segment ids the build
itself stamped onto the record (`suppressed`), never on a rule merely
existing.
"""

from library.tools.reel_conformance_verifier import (
    FindingClass,
    TimelineItem,
    check_semantic_visuals,
)

FPS = 24000 / 1001


def _item(start_frame, frames, name="mg_x"):
    return TimelineItem(
        track_type="video",
        track_index=5,
        start_frame=start_frame,
        end_frame=start_frame + frames,
        duration_frames=frames,
        source_start_frame=0,
        source_end_frame=frames,
        source_file="/tmp/mg_x.mov",
        speaker=None,
        name=name,
    )


def _planned(suppressed=()):
    return {
        "reel": "Reel 01 - test",
        "basis": "planned",
        "entries": [],
        "dropped": [],
        "segments": [
            {
                "overlay_path": "/tmp/mg_a.mov",
                "segment_id": "mg_a",
                "placement_label": "vox_test_00",
                "timeline_start": 10.0,
                "timeline_end": 13.5,
                "total_frames": 84,
                "elements": ["title_lockup"],
            },
            {
                "overlay_path": "/tmp/mg_b.mov",
                "segment_id": "mg_b",
                "placement_label": "vox_test_01",
                "timeline_start": 24.066,
                "timeline_end": 24.483,
                "total_frames": 10,
                "elements": ["beat_accent"],
            },
        ],
        "suppressed": [{"segment_id": sid} for sid in suppressed],
    }


def test_suppressed_segment_absent_draws_no_f22():
    planned = _planned(suppressed=("mg_b",))
    placed_a = _item(int(round(10.0 * FPS)), 84, name="mg_a")
    findings = check_semantic_visuals(
        "Reel 01 - test", [placed_a], planned, FPS)
    assert [f for f in findings
            if f.finding_class == FindingClass.F22] == []


def test_unsuppressed_missing_segment_still_errors():
    planned = _planned(suppressed=())
    placed_a = _item(int(round(10.0 * FPS)), 84, name="mg_a")
    findings = check_semantic_visuals(
        "Reel 01 - test", [placed_a], planned, FPS)
    errors = [f for f in findings
              if f.finding_class == FindingClass.F22
              and f.severity == "error"]
    assert len(errors) == 1
    assert "577" in errors[0].message


def test_suppression_naming_nothing_suppresses_nothing():
    planned = _planned(suppressed=("mg_elsewhere",))
    placed_a = _item(int(round(10.0 * FPS)), 84, name="mg_a")
    findings = check_semantic_visuals(
        "Reel 01 - test", [placed_a], planned, FPS)
    errors = [f for f in findings
              if f.finding_class == FindingClass.F22
              and f.severity == "error"]
    assert len(errors) == 1


def test_suppressed_segment_placed_anyway_is_flagged():
    # The record says "held back" but the timeline plays it: that
    # contradiction is fail-closed, not exempted.
    planned = _planned(suppressed=("mg_b",))
    placed_a = _item(int(round(10.0 * FPS)), 84, name="mg_a")
    placed_b = _item(int(round(24.066 * FPS)), 10, name="mg_b")
    findings = check_semantic_visuals(
        "Reel 01 - test", [placed_a, placed_b], planned, FPS)
    errors = [f for f in findings
              if f.finding_class == FindingClass.F22
              and f.severity == "error"]
    assert len(errors) == 1
    assert "no recorded semantic visual accounts for" in errors[0].message
