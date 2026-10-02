from library.tools.timeline_qa import (
    verify_transitions,
    verify_color_grades,
    verify_fusion_comps,
    run_full_timeline_qa
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


def test_full_sweep_fails_a_gap_or_overlap_unless_v2_covers_it():
    project = {"project": {"frame_rate": 30, "duration_seconds": 2.0}}
    # Gap between 30 and 32.
    timeline = MockTimeline([MockTimelineItem(start=0, end=30),
                             MockTimelineItem(start=32, end=60)])
    report = run_full_timeline_qa(timeline, None, project)
    assert not report.passed
    assert any("gap_before" in c.name for c in report.checks)
    # Overlap between 25 and 30.
    timeline = MockTimeline([MockTimelineItem(start=0, end=30),
                             MockTimelineItem(start=25, end=60)])
    report = run_full_timeline_qa(timeline, None, project)
    assert not report.passed
    assert any("overlap_before" in c.name for c in report.checks)
    # The same V1 gap covered by V2 b-roll (25 to 35) is no gap.
    timeline = MockTimeline(items=[], by_track={
        1: [MockTimelineItem(start=0, end=30),
            MockTimelineItem(start=32, end=60)],
        2: [MockTimelineItem(start=25, end=35)]})
    report = run_full_timeline_qa(timeline, None, project)
    assert report.passed, "Gap should be ignored if covered by V2"
    assert not any("gap_before" in c.name for c in report.checks)


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


def test_verify_fusion_comps_reads_every_planned_comp():
    """Passes when the comp is there or no effect was planned; fails a
    missing comp on V1 (and says the collapse once when nothing carried
    one) or V2 (a miss, not a collapse); and no labels means it cannot
    check, which is not a pass."""
    def check(by_track, labels, effects=FUSION_EFFECTS):
        timeline = MockTimeline([], by_track=by_track)
        report = verify_fusion_comps(timeline, labels, effects)
        return report.passed, {c.name for c in report.checks}

    assert check({1: [MockTimelineItem(comps=["Fusion Composition 1"])],
                  2: []}, {1: ["a_roll_0"], 2: []}) == (True, set())
    assert check({1: [MockTimelineItem(comps=[])]}, {1: ["a_roll_0"]},
                 {"per_clip": {}})[0] is True
    passed, names = check({1: [MockTimelineItem(comps=[])], 2: []},
                          {1: ["a_roll_0"], 2: []})
    assert not passed
    assert {"V1_clip_0_fusion_comp", "fusion_comps_all_missing"} <= names
    passed, names = check(
        {1: [MockTimelineItem(comps=["Fusion Composition 1"])],
         2: [MockTimelineItem(comps=[])]},
        {1: ["a_roll_0"], 2: ["b_roll_0"]},
        {"per_clip": {"a_roll_0": {}, "b_roll_0": {}}})
    assert not passed
    assert "V2_clip_0_fusion_comp" in names
    assert "fusion_comps_all_missing" not in names
    passed, names = check({1: [MockTimelineItem(comps=[])]}, None)
    assert not passed
    assert "fusion_comps_unverifiable" in names


def test_verify_transitions_end_of_piece_fade_needs_no_head():
    # TR3.1's 1 s dip to black out of the final shot: the build drew the
    # tail on the last V1 clip, and the station failed the render looking
    # for a head on a clip index past the end of the track.
    last = MockTimelineItem(comps=["Fusion Composition 1"])
    timeline = MockTimeline([], by_track={1: [MockTimelineItem(), last]})
    end_fade = [{"after_clip": 1, "at_end": True, "type": "fade_to_black",
                 "duration_frames": 30}]
    report = verify_transitions(timeline, None, end_fade)
    assert report.passed, [c.name for c in report.checks]
    # Mid-piece, the head is still required.
    first = MockTimelineItem(comps=["Fusion Composition 1"])
    timeline = MockTimeline([], by_track={1: [first, MockTimelineItem()]})
    report = verify_transitions(
        timeline, None, [{"after_clip": 0, "type": "fade_to_black"}])
    assert not report.passed
    assert [c.name for c in report.checks] == [
        "transition_fade_to_black_head_on_v1[1]"]
