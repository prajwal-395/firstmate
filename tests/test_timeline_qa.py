import pytest
from library.tools.timeline_qa import (
    verify_clip_placement,
    verify_transitions,
    verify_color_grades,
    verify_audio,
    verify_fusion_comps,
    run_full_timeline_qa,
    QAReport
)

class MockMediaPoolItem:
    def __init__(self, name="test.mov", status="Online"):
        self.name = name
        self.status = status
    def GetClipProperty(self, prop):
        if prop == "File Name": return self.name
        if prop == "Status": return self.status
        return None

class MockTimelineItem:
    def __init__(self, start=0, end=30, mpi=None, comps=()):
        self.start = start
        self.end = end
        self.mpi = mpi or MockMediaPoolItem()
        self.name = self.mpi.name
        self.comps = list(comps)

    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start
    def GetMediaPoolItem(self): return self.mpi
    def GetName(self): return self.name
    def GetCDL(self): return {"Slope": "1.0 1.0 1.0"}
    def GetFusionCompNameList(self): return self.comps

class MockTimeline:
    def __init__(self, items, by_track=None):
        self.items = items
        self.by_track = by_track or {}
    def GetItemListInTrack(self, t_type, index):
        if self.by_track:
            return self.by_track.get(index, [])
        return self.items
    def GetTrackCount(self, t_type):
        return 2

def test_qa_report_structure():
    report = QAReport(station="test", passed=True)
    assert report.station == "test"
    assert report.passed is True
    assert isinstance(report.checks, list)

def test_verify_clip_placement():
    item = MockTimelineItem(start=0, end=30)
    timeline = MockTimeline([item])
    track_items = {"V1": [item]}
    manifest_clips = {"V1": [{"timeline_in_frame": 0, "timeline_out_frame": 30}]}
    
    report = verify_clip_placement(timeline, track_items, manifest_clips)
    assert report.passed

def test_gap_detection_logic():
    # Gap between 30 and 32
    item1 = MockTimelineItem(start=0, end=30)
    item2 = MockTimelineItem(start=32, end=60)
    timeline = MockTimeline([item1, item2])
    
    report = run_full_timeline_qa(timeline, None, {"project": {"frame_rate": 30, "duration_seconds": 2.0}})
    assert not report.passed
    assert any("gap_before" in c.name for c in report.checks)

def test_overlap_detection():
    # Overlap between 25 and 30
    item1 = MockTimelineItem(start=0, end=30)
    item2 = MockTimelineItem(start=25, end=60)
    timeline = MockTimeline([item1, item2])
    
    report = run_full_timeline_qa(timeline, None, {"project": {"frame_rate": 30, "duration_seconds": 2.0}})
    assert not report.passed
    assert any("overlap_before" in c.name for c in report.checks)

def test_verify_audio():
    timeline = MockTimeline([])
    report = verify_audio(timeline, None, {})
    assert report.passed

def test_verify_color_grades():
    item = MockTimelineItem()
    timeline = MockTimeline([item])
    manifest_color = {
        "per_clip_adjustments": [
            {
                "source_file": "test.mov",
                "cdl_values": {"slope_r": 1.0, "slope_g": 1.0, "slope_b": 1.0}
            }
        ]
    }
    report = verify_color_grades(timeline, None, manifest_color)
    assert report.passed


def test_verify_color_grades_records_a_readback_that_raises():
    """The bare `except: pass` used to make a broken readback a pass."""
    class Exploding(MockTimelineItem):
        def GetCDL(self): raise RuntimeError("boom")

    timeline = MockTimeline([Exploding()])
    manifest_color = {
        "per_clip_adjustments": [
            {"source_file": "test.mov", "cdl_values": {"slope_r": 1.0}}
        ]
    }
    report = verify_color_grades(timeline, None, manifest_color)
    assert any("cdl_readback" in c.name for c in report.checks)


# ── Station 5: fusion comps ───────────────────────────────────────
# This station had `pass` as its only loop body and returned passed=True
# whatever the timeline held. These tests exist so it cannot go back.

FUSION_EFFECTS = {"per_clip": {"a_roll_0": {"glow_gain": 2.0}}}


def test_verify_fusion_comps_passes_when_the_comp_is_there():
    item = MockTimelineItem(comps=["Fusion Composition 1"])
    timeline = MockTimeline([], by_track={1: [item], 2: []})
    report = verify_fusion_comps(timeline, {1: ["a_roll_0"], 2: []}, FUSION_EFFECTS)
    assert report.passed
    assert report.checks == []


def test_verify_fusion_comps_fails_when_the_planned_comp_is_missing():
    item = MockTimelineItem(comps=[])
    timeline = MockTimeline([], by_track={1: [item], 2: []})
    report = verify_fusion_comps(timeline, {1: ["a_roll_0"], 2: []}, FUSION_EFFECTS)
    assert not report.passed
    assert any(c.name == "V1_clip_0_fusion_comp" for c in report.checks)
    # Nothing at all carried a comp: that is the collapse, said once.
    assert any(c.name == "fusion_comps_all_missing" for c in report.checks)


def test_verify_fusion_comps_covers_v2():
    v1 = MockTimelineItem(comps=["Fusion Composition 1"])
    v2 = MockTimelineItem(comps=[])
    timeline = MockTimeline([], by_track={1: [v1], 2: [v2]})
    effects = {"per_clip": {"a_roll_0": {}, "b_roll_0": {}}}
    report = verify_fusion_comps(
        timeline, {1: ["a_roll_0"], 2: ["b_roll_0"]}, effects)
    assert not report.passed
    assert any(c.name == "V2_clip_0_fusion_comp" for c in report.checks)
    # One of two carried a comp, so this is a miss, not a collapse.
    assert not any(c.name == "fusion_comps_all_missing" for c in report.checks)


def test_verify_fusion_comps_is_quiet_when_no_effect_was_planned():
    timeline = MockTimeline([], by_track={1: [MockTimelineItem(comps=[])]})
    report = verify_fusion_comps(timeline, {1: ["a_roll_0"]}, {"per_clip": {}})
    assert report.passed


def test_verify_fusion_comps_will_not_pass_without_labels():
    """No labels means it cannot check, and that is not a pass."""
    timeline = MockTimeline([], by_track={1: [MockTimelineItem(comps=[])]})
    report = verify_fusion_comps(timeline, None, FUSION_EFFECTS)
    assert not report.passed
    assert any(c.name == "fusion_comps_unverifiable" for c in report.checks)

def test_gap_detection_ignores_covered_gaps():
    # Gap between 30 and 32 on V1, but covered by V2 B-roll (25 to 35)
    item1 = MockTimelineItem(start=0, end=30)
    item2 = MockTimelineItem(start=32, end=60)
    
    broll = MockTimelineItem(start=25, end=35)
    
    timeline = MockTimeline(items=[], by_track={1: [item1, item2], 2: [broll]})
    
    # Needs a mock that handles both tracks properly for GetItemListInTrack
    # which we configured with by_track
    report = run_full_timeline_qa(timeline, None, {"project": {"frame_rate": 30, "duration_seconds": 2.0}})
    
    # Gap check should not fail now
    assert report.passed, "Gap should be ignored if covered by V2"
    assert not any("gap_before" in c.name for c in report.checks)
