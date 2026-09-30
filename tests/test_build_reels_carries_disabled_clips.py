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
    def __init__(self, name, path, start, enabled, duration=48):
        self.name = name
        self.path = path
        self.start = start
        self.enabled = enabled
        self.duration = duration

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + self.duration

    def GetDuration(self):
        return self.duration

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
    def __init__(self, name, clip, semantic_index=1):
        self.name = name
        clips = clip if isinstance(clip, list) else [clip]
        video = [(f"Video {index}", [])
                 for index in range(1, semantic_index)]
        video.append(("Semantic", clips))
        self.rows = {"video": video, "audio": []}

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


def _saved_semantic_graphic_record(reel, segment_id, element, display):
    """The saved Reel 24 props: same copy, regenerated element type."""
    return {
        "reel": reel,
        "basis": "planned",
        "segments": [{
            "segment_id": segment_id,
            "overlay_path": f"/fixture/{segment_id}.mov",
            "carry_identity": [{
                "element": element,
                "runs": [{"text": display, "type_role": "display"}],
                "asset": "",
                "data": {},
            }],
        }],
    }


def _rerendered_title(copy, index, *, rebuilt):
    """A saved Semantic props row before or after a rerender.

    These are synthetic values with the same props differences observed
    between Reel 15's retired and staging renders: theme provenance and a
    default layer/run flag appeared, and the third card's color and anchor
    changed. Type, copy and semantic slot remain the same.
    """
    text = list(copy) if isinstance(copy, (list, tuple)) else [copy]
    element = {
        "element": "title_lockup",
        "runs": [{"text": line, "type_role": "display" if not index
                  else "supporting"} for line in text],
        "subject": f"subject {index}",
        "why": f"reason {index}",
        "anchor": "centre" if rebuilt and index == 2 else "top_centre",
        "color": "#FBF0B8" if rebuilt and index == 2 else "#aabbcc",
        "colorBasis": ("brand palette role 'text' of 'fixture'"
                       if rebuilt else "brand palette role 'text'"),
        "data": {},
        "asset": "",
        "entrance": "fade",
        "exit": "fade",
    }
    if rebuilt:
        element["layer"] = "above"
        for run in element["runs"]:
            run["uppercase"] = False
    return element


def _write_reel15_semantic_case(folder, final, staging, *, changed=None):
    from library.tools.reel_disabled_clip_carry import semantic_graphic_identity

    graphics = [
        ("mg_live_a", "mg_stage_a", ["Graphic A"], 0, 96),
        ("mg_live_b", "mg_stage_b",
         ["Graphic B top", "Graphic B bottom"], 389, 144),
        ("mg_live_c", "mg_stage_c", ["Graphic C"], 882, 96),
    ]
    props_dir = (Path(folder) / "pipeline_output" / "steps" /
                 "4_06_render_motion_graphics" / "motion_graphics")
    props_dir.mkdir(parents=True, exist_ok=True)
    old_segments, new_segments = [], []
    for index, (old_id, new_id, copy, start, duration) in enumerate(graphics):
        old_element = _rerendered_title(copy, index, rebuilt=False)
        new_copy = (["Changed copy", *copy[1:]]
                    if changed == "copy" and index == 0 else copy)
        new_element = _rerendered_title(new_copy, index, rebuilt=True)
        if changed == "element_type" and index == 0:
            new_element["element"] = "different_title"
        for segment_id, element in ((old_id, old_element),
                                    (new_id, new_element)):
            (props_dir / f"{segment_id}_props.json").write_text(
                json.dumps({"elements": [element]}), encoding="utf-8")
        old_segments.append({
            "segment_id": old_id,
            "overlay_path": str(props_dir / f"{old_id}.mov"),
            "timeline_start": start / 24,
            "timeline_end": (start + duration) / 24,
        })
        new_start = start + (1 if index == 1 else 0)
        new_segments.append({
            "segment_id": new_id,
            "overlay_path": str(props_dir / f"{new_id}.mov"),
            "timeline_start": new_start / 24,
            "timeline_end": (new_start + duration) / 24,
            "carry_identity": semantic_graphic_identity([new_element]),
        })
    review = Path(folder) / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "semantic_visual_plans.json").write_text(json.dumps({
        "format": "semantic_visual_plans/1",
        "plans": [
            {"reel": final, "basis": "planned", "segments": old_segments},
            {"reel": staging, "basis": "planned", "segments": new_segments},
        ],
    }), encoding="utf-8")
    return graphics


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
                       624 if new_display == "Launch plan" else 480,
                       enabled=True))
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
            "match_basis": "semantic graphic type and copy",
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


