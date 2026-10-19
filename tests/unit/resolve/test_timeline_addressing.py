from types import SimpleNamespace

import pytest

from library.tools.timeline_addressing import (
    AmbiguousTarget,
    StaleTarget,
    TargetNotFound,
    resolve_item,
    resolve_timeline,
    verify_item,
    verify_project,
)


class Timeline:
    def __init__(self, name, uid, rows=None):
        self.name = name
        self.uid = uid
        self.rows = rows or {}

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def GetTrackCount(self, media_type):
        return len(self.rows.get(media_type, {}))

    def GetItemListInTrack(self, media_type, index):
        return self.rows[media_type][index]


class Project:
    def __init__(self, name, timelines):
        self.name = name
        self.timelines = timelines

    def GetName(self):
        return self.name

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]


def test_timeline_lookup_uses_persistent_id_after_rename():
    timeline = Timeline("Reel 26 - renamed", "timeline-26")
    project = Project("Exact Project", [timeline])

    assert resolve_timeline(project, project_name="Exact Project",
                            timeline_id="timeline-26",
                            timeline_name="Reel 26 - old") is timeline


def test_timeline_lookup_refuses_nonexact_or_ambiguous_identity():
    project = Project("Exact Project", [Timeline("Reel 2", "a"),
                                         Timeline("Reel 20", "b")])
    with pytest.raises(TargetNotFound):
        resolve_timeline(project, project_name="Exact Project",
                         timeline_name="Reel 2 ")
    project.timelines.append(Timeline("Reel 2", "c"))
    with pytest.raises(AmbiguousTarget):
        resolve_timeline(project, project_name="Exact Project",
                         timeline_name="Reel 2")


def test_project_name_is_an_exact_precondition():
    with pytest.raises(StaleTarget, match="expected exact project"):
        verify_project(Project("Project copy", []), "Project")


def test_semantic_item_lookup_refuses_ambiguous_source_ranges():
    def item(uid):
        pool = SimpleNamespace(GetClipProperty=lambda _key: "/media/a.mov")
        return SimpleNamespace(
            GetUniqueId=lambda: uid,
            GetMediaPoolItem=lambda: pool,
            GetSourceStartFrame=lambda: 10,
            GetSourceEndFrame=lambda: 20,
        )

    timeline = Timeline("Reel", "timeline", {"video": {
        1: [item("first")], 2: [item("second")],
    }})
    with pytest.raises(AmbiguousTarget):
        resolve_item(timeline, {
            "source_file": "/media/a.mov",
            "source_in_frame": 10,
            "source_out_frame": 20,
            "track_type": "video",
        })


def test_item_handle_is_checked_against_timeline_identity():
    item = SimpleNamespace(GetUniqueId=lambda: "clip-1")
    address = SimpleNamespace(timeline_id="timeline-1",
                              resolve_item_id="clip-1", item=item)
    with pytest.raises(StaleTarget, match="belongs to timeline"):
        verify_item(Timeline("Reel", "timeline-2"), address)


def test_live_track_role_is_relocated_by_name_after_index_shift():
    from library.tools.timeline_layout import (
        A_ROLL,
        VIDEO,
        TrackPlan,
        TrackSpec,
        resolve_live_track,
    )

    plan = TrackPlan(video_tracks=[TrackSpec(
        index=1, media_type=VIDEO, role=A_ROLL, name="A-Roll", occupant="main")])
    timeline = SimpleNamespace(
        GetTrackCount=lambda _kind: 2,
        GetTrackName=lambda _kind, index: {1: "B-Roll", 2: "A-Roll"}[index],
    )
    assert resolve_live_track(timeline, plan, A_ROLL, VIDEO, "main") == 2
