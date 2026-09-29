"""A `build-reels` rebuild preserves a captain-disabled Semantic graphic.

This drives the public command through its process runner and operation
registry. Resolve placement is replaced with an offline staging fixture, but
the real promotion code reads the live final, judges the staging, and replaces
it. The fixture shifts the opening frame and changes the generated filename,
the two things that make a timeline-position or filename match insufficient.
"""
import json
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import manage_project
from library.tools import operations, reel_build
from library.tools import requirements as _R
from library.tools.reel_proposal import (
    Approval,
    ReelMoment,
    proposal_path,
    write_proposal,
)
from library.tools.timeline_transcript import transcript_path
from tests.promotion_test_helpers import no_a_roll_track_plans

FINAL = "Reel 01 - a-witness"
STAGING = FINAL + " (rebuild staging)"
OLD_SEGMENT = "mg_old-rendered-name"
NEW_SEGMENT = "mg_new-rendered-name"


class _Media:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, name):
        return self.path if name == "File Path" else ""

    def GetMediaId(self):
        return Path(self.path).stem


class _Clip:
    def __init__(self, name, path, start, enabled):
        self.name = name
        self.path = path
        self.start = start
        self.enabled = enabled

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + 48

    def GetDuration(self):
        return 48

    def GetMediaPoolItem(self):
        return _Media(self.path)

    def GetClipEnabled(self):
        return self.enabled

    def SetClipEnabled(self, enabled):
        self.enabled = bool(enabled)
        return True

    def GetMarkers(self):
        return {}

    def GetUniqueId(self):
        return self.name


class _Timeline:
    def __init__(self, name, clip):
        self.name = name
        self.rows = {"video": [("Semantic", [clip])], "audio": []}

    def GetName(self):
        return self.name

    def SetName(self, name):
        self.name = name
        return True

    def GetTrackCount(self, kind):
        return len(self.rows[kind])

    def GetTrackName(self, kind, index):
        return self.rows[kind][index - 1][0]

    def GetItemListInTrack(self, kind, index):
        return self.rows[kind][index - 1][1]

    def GetStartFrame(self):
        return 0

    def GetMarkers(self):
        return {}

    def AddMarker(self, *_args):
        return True

    def GetUniqueId(self):
        return self.name


class _Pool:
    def __init__(self, project):
        self.project = project

    def DeleteTimelines(self, timelines):
        for timeline in timelines:
            self.project.timelines.remove(timeline)
        return True


class _Project:
    def __init__(self, timelines):
        self.timelines = list(timelines)
        self.pool = _Pool(self)

    def GetName(self):
        return "Fixture Project"

    def GetMediaPool(self):
        return self.pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def GetCurrentTimeline(self):
        return None


def _ready_project(root: Path) -> str:
    (root / "pipeline_output").mkdir(parents=True, exist_ok=True)
    state = {"project_folder": str(root), "step_outputs": {},
             _R._FORCE: {"resolve_scripting": True,
                         "face_detector": True,
                         "reel_build_libraries": True}}
    (root / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    transcript = transcript_path(root)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({
        "segments": [], "segment_count": 0,
        "derived_from": {"duration_seconds": 60.0}}), encoding="utf-8")
    moment = ReelMoment(
        number=1, slug="a-witness", reason="a moment to build",
        timeline_start=10.0, timeline_end=40.0,
        approval=Approval.APPROVED)
    write_proposal(proposal_path(root), [moment],
                   {"derived_from": {"duration_seconds": 60.0}})
    (root / "project.yaml").write_text(
        "name: fixture\nresolve:\n"
        "  project_name: Fixture Project\n"
        "  timeline_name: Fixture Timeline\n", encoding="utf-8")
    return str(root)


def _args(project):
    return types.SimpleNamespace(
        project=project, skip_captions=True, only_reel=[1],
        name_suffix="", allow_drop=[], supersede=[], retain=[],
        rebuild_all=True)


def _semantic_record(reel, segment_id, display="Launch plan"):
    return {
        "reel": reel,
        "basis": "planned",
        "entries": [{
            "element": "title",
            "subject": "the launch plan",
            "anchor_phrase": "the launch plan",
            "copy": {"display": display},
            "anchor": "top_centre",
            "why": "identify the subject of this passage",
        }],
        "segments": [{
            "segment_id": segment_id,
            "overlay_path": f"/fixture/{segment_id}.mov",
            "carry_identity": [
                {"element": "title", "subject": "the launch plan",
                 "anchor_phrase": "the launch plan",
                 "copy": {"display": display},
                 "anchor": "top_centre",
                 "why": "identify the subject of this passage"}],
        }],
    }


