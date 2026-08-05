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
    def __init__(self, start=0, end=30, mpi=None):
        self.start = start
        self.end = end
        self.mpi = mpi or MockMediaPoolItem()
        self.name = self.mpi.name
        
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start
    def GetMediaPoolItem(self): return self.mpi
    def GetName(self): return self.name
    def GetCDL(self): return {"Slope": "1.0 1.0 1.0"}

class MockTimeline:
    def __init__(self, items):
        self.items = items
    def GetItemListInTrack(self, t_type, index):
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
