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
from library.tools import operations, reel_build, reel_replace_guard
from library.tools import requirements as _R
from library.tools.reel_proposal import (
    Approval,
    ReelMoment,
    proposal_path,
    write_proposal,
)
from library.tools.timeline_transcript import transcript_path
from tests.promotion_test_helpers import no_a_roll_track_plans

@pytest.fixture(autouse=True)
def fake_preservation_snapshots(monkeypatch):
    monkeypatch.setattr(
        reel_replace_guard, "full_timeline_snapshot",
        lambda timeline, _project, _folder=None: {
            "timeline": {"name": timeline.GetName(),
                         "unique_id": timeline.GetUniqueId(),
                         "settings": {}, "start_frame": 0,
                         "end_frame": 0},
            "items": [], "markers": []})


from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeTimelineItem,
    make_project,
)


def _clip_spec(name, path, start, enabled, duration=48):
    """One graphic placement: the pool item carries the File Path the
    carry matches on, the timeline item its span and enabled state."""
    pool = FakeMediaPoolItem(Path(path).name)
    pool.SetClipProperty("File Path", path)
    return (pool, name, start, enabled, duration)


def _reel_timeline(project, name, specs, semantic_index=1):
    """A reel timeline off the canonical double: `Video 1..N` rows with
    the `Semantic` row last, carrying the given placements."""
    timeline = project.GetMediaPool().CreateEmptyTimeline(name)
    for index in range(1, semantic_index):
        timeline.add_track("video", f"Video {index}")
    timeline.add_track("video", "Semantic")
    row = timeline.GetItemListInTrack("video", semantic_index)
    for pool, clip_name, start, enabled, duration in specs:
        row.append(FakeTimelineItem(
            clip_name, timeline, start=start, duration=duration,
            left_offset=start, pool_item=pool, enabled=enabled))
    return timeline


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


def _write_reel15_semantic_case(folder, final, staging):
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
        new_element = _rerendered_title(copy, index, rebuilt=True)
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

    project = make_project()
    _reel_timeline(project, "Fixture Timeline", [
        _clip_spec("master", "/fixture/master.mov", 0, True)])
    original = _reel_timeline(project, final, [
        _clip_spec(f"{old_id}.mov", f"/fixture/{old_id}.mov", 1256, False,
                   144)], semantic_index=5)
    staged = _reel_timeline(
        project, staging,
        [] if staged_enabled is None else [
            _clip_spec(f"{new_id}.mov", f"/fixture/{new_id}.mov", 1256,
                       staged_enabled, 144)],
        semantic_index=5)
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


def test_semantic_identity_ignores_rationale_and_color_provenance():
    from library.tools.reel_disabled_clip_carry import semantic_graphic_identity

    old_accent = [{
        "element": "beat_accent", "anchor": "centre", "layer": "above",
        "row": 0, "runs": [], "color": "#aabbcc",
        "colorBasis": "brand palette role 'text' of 'cinematic_narrative'",
        "entrance": "cut", "exit": "fade", "asset": "", "footprint": 0.5,
        "subject": "fragment handoff ends, restated thesis begins",
        "why": "marks the speaker change without emphasizing a word",
        "data": {},
    }]
    rebuilt_accent = [{
        **old_accent[0],
        "subject": "question ends, audit story begins",
        "why": "marks the speaker change without emphasizing a spoken word",
    }]
    changed_color = [{**rebuilt_accent[0], "color": "#ffffff"}]

    old_title = [{
        "element": "lower_third", "runs": [
            {"text": "Akshita Gorti"}, {"text": "AI @ Lucie Content"}],
        "color": "#68AEE0",
        "data": {"speaker": "Akshita", "colour_basis":
                 'pipeline.speaker_subtitle_styles["Akshita"].accentColor'},
    }]
    rebuilt_title = [{
        **old_title[0],
        "data": {"speaker": "Akshita", "colour_basis":
                 "pipeline.speaker_subtitle_styles['Akshita'].accentColor"},
    }]
    different_speaker = [{
        **rebuilt_title[0],
        "data": {"speaker": "Craig", "colour_basis":
                 "pipeline.speaker_subtitle_styles['Akshita'].accentColor"},
    }]

    assert semantic_graphic_identity(old_accent) == semantic_graphic_identity(
        rebuilt_accent)
    assert semantic_graphic_identity(old_accent) != semantic_graphic_identity(
        changed_color)
    assert semantic_graphic_identity(old_title) == semantic_graphic_identity(
        rebuilt_title)
    assert semantic_graphic_identity(old_title) != semantic_graphic_identity(
        different_speaker)


def test_build_reels_carries_reel15_restyled_rerenders(
        tmp_path, monkeypatch, stub_resolve_script):
    """Same-copy rerenders carry across restyling and a one-frame shift.

    Changed copy refusing and a type change carrying are the Reel 24
    replay above, through the same `cmd_build_reels` path."""
    case_final = "Reel 15 - the-3d-nail-art-salon-beats-the-chains"
    case_staging = case_final + " (rebuild staging)"
    folder = _ready_project(tmp_path)
    graphics = _write_reel15_semantic_case(folder, case_final, case_staging)
    old_specs, new_specs = [], []
    for index, (old_id, new_id, _copy, start, duration) in enumerate(graphics):
        old_specs.append(_clip_spec(
            f"{old_id}.mov", f"/fixture/{old_id}.mov", start, False,
            duration))
        stage_start = start + (1 if index == 1 else 0)
        new_specs.append(_clip_spec(
            f"{new_id}.mov", f"/fixture/{new_id}.mov", stage_start, True,
            duration))
    project = make_project()
    _reel_timeline(project, "Fixture Timeline", [
        _clip_spec("master", "/fixture/master.mov", 0, True)])
    original = _reel_timeline(project, case_final, old_specs,
                              semantic_index=5)
    staged = _reel_timeline(project, case_staging, new_specs,
                            semantic_index=5)
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
        manage_project.cmd_build_reels(args)

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
    assert staged in project.timelines