@pytest.mark.parametrize("new_display,should_promote", [
    ("Launch plan", True),
    ("A different message", False),
])
def test_build_reels_carries_or_refuses_disabled_semantic_graphic(
        tmp_path, monkeypatch, stub_resolve_script,
        new_display, should_promote):
    """A shifted rerender carries a match; changed content refuses."""
    folder = _ready_project(tmp_path)
    original = _Timeline(
        FINAL, _Clip("old-render-name", f"/fixture/{OLD_SEGMENT}.mov", 480,
                     enabled=False))
    staged = _Timeline(
        STAGING, _Clip("new-render-name", f"/fixture/{NEW_SEGMENT}.mov",
                       624, enabled=True))
    project = _Project([
        _Timeline("Fixture Timeline", _Clip("master", "/fixture/master.mov",
                                             0, True)),
        original, staged,
    ])
    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "plan_provenance.json").write_text(
        json.dumps({"built_reels": [FINAL, STAGING]}), encoding="utf-8")
    (review / "semantic_visual_plans.json").write_text(json.dumps({
        "format": "semantic_visual_plans/1",
        "plans": [_semantic_record(FINAL, OLD_SEGMENT),
                  _semantic_record(STAGING, NEW_SEGMENT, new_display)],
    }), encoding="utf-8")

    def offline_rebuild(project_folder, **_kwargs):
        return reel_build.promote_staged_reels(
            project_folder, "Fixture Project", "Fixture Timeline",
            {FINAL: STAGING}, organise=False,
            track_plans=no_a_roll_track_plans({FINAL: STAGING}))

    monkeypatch.setattr(
        reel_build, "resolve_project_exactly", lambda *_args: project)
    execute = operations.Operation.execute

    def skip_model_followups(self, project_folder, scope=None, **overrides):
        if self.name in {"reel.ask", "reel.verify"}:
            return SimpleNamespace(
                refused=False, payload={}, status="completed")
        return execute(self, project_folder, scope, **overrides)

    monkeypatch.setattr(operations.Operation, "execute", skip_model_followups)

    promotion_results = []

    def capture_result(project_folder, **kwargs):
        result = offline_rebuild(project_folder, **kwargs)
        promotion_results.append(result)
        return result

    monkeypatch.setattr(reel_build, "rebuild_reels_in_project", capture_result)
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"):
        if should_promote:
            manage_project.cmd_build_reels(_args(folder))
        else:
            with pytest.raises(reel_build.ReelBuildError,
                               match="old-render-name"):
                manage_project.cmd_build_reels(_args(folder))

    if should_promote:
        report = promotion_results[0]["replace_reports"][FINAL]
        semantic_row = next(row for row in report["rows"]
                            if row["key"] == "video:Semantic")
        assert semantic_row["enabled_changes"] == [{
            "name": "old-render-name", "start": 480, "end": 528,
            "retired_enabled": False, "incoming_enabled": True,
            "staged_item": "new-render-name", "staged_start": 624,
            "staged_enabled_after": False,
            "match_basis": "semantic graphic content and intent",
        }]
        promoted = next(t for t in project.timelines
                        if t.GetName() == FINAL)
        clip = promoted.GetItemListInTrack("video", 1)[0]
        assert clip.GetClipEnabled() is False
        assert staged in project.timelines
        assert staged.GetName() == FINAL
        assert original not in project.timelines
    else:
        assert original in project.timelines
        assert staged in project.timelines
        original_clip = original.GetItemListInTrack("video", 1)[0]
        assert original_clip.GetClipEnabled() is False
        assert staged.GetItemListInTrack("video", 1)[0].GetClipEnabled() is True


def test_semantic_identity_ignores_timing_but_keeps_content():
    from library.tools.reel_disabled_clip_carry import semantic_graphic_identity

    first = [{"element": "title", "runs": [{"text": "Launch plan"}],
              "anchor": "top_centre", "startFrame": 12,
              "durationFrames": 48}]
    shifted = [{"element": "title", "runs": [{"text": "Launch plan"}],
                "anchor": "top_centre", "startFrame": 156,
                "durationFrames": 48}]
    changed = [{"element": "title", "runs": [{"text": "New message"}],
                "anchor": "top_centre", "startFrame": 156,
                "durationFrames": 48}]

    assert (semantic_graphic_identity(first)
            == semantic_graphic_identity(shifted))
    assert semantic_graphic_identity(first) != semantic_graphic_identity(changed)