@pytest.mark.parametrize("staged_enabled,staged_display,should_promote", [
    (False, "Descriptions / Titles / Headers", True),
    (None, "Descriptions / Titles / Headers", True),
    (True, "Descriptions / Titles / Headers", True),
    (True, "Different copy", False),
])
def test_reel24_carries_type_change_and_refuses_different_copy(
        tmp_path, monkeypatch, stub_resolve_script,
        staged_enabled, staged_display, should_promote):
    """Replay the saved Reel 24 type change through build-reels offline."""
    from library.tools import reel_disabled_clip_carry as disabled_carry

    final = "Reel 24 - why-ai-trusts-youtube"
    staging = final + " (rebuild staging)"
    old_id = "mg_geo-podcast_2c578933"
    new_id = "mg_geo-podcast_e2da4fb8"
    display = "Descriptions / Titles / Headers"
    folder = _ready_project(tmp_path)
    write_proposal(proposal_path(folder), [ReelMoment(
        number=24, slug="why-ai-trusts-youtube", reason="fixture replay",
        timeline_start=10.0, timeline_end=40.0,
        approval=Approval.APPROVED)],
        {"derived_from": {"duration_seconds": 60.0}})

    original = _Timeline(
        final, _Clip(f"{old_id}.mov", f"/fixture/{old_id}.mov", 1256,
                     enabled=False, duration=144), semantic_index=5)
    staged_clip = ([] if staged_enabled is None else _Clip(
        f"{new_id}.mov", f"/fixture/{new_id}.mov", 1256,
        enabled=staged_enabled, duration=144))
    staged = _Timeline(staging, staged_clip, semantic_index=5)
    project = _Project([
        _Timeline("Fixture Timeline", _Clip("master", "/fixture/master.mov",
                                             0, True)),
        original, staged,
    ])
    review = Path(folder) / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "plan_provenance.json").write_text(json.dumps({
        "built_reels": [final, staging]}), encoding="utf-8")
    (review / "semantic_visual_plans.json").write_text(json.dumps({
        "format": "semantic_visual_plans/1",
        "plans": [
            _saved_semantic_graphic_record(
                final, old_id, "list_build", display),
            _saved_semantic_graphic_record(
                staging, new_id, "title_lockup", staged_display),
        ],
    }), encoding="utf-8")
    assert (disabled_carry.semantic_graphic_identity(
        [{"element": "list_build", "runs": [{"text": display}]}])
        != disabled_carry.semantic_graphic_identity(
            [{"element": "title_lockup", "runs": [{"text": display}]}]))

    promotion_results = []

    def offline_rebuild(project_folder, **_kwargs):
        result = reel_build.promote_staged_reels(
            project_folder, "Fixture Project", "Fixture Timeline",
            {final: staging}, organise=False,
            track_plans=no_a_roll_track_plans({final: staging}))
        promotion_results.append(result)
        return result

    monkeypatch.setattr(
        reel_build, "resolve_project_exactly", lambda *_args: project)
    execute = operations.Operation.execute

    def skip_model_followups(self, project_folder, scope=None, **overrides):
        if self.name in {"reel.ask", "reel.verify"}:
            return SimpleNamespace(
                refused=False, payload={}, status="completed")
        return execute(self, project_folder, scope, **overrides)

    monkeypatch.setattr(operations.Operation, "execute", skip_model_followups)
    monkeypatch.setattr(reel_build, "rebuild_reels_in_project", offline_rebuild)
    args = _args(folder)
    args.only_reel = [24]
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"):
        if should_promote:
            manage_project.cmd_build_reels(args)
        else:
            with pytest.raises(reel_build.ReelBuildError,
                               match="enabled item.*at frame 1256"):
                manage_project.cmd_build_reels(args)

    if should_promote:
        report = promotion_results[0]["replace_reports"][final]
        carry_report = report["disabled_clip_carry"]
        if staged_display == display and staged_enabled is not None:
            assert carry_report["carried"] == [{
                "row": "video:Semantic",
                "source_item": f"{old_id}.mov",
                "source_frame": 1256,
                "source_end": 1400,
                "source_enabled": False,
                "staged_item": f"{new_id}.mov",
                "staged_frame": 1256,
                "staged_enabled_before": staged_enabled,
                "staged_enabled_after": False,
                "match_basis": (
                    "semantic graphic copy with element type change"),
                "element_type_change": {
                    "retired": ["list_build"],
                    "staged": ["title_lockup"],
                },
            }]
            assert carry_report["unchanged_unmatched"] == []
            assert carry_report["safe_replacements"] == [{
                "row": "video:Semantic",
                "source_item": f"{old_id}.mov",
                "source_frame": 1256,
                "source_end": 1400,
                "replacement_item": f"{new_id}.mov",
                "replacement_frame": 1256,
                "reason": (
                    "same copy carried disabled across an element type change"),
            }]
            semantic_row = next(row for row in report["rows"]
                                if row["key"] == "video:Semantic")
            assert semantic_row["enabled_changes"][0][
                "element_type_change"] == {
                    "retired": ["list_build"],
                    "staged": ["title_lockup"],
                }
            assert semantic_row["carried_disabled_replacements"] == [{
                "row": "video:Semantic",
                "source_item": f"{old_id}.mov",
                "source_frame": 1256,
                "source_end": 1400,
                "replacement_item": f"{new_id}.mov",
                "replacement_frame": 1256,
                "reason": (
                    "same copy carried disabled across an element type change"),
            }]
        else:
            assert carry_report["carried"] == []
            assert carry_report["safe_replacements"] == []
            assert carry_report["unchanged_unmatched"] == [{
                "row": "video:Semantic",
                "source_item": f"{old_id}.mov",
                "source_frame": 1256,
                "source_end": 1400,
                "staged_items": ([{
                    "name": f"{new_id}.mov",
                    "start": 1256,
                    "end": 1400,
                    "enabled": False,
                }] if staged_enabled is False else []),
                "reason": ("staging has no enabled graphic at this place"
                           if staged_enabled is False
                           else "staging has no graphic at this place"),
            }]
        promoted = next(t for t in project.timelines
                        if t.GetName() == final)
        assert all(clip.GetClipEnabled() is False for clip in
                   promoted.GetItemListInTrack("video", 5))
    else:
        assert not promotion_results
        assert original in project.timelines
        assert staged in project.timelines
        assert staged.GetItemListInTrack("video", 5)[0].GetClipEnabled() is True


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
    changed_type = [{"element": "caption",
                     "runs": [{"text": "Launch plan"}],
                     "anchor": "top_centre", "startFrame": 156,
                     "durationFrames": 48}]
    restyled = [{"element": "title", "runs": [{"text": "Launch plan"}],
                 "anchor": "centre", "color": "#fff", "layer": "above",
                 "startFrame": 156, "durationFrames": 48}]
    changed_asset = [{"element": "title",
                      "runs": [{"text": "Launch plan"}],
                      "asset": "different-logo.svg",
                      "anchor": "top_centre", "startFrame": 156,
                      "durationFrames": 48}]

    assert (semantic_graphic_identity(first)
            == semantic_graphic_identity(shifted))
    assert semantic_graphic_identity(first) != semantic_graphic_identity(changed)
    assert (semantic_graphic_identity(first)
            != semantic_graphic_identity(changed_type))
    assert semantic_graphic_identity(first) == semantic_graphic_identity(restyled)
    assert (semantic_graphic_identity(first)
            != semantic_graphic_identity(changed_asset))


@pytest.mark.parametrize("changed,should_promote", [
    (None, True), ("copy", False), ("element_type", True),
])
def test_build_reels_carries_reel15_rerenders_and_refuses_changes(
        tmp_path, monkeypatch, stub_resolve_script, changed, should_promote):
    """Same-copy rerenders or type changes carry; changed copy blocks."""
    case_final = "Reel 15 - the-3d-nail-art-salon-beats-the-chains"
    case_staging = case_final + " (rebuild staging)"
    folder = _ready_project(tmp_path)
    graphics = _write_reel15_semantic_case(
        folder, case_final, case_staging, changed=changed)
    old_clips, new_clips = [], []
    for index, (old_id, new_id, _copy, start, duration) in enumerate(graphics):
        old_clips.append(_Clip(
            f"{old_id}.mov", f"/fixture/{old_id}.mov", start, False,
            duration))
        stage_start = start + (1 if index == 1 else 0)
        new_clips.append(_Clip(
            f"{new_id}.mov", f"/fixture/{new_id}.mov", stage_start, True,
            duration))
    original = _Timeline(case_final, old_clips, semantic_index=5)
    staged = _Timeline(case_staging, new_clips, semantic_index=5)
    project = _Project([
        _Timeline("Fixture Timeline", _Clip("master", "/fixture/master.mov",
                                             0, True)),
        original, staged,
    ])
    review = Path(folder) / "pipeline_output" / "review"
    (review / "plan_provenance.json").write_text(json.dumps({
        "built_reels": [case_final, case_staging]}), encoding="utf-8")

    promotion_results = []

    def offline_rebuild(project_folder, **_kwargs):
        result = reel_build.promote_staged_reels(
            project_folder, "Fixture Project", "Fixture Timeline",
            {case_final: case_staging}, organise=False,
            track_plans=no_a_roll_track_plans({case_final: case_staging}))
        promotion_results.append(result)
        return result

    monkeypatch.setattr(
        reel_build, "resolve_project_exactly", lambda *_args: project)
    execute = operations.Operation.execute

    def skip_model_followups(self, project_folder, scope=None, **overrides):
        if self.name in {"reel.ask", "reel.verify"}:
            return SimpleNamespace(
                refused=False, payload={}, status="completed")
        return execute(self, project_folder, scope, **overrides)

    monkeypatch.setattr(operations.Operation, "execute", skip_model_followups)
    monkeypatch.setattr(reel_build, "rebuild_reels_in_project", offline_rebuild)
    args = _args(folder)
    args.only_reel = [15]
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"):
        if should_promote:
            manage_project.cmd_build_reels(args)
        else:
            with pytest.raises(reel_build.ReelBuildError,
                               match="mg_live_a.mov"):
                manage_project.cmd_build_reels(args)

    if should_promote:
        report = promotion_results[0]["replace_reports"][case_final]
        carried = report["disabled_clip_carry"]["carried"]
        assert len(carried) == 3
        assert [entry["staged_item"] for entry in carried] == [
            "mg_stage_a.mov", "mg_stage_b.mov", "mg_stage_c.mov"]
        promoted = next(t for t in project.timelines
                        if t.GetName() == case_final)
        assert [clip.GetClipEnabled() for clip in
                promoted.GetItemListInTrack("video", 5)] == [False] * 3
        assert original not in project.timelines
    else:
        assert not promotion_results
        assert original in project.timelines
        assert staged in project.timelines
        assert [clip.GetClipEnabled() for clip in new_clips] == [True] * 3
