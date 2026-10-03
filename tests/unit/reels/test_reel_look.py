"""Tests for reel-look framing, motion mapping, and treatment limits.
"""
from __future__ import annotations
import json
import os
import sys
from dataclasses import dataclass
import pytest
from pathlib import Path
from library.tools.framing_intent import DEFAULT_FRAMING_INTENT, FILL, LETTERBOX
from library.tools.reel_conformance_verifier import (
    FindingClass,
    TimelineItem,
    check_delivered_framing,
)
from library.tools.reel_framing import (
    PIXEL,
    ReelFramingError,
    declared_picture,
    delivered_picture,
    disagreement,
    display_size,
    max_zoom,
)
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.transition_frames import parse_splines, value_at


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import reel_look, tv_frame
from library.tools.sub_block_anchor import AnchorRefused


@dataclass
class _Clip:
    source_file: str
    track_type: str = "video"
    timeline_start: float = 0.0
    source_in: float = 0.0


def _placement(source_file, record_frame, seconds, fps=24.0):
    return {
        "clip": _Clip(source_file),
        "source_in": 0.0,
        "source_out": seconds,
        "record": record_frame / fps,
        "snapped_record": record_frame,
        "speaker": "Craig",
    }


def _png(path, size, window=True):
    """A frame asset. `window=False` is a slate - opaque everywhere."""
    from PIL import Image
    image = Image.new("RGBA", size, (10, 10, 10, 255))
    if window:
        # A transparent window with an opaque rail either side, which is
        # the shape the captain's own asset measures (opaque columns
        # 0-723 and 3091-3840 of 3840, transparent between).
        inset = size[0] // 5
        for x in range(inset, size[0] - inset):
            for y in range(0, size[1], 4):
                image.putpixel((x, y), (0, 0, 0, 0))
    image.save(path)
    return str(path)


def _look(path):
    return {"asset": path, "punch_in": 2.3, "power": {},
            "origin": "test declaration"}


def test_a_landscape_frame_in_a_portrait_reel_is_cover_scaled(tmp_path):
    """The captain's own working configuration, and the number it derives.

    An earlier version of this refused a mismatched aspect outright. The
    captain disproved it by hand on 2026-09-09: same 3840x2160 asset,
    same 1080x1920 reel, zoom 3.16, and it framed. So a mismatched
    aspect is COVER-SCALED, and the zoom is derived from the two sizes -
    nothing in the engine holds 3.16.
    """
    asset = _png(tmp_path / "TV 4k.png", (3840, 2160), window=True)
    tv_frame.assert_frameable(_look(asset), 1080, 1920)
    zoom = tv_frame.cover_zoom((3840, 2160), 1080, 1920)
    assert zoom == pytest.approx(3.1605, abs=0.001)
    # The captain's own statement of the rule, for a wider-than-frame
    # asset, must give the same answer as the symmetric form above.
    assert zoom == pytest.approx((1920 / 1080) / (2160 / 3840), abs=1e-9)


def test_an_unframeable_asset_is_refused_by_name(tmp_path):
    """A cover that would upscale, or a frame with no window (a slate)."""
    small = _png(tmp_path / "small.png", (540, 960), window=True)
    with pytest.raises(ValueError) as excinfo:
        tv_frame.assert_frameable(_look(small), 1080, 1920)
    message = str(excinfo.value)
    assert "upscale" in message
    assert "540x960" in message and "1080x1920" in message

    slate = _png(tmp_path / "slate.png", (3840, 2160), window=False)
    with pytest.raises(ValueError) as excinfo:
        tv_frame.assert_frameable(_look(slate), 1080, 1920)
    assert "slate" in str(excinfo.value)


def test_sequential_placements_frame_as_one_run():
    fps = 24.0
    placements = [
        _placement("/a.mxf", 0, 5.0, fps),
        _placement("/b.mxf", 120, 5.0, fps),
    ]
    # One contiguous run, because the two abut exactly - read off the
    # placements as placed, with no collapse onto one row first.
    assert reel_look.frame_runs(placements, fps) == [(0, 240)]


def test_an_unanswered_motion_ask_is_not_an_empty_plan(tmp_path):
    resolved, record = reel_look.resolve_motion(None, {"structure": []}, 24.0)
    assert resolved == []
    assert record["basis"] == reel_look.MOTION_AWAITING_ANSWER
    resolved, record = reel_look.resolve_motion([], {"structure": []}, 24.0)
    assert record["basis"] == reel_look.MOTION_PLANNED_NONE


def test_motion_request_omits_a_word_ending_beyond_the_picture_shot(
        tmp_path):
    """A clipped final word is not offered as a motion anchor.

    Reel 22 ended its last picture at 49.341s while the transcript word
    "business." ended at 49.471s. The motion spine used to retain the
    overlapping word at its full duration, so the planner could select its
    end and the shared VFX resolver refused the build. Preserve the raw
    transcript for that resolver, but keep the ask from offering the edge.
    """
    from library.tools.sub_block_anchor import resolve_anchor

    fps = 24000 / 1001
    start_frame = round(10.636 * fps)
    placement = _placement("/m22.mxf", start_frame, 38.705, fps)
    placement["clip"] = _Clip(
        "/m22.mxf", timeline_start=0.0, source_in=0.0)
    transcript = [{
        "source_file": "/m22.mxf",
        "timeline_start": 0.0,
        "timeline_end": 38.835,
        "source_start": 0.0,
        "source_end": 38.835,
        "words": [{
            "word": "business.",
            "start": 38.375,
            "end": 38.835,
        }],
    }]

    spine = reel_look.motion_spine([placement], fps, transcript)
    block = spine["structure"][0]
    assert block["timeline_end"] == pytest.approx(49.341, abs=0.001)
    assert block["word_timestamps"] == [{
        "word": "business.",
        "source_start": 38.375,
        "source_end": 38.835,
    }]

    request_path = reel_look.write_motion_request(
        22, "Reel 22 - a-score-is-not-a-fix", spine,
        [{"timeline_start": 0.0, "timeline_end": 38.835,
          "text": "business."}], os.fspath(tmp_path))
    with open(request_path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    assert reel_look.expand_word_spans(
        request["context"]["shots"][0]["word_spans"]) == []

    with pytest.raises(ValueError, match="outside block 0"):
        resolve_anchor(
            {"word": "business.", "edge": "end"}, block=block,
            frame_rate=fps, step="plan_vfx", plan="vfx_creative",
            index=1, end="anchor_end")

    # A sub-frame overrun that rounds to the played end frame remains a
    # usable edge and is shown on that frame, matching the resolver rule.
    block["word_timestamps"] = [{
        "word": "edge", "source_start": 38.5, "source_end": 38.715,
    }]
    request_path = reel_look.write_motion_request(
        22, "Reel 22 - a-score-is-not-a-fix", spine,
        [{"timeline_start": 0.0, "timeline_end": 38.835,
          "text": "edge"}], os.fspath(tmp_path))
    with open(request_path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    span = reel_look.expand_word_spans(
        request["context"]["shots"][0]["word_spans"])[0]
    assert span["end"] == block["timeline_end"]


def test_a_reasoned_drift_resolves_and_reaches_the_manifest():
    fps = 24.0
    placements = [_placement("/a.mxf", 0, 10.0, fps)]
    spine = reel_look.motion_spine(placements, fps)
    resolved, record = reel_look.resolve_motion(
        [{"target_block_position": 0, "effect_type": "ken_burns",
          "params": {"zoom_start": 1.0, "zoom_end": 1.04},
          "rationale": "this shot narrows onto one figure"}],
        spine, fps)
    assert record["basis"] == reel_look.MOTION_PLANNED
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, resolved, fps)
    effects = manifest["fusion_effects"]["per_clip"][reel_look.clip_label(0)]
    assert effects["_preset"] == "slow_zoom_in"
    assert effects["zoom_end"] == 1.04
    # The switch animation rides the same clip, which is what the
    # comp builder reads both from.
    assert effects["tv_power_head"] is True
    assert manifest["tracks"]["V1"]["clips"][0]["source_file"] == "/a.mxf"


def test_existing_cta_motion_is_locked_by_its_span_not_the_last_shot(
        tmp_path):
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.fspath(tmp_path / "project")
    response_dir = str(ProjectLayout(project_folder).read_dir(
        Area.LLM_RESPONSES))
    os.makedirs(response_dir, exist_ok=True)
    cta_move = {
        "target_block_position": 1,
        "effect_type": "ken_burns",
        "params": {"zoom_start": 1.05, "zoom_end": 1.0},
        "rationale": "the existing CTA pull-back",
    }
    later_move = {
        "target_block_position": 2,
        "effect_type": "ken_burns",
        "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "rationale": "the later body shot",
    }
    placements = [
        _placement("/body.mxf", 0, 4.0),
        _placement("/cta.mxf", 96, 4.0),
        _placement("/tail.mxf", 192, 4.0),
    ]
    placements[1]["clip"] = _Clip("/cta.mxf", timeline_start=4.0)
    placements[2]["clip"] = _Clip("/tail.mxf", timeline_start=8.0)
    spine = reel_look.motion_spine(placements, 24.0)
    says = [{"timeline_start": 0.0, "timeline_end": 12.0,
             "text": "A body, then the CTA, then a trailing shot."}]
    call_to_action = {"timeline_start": 4.0, "timeline_end": 8.0}
    reel_look.write_motion_request(
        43, "Reel 43", spine, says, project_folder,
        call_to_action=call_to_action)
    response_path = os.path.join(response_dir, "reel_motion_43.json")
    with open(response_path, "w", encoding="utf-8") as handle:
        json.dump(reel_look.bind_motion_answer(
            project_folder, 43,
            {"reel_motion_plan": [cta_move, later_move]}), handle)
    request_path = reel_look.write_motion_request(
        43, "Reel 43", spine,
        says, project_folder, call_to_action=call_to_action)
    with open(request_path, "r", encoding="utf-8") as handle:
        request = json.load(handle)

    assert request["context"]["locked_closing_positions"] == [1]
    assert request["context"]["locked_closing_moves"] == [cta_move]

    changed_cta = dict(cta_move)
    changed_cta["params"] = {"zoom_start": 1.0, "zoom_end": 1.1}
    with open(response_path, "w", encoding="utf-8") as handle:
        json.dump({"reel_motion_plan": [changed_cta, later_move]}, handle)

    assert reel_look.read_motion_answer(project_folder, 43) == [
        later_move, cta_move]


def test_changed_motion_spine_invalidates_answer_and_offline_build_resolves(
        tmp_path):
    """A re-asked motion plan binds to its shot text and resolves pre-placement.

    The build calls `resolve_motion_for_build` before creating a timeline.
    This exercises that exact gate with no Resolve connection.
    """
    from library.tools import reel_build

    project_folder = os.fspath(tmp_path / "project")
    call_to_action = {"timeline_start": 20.0, "timeline_end": 21.0}

    def make_spine(repeated_you):
        placements = [_placement("/clip.mxf", 0, 8.0)]
        words = [
            {"word": "The", "start": 0.2, "end": 0.4},
            {"word": "point", "start": 0.6, "end": 0.9},
            {"word": "you.", "start": 1.2, "end": 1.4},
            {"word": "you.", "start": 2.2, "end": 2.4},
        ]
        if repeated_you == 3:
            words.append({"word": "you.", "start": 3.2, "end": 3.4})
        words.extend([
            {"word": "learn", "start": 5.8, "end": 6.2},
            {"word": "today.", "start": 7.0, "end": 7.3},
        ])
        return reel_look.motion_spine(
            placements, 24.0,
            [{"source_file": "/clip.mxf", "timeline_start": 0.0,
              "timeline_end": 8.0, "source_start": 0.0,
              "source_end": 8.0, "words": words}])

    old_spine = make_spine(3)
    old_text = "The point you. you. you. learn today."
    reel_look.write_motion_request(
        24, "Reel 24", old_spine,
        [{"timeline_start": 0.0, "timeline_end": 8.0,
          "text": old_text}], project_folder,
        call_to_action=call_to_action)
    old_plan = [{
        "target_block_position": 0,
        "anchor": {"word": "point"},
        "anchor_end": {"word": "learn", "edge": "end"},
        "effect_type": "ken_burns",
        "params": {"zoom_start": 1.0, "zoom_end": 1.02},
        "rationale": "the explanation opens into its conclusion",
    }]
    reel_build.write_visual_answers(project_folder, 24, {
        "reel_semantic": [],
        "reel_span": [],
        "reel_motion": {reel_look.MOTION_PLAN_KEY: old_plan},
    })
    assert reel_look.read_motion_answer(project_folder, 24) == old_plan

    new_spine = make_spine(2)
    new_text = "The point you. you. learn today."
    reel_look.write_motion_request(
        24, "Reel 24", new_spine,
        [{"timeline_start": 0.0, "timeline_end": 8.0,
          "text": new_text}], project_folder,
        call_to_action=call_to_action)
    response_path = (tmp_path / "project" / "pipeline_output"
                     / "llm_responses" / "reel_motion_24.json")
    assert not response_path.exists()

    fresh_plan = [{
        "target_block_position": 0,
        "anchor": {"word": "point"},
        "anchor_end": {"word": "learn", "edge": "end"},
        "effect_type": "ken_burns",
        "params": {"zoom_start": 1.0, "zoom_end": 1.018},
        "rationale": "the explanation relaxes as its conclusion lands",
    }]
    reel_build.write_visual_answers(project_folder, 24, {
        "reel_semantic": [],
        "reel_span": [],
        "reel_motion": {reel_look.MOTION_PLAN_KEY: fresh_plan},
    })
    resolved, record = reel_look.resolve_motion_for_build(
        project_folder, 24, new_spine, 24.0)

    assert record["basis"] == reel_look.MOTION_PLANNED
    assert record["resolved"] == 1
    assert record["dropped"] == []
    assert [item["effect_type"] for item in resolved] == ["slow_zoom_in"]


def test_compact_word_spans_keep_an_answer_bound_to_a_list_form_ask(
        tmp_path):
    """Asks written before the compact spelling carried word spans as a
    JSON list, and every answer on disk is fingerprinted against that
    list. Re-asking in the compact spelling must not read as a changed
    spine and delete those answers."""
    from library.tools import reel_build
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.fspath(tmp_path / "project")
    spine = reel_look.motion_spine(
        [_placement("/clip.mxf", 0, 8.0)], 24.0,
        [{"source_file": "/clip.mxf", "timeline_start": 0.0,
          "timeline_end": 8.0, "source_start": 0.0, "source_end": 8.0,
          "words": [{"word": "AI sees", "start": 0.2, "end": 0.6},
                    {"word": "a", "start": 0.6, "end": 0.7},
                    {"word": "point.", "start": 1.0, "end": 1.4}]}])
    says = [{"timeline_start": 0.0, "timeline_end": 8.0,
             "text": "AI sees a point."}]
    path = reel_look.write_motion_request(
        7, "Reel 07", spine, says, project_folder)
    with open(path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    compact = request["context"]["shots"][0]["word_spans"]
    assert compact == ("AI sees[0.200-0.600] a[0.600-0.700] "
                       "point.[1.000-1.400]")
    # The ask as the list-form writer left it, answered under that form.
    request["context"]["shots"][0]["word_spans"] = (
        reel_look.expand_word_spans(compact))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(request, handle)
    plan = [{"target_block_position": 0, "anchor": {"word": "AI sees"},
             "anchor_end": {"word": "point.", "edge": "end"},
             "effect_type": "ken_burns",
             "params": {"zoom_start": 1.0, "zoom_end": 1.02},
             "rationale": "the claim lands"}]
    reel_build.write_visual_answers(project_folder, 7, {
        "reel_semantic": [], "reel_span": [],
        "reel_motion": {reel_look.MOTION_PLAN_KEY: plan}})

    reel_look.write_motion_request(7, "Reel 07", spine, says, project_folder)

    assert os.path.isfile(os.path.join(str(ProjectLayout(
        project_folder).read_dir(Area.LLM_RESPONSES)), "reel_motion_07.json"))
    assert reel_look.read_motion_answer(project_folder, 7) == plan


def test_legacy_motion_answer_older_than_latest_ask_is_invalidated(
        tmp_path):
    from library.tools import reel_phase_log
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.fspath(tmp_path / "project")
    spine = reel_look.motion_spine([_placement("/clip.mxf", 0, 8.0)], 24.0)
    says = [{"timeline_start": 0.0, "timeline_end": 8.0,
             "text": "The same words are still here."}]
    reel_look.write_motion_request(25, "Reel 25", spine, says,
                                  project_folder)

    response_dir = str(ProjectLayout(project_folder).write_dir(
        Area.LLM_RESPONSES))
    response_path = os.path.join(response_dir, "reel_motion_25.json")
    with open(response_path, "w", encoding="utf-8") as handle:
        json.dump({reel_look.MOTION_PLAN_KEY: []}, handle)
    reel_phase_log.log_event(
        project_folder, 25, "Reel 25", reel_phase_log.PLAN_ASKED,
        detail="motion ask written: reel_motion_25.json")

    assert reel_look.read_motion_answer(project_folder, 25) is None
    assert not os.path.exists(response_path)


def test_two_anchored_body_moves_compose_inside_one_picture_cut():
    fps = 24.0
    placements = [_placement("/a.mxf", 0, 12.0, fps)]
    words = [
        {"word": "one", "start": 1.0, "end": 1.2},
        {"word": "bridge", "start": 5.5, "end": 5.7},
        {"word": "next", "start": 6.1, "end": 6.3},
        {"word": "idea", "start": 10.5, "end": 10.7},
    ]
    spine = reel_look.motion_spine(
        placements, fps, [{"source_file": "/a.mxf",
                           "timeline_start": 0.0,
                           "timeline_end": 12.0,
                           "source_start": 0.0,
                           "source_end": 12.0,
                           "words": words}])
    resolved, record = reel_look.resolve_motion([
        {"target_block_position": 0,
         "anchor": {"word": "one"},
         "anchor_end": {"word": "bridge", "edge": "end"},
         "effect_type": "ken_burns",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03},
         "rationale": "the explanation builds"},
        {"target_block_position": 0,
         "anchor": {"word": "next"},
         "anchor_end": {"word": "idea", "edge": "end"},
         "effect_type": "ken_burns",
         "params": {"zoom_start": 1.03, "zoom_end": 1.0},
         "rationale": "the speaker widens the conclusion"},
    ], spine, fps)
    assert record["resolved"] == 2
    assert [move["effect_type"] for move in record["moves"]] == [
        "slow_zoom_in", "slow_zoom_out"]

    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, resolved, fps)
    effects = manifest["fusion_effects"]["per_clip"][
        reel_look.clip_label(0)]
    assert effects["zoom_windows"] == [
        {"first_frame": 24, "last_frame": 136,
         "zoom_start": 1.0, "zoom_end": 1.03},
        {"first_frame": 146, "last_frame": 256,
         "zoom_start": 1.03, "zoom_end": 1.0},
    ]
    from library.tools.treatment_verify import verify_drift
    verdict = verify_drift(effects, 288, played_frames=288,
                           source_res=(3840, 2160))
    assert verdict["passed"] is True
    assert verdict["motion_over_time"] is True


def test_build_motion_resolves_a_word_end_on_the_picture_frame_edge():
    """The reel build sends its own frame span to plan_vfx.

    The saved Reel 21 motion answer ends a word at 9.430s. Its picture
    placement ends at frame 226 (9.426s) at 23.976 fps. Those timestamps
    resolve to the same frame, so the visual span ends at the played edge
    instead of refusing on the rounded seconds display.
    """
    fps = 24000 / 1001
    placement = _placement("/a.mxf", 0, 9.426, fps)
    words = [
        {"word": "They're", "start": 0.0, "end": 0.19},
        {"word": "like,", "start": 0.19, "end": 0.57},
        {"word": "everything", "start": 4.32, "end": 4.73},
        {"word": "I", "start": 3.51, "end": 3.63},
        {"word": "I", "start": 4.03, "end": 4.12},
        {"word": "I", "start": 4.73, "end": 4.81},
        {"word": "enough?", "start": 9.06, "end": 9.430},
    ]
    spine = reel_look.motion_spine(
        [placement], fps,
        [{"source_file": "/a.mxf", "timeline_start": 0.0,
          "timeline_end": 9.430, "source_start": 0.0,
          "source_end": 9.430, "words": words}])
    plan = [
        {"target_block_position": 0,
         "anchor": {"word": "like,"},
         "anchor_end": {"word": "everything", "edge": "end"},
         "effect_type": "ken_burns",
         "params": {"zoom_start": 1.0, "zoom_end": 1.025},
         "rationale": "the first explanation unfolds"},
        {"target_block_position": 0,
         "anchor": {"word": "I", "occurrence": 3},
         "anchor_end": {"word": "enough?", "edge": "end"},
         "effect_type": "ken_burns",
         "params": {"zoom_start": 1.025, "zoom_end": 1.0},
         "rationale": "the question lands"},
    ]

    assert spine["structure"][0]["timeline_end_frame"] == 226
    resolved, record = reel_look.resolve_motion(plan, spine, fps)

    assert record["resolved"] == 2
    assert resolved[1]["timeline_end"] == pytest.approx(9.426)
    assert "snapped to block end frame" in resolved[1]["anchor_method"]

    # One frame further is outside the picture shot, and refuses.
    next_frame_edge = 227 / fps
    spine = reel_look.motion_spine(
        [placement], fps,
        [{"source_file": "/a.mxf", "timeline_start": 0.0,
          "timeline_end": next_frame_edge, "source_start": 0.0,
          "source_end": next_frame_edge,
          "words": [{"word": "starts", "start": 4.0, "end": 4.2},
                    {"word": "outside", "start": 9.06,
                     "end": next_frame_edge}]}])
    plan = [{"target_block_position": 0,
             "anchor": {"word": "starts"},
             "anchor_end": {"word": "outside", "edge": "end"},
             "effect_type": "ken_burns",
             "params": {"zoom_start": 1.0, "zoom_end": 1.03},
             "rationale": "the move tracks the explanation"}]

    with pytest.raises(AnchorRefused, match="outside block 0"):
        reel_look.resolve_motion(plan, spine, fps)


def test_closing_cta_ken_burns_keeps_its_existing_full_shot_window():
    fps = 24.0
    placements = [
        _placement("/body.mxf", 0, 12.0, fps),
        _placement("/cta.mxf", 288, 4.0, fps),
    ]
    body = {
        "target_block_position": 0,
        "timeline_start": 1.0,
        "timeline_end": 5.0,
        "effect_type": "slow_zoom_in",
        "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "anchor_method": "word",
    }
    cta = {
        "target_block_position": 1,
        "timeline_start": 12.5,
        "timeline_end": 15.0,
        "effect_type": "slow_zoom_out",
        "params": {"zoom_start": 1.05, "zoom_end": 1.0},
        "anchor_method": "word",
    }
    manifest = reel_look.fusion_manifest(
        placements, {"power": {}}, [body, cta], fps,
        locked_closing_positions={1})
    effects = manifest["fusion_effects"]["per_clip"]
    closing = effects[reel_look.clip_label(1)]
    assert closing["_preset"] == "slow_zoom_out"
    assert closing["zoom_start"] == 1.05
    assert closing["zoom_end"] == 1.0
    assert "effect_window_frames" not in closing


def test_body_ken_burns_refuses_a_punch_sized_scale_change():
    """Anchored or whole-shot, a body move past 1.0-1.05 is refused."""
    fps = 24.0
    placement = _placement("/body.mxf", 0, 12.0, fps)
    punch = {
        "target_block_position": 0,
        "timeline_start": 1.0,
        "timeline_end": 5.0,
        "effect_type": "slow_zoom_in",
        "params": {"zoom_start": 1.0, "zoom_end": 1.12},
        "anchor_method": "word",
    }
    whole_shot, _ = reel_look.resolve_motion([
        {"target_block_position": 0,
         "effect_type": "ken_burns",
         "params": {"zoom_start": 1.0, "zoom_end": 1.07},
         "rationale": "the old whole-shot body push"},
    ], reel_look.motion_spine([placement], fps), fps)
    for moves in ([punch], whole_shot):
        with pytest.raises(reel_look.ReelLookRefused,
                           match="subtle 1.0-1.05 body scale"):
            reel_look.fusion_manifest(
                [placement], {"power": {}}, moves, fps)


def test_anchored_body_ken_burns_cannot_straddle_a_picture_cut():
    fps = 24.0
    placements = [
        _placement("/first.mxf", 0, 5.0, fps),
        _placement("/second.mxf", 120, 5.0, fps),
    ]
    move = {
        "target_block_position": 0,
        "timeline_start": 4.5,
        "timeline_end": 5.5,
        "effect_type": "slow_zoom_in",
        "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "anchor_method": "word",
    }
    with pytest.raises(reel_look.ReelLookRefused,
                       match="does not fit inside picture shot 0"):
        reel_look.fusion_manifest(
            placements, {"power": {}}, [move], fps)


def test_ken_burns_draw_gain_check_rejects_a_zoom_out_that_uncovers_the_frame():
    safe = {"ZoomX": 3.2, "ZoomY": 3.2, "Pan": 0.0, "Tilt": 0.0}
    move = [{"target_block_position": 0,
             "params": {"zoom_start": 1.0, "zoom_end": 1.03}}]
    window = (0.0, 0.0, 1080.0, 1920.0)
    proof = reel_look.assert_motion_covers_window(
        safe, move, (3840, 2160), 1080, 1920, window, draw_gain=1.0)
    assert proof["draw_gain"] == 1.0
    exposed = [{"target_block_position": 0,
                "params": {"zoom_start": 1.0, "zoom_end": 0.1}}]
    with pytest.raises(reel_look.PunchInLeavesBlack):
        reel_look.assert_motion_covers_window(
            safe, exposed, (3840, 2160), 1080, 1920, window,
            draw_gain=1.0)


class _FakeItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, key):
        return self._path if key == "File Path" else ""


class _FakeFolder:
    def __init__(self, clips=None, subs=None):
        self._clips = list(clips or [])
        self._subs = list(subs or [])

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _FakePool:
    """Counts imports, because the defect was invisible from the timeline."""

    def __init__(self, existing=()):
        self.root = _FakeFolder(subs=[_FakeFolder(
            [_FakeItem(p) for p in existing])])
        self.imports = []

    def GetRootFolder(self):
        return self.root

    def ImportMedia(self, paths):
        self.imports.append(list(paths))
        item = _FakeItem(paths[0])
        self.root.GetSubFolderList()[0]._clips.append(item)
        return [item]


def test_a_file_already_in_the_pool_is_not_imported_again():
    """The pool is asked BEFORE anything is imported.

    Measured 2026-09-09: the field-test project's "not placed on any
    timeline" bin held 96 copies of 12 motion-graphic files - eight of
    each, one per build attempt - because every build re-imported the
    overlays it had imported before. Nested folders are searched, since
    that is where a filed pool puts them.
    """
    from library.tools.reel_build import import_pool_item, pool_item_for

    pool = _FakePool(existing=["/x/vox_00.mov"])
    assert pool_item_for(pool, "/x/vox_00.mov") is not None
    assert pool_item_for(pool, "/x/absent.mov") is None

    found = import_pool_item(pool, "/x/vox_00.mov")
    assert found is not None
    assert pool.imports == [], "an item already in the pool was re-imported"

    fresh = import_pool_item(pool, "/x/vox_01.mov")
    assert fresh is not None
    assert pool.imports == [["/x/vox_01.mov"]]

    # And a second build of the same reel imports nothing at all.
    import_pool_item(pool, "/x/vox_01.mov")
    assert pool.imports == [["/x/vox_01.mov"]]


class _Subject:
    def __init__(self, cx, cy, others=0):
        self.center_x = cx
        self.center_y = cy
        self.width = 0.1
        self.samples = 12
        self.detected = 12
        self.others = others


def test_no_subject_means_no_punch_in():
    """The captain's ruling: refuse rather than guess.

    A centred 2.30 on 3840x2160 footage shows the middle 43% of the
    width, so a crop with nothing aiming it is a bet on where the
    speaker is standing. Returning None here is what makes the shot
    play uncropped instead.
    """
    assert reel_look.punch_in_properties(
        {"punch_in": 2.3}, None, 3840, 2160, 1080, 1920) is None


def test_the_punch_in_is_aimed_at_the_measured_subject():
    window = _window(90)
    props = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.30, 0.32), 3840, 2160, 1080, 1920,
        window=window)
    # A subject left of centre pulls the picture right, and vice versa.
    assert props["Pan"] > 0
    other = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.70, 0.32), 3840, 2160, 1080, 1920,
        window=window)
    assert other["Pan"] < 0


def test_the_aim_never_uncovers_the_screen_window():
    """Clamped to the WINDOW, which is what the viewer can see through.

    Clamping to the delivery frame would let the aim pull the picture off
    an edge of the screen while still covering the frame - black inside
    the television, which is the defect the post-condition now catches.
    """
    window = _window(90)
    far = reel_look.punch_in_properties(
        {"punch_in": 2.3}, _Subject(0.01, 0.99), 3840, 2160, 1080, 1920,
        window=window)
    reel_look.assert_covers_window(far, 3840, 2160, 1080, 1920, window)


def test_a_landscape_frame_is_turned_upright_for_a_portrait_delivery():
    """The captain's instruction, and what the turn buys.

    Turned, a 3840x2160 asset is 2160x3840 against a 1080x1920 reel -
    the same aspect - so it needs no cover zoom at all and downscales by
    half. Judged on the ORIENTED size, because judging the cover before
    the turn refuses an asset that fits perfectly after it.
    """
    look = {"rotate": tv_frame.AUTO_ROTATE}
    assert tv_frame.applied_rotation(look, (3840, 2160), 1080, 1920) == 90
    assert tv_frame.oriented_size((3840, 2160), 90) == (2160, 3840)
    assert tv_frame.cover_zoom((2160, 3840), 1080, 1920) == pytest.approx(1.0)


def test_a_partial_turn_is_refused():
    """A frame turns in quarters or not at all."""
    with pytest.raises(ValueError) as excinfo:
        tv_frame.validate_rotation(45, "test declaration")
    assert "quarters" in str(excinfo.value)
    with pytest.raises(TypeError):
        tv_frame.validate_rotation("sideways", "test declaration")


def _window(look_rotate, asset=(3840, 2160)):
    """The screen window of the captain's asset at a given rotation."""
    from unittest import mock
    look = {"asset": "TV 4k.png", "rotate": look_rotate}
    with mock.patch.object(tv_frame, "screen_window",
                           return_value=(519, 37, 3322, 2123)):
        return tv_frame.screen_window_rect(look, 1080, 1920, asset_size=asset)


def test_the_picture_covers_the_SCREEN_WINDOW_not_the_frame():
    """The rule the captain had to state twice.

    Covering the delivery frame and covering the television's screen are
    different targets. Turned upright the window is 1043x1402 timeline
    pixels and needs 2.3070; unturned it is 2491x1853 and needs 3.05.
    The difference between those is the whole of what rotating fixed.
    """
    turned = _window(90)
    assert (round(turned[2] - turned[0]), round(turned[3] - turned[1])) \
        == (1043, 1402)
    needed = tv_frame.window_cover_zoom(3840, 2160, turned, 1080, 1920)
    assert needed == pytest.approx(2.307, abs=0.001)

    flat = _window(0)
    assert tv_frame.window_cover_zoom(3840, 2160, flat, 1080, 1920) \
        == pytest.approx(3.05, abs=0.01)


def test_a_punch_in_leaving_black_in_the_screen_is_refused():
    """The post-condition, on the exact geometry that shipped.

    Unrotated and cover-scaled, the declared 2.30 leaves 228px of black
    above and below the picture INSIDE the television's screen. That
    reached the captain twice before anything measured it.
    """
    flat = _window(0)
    with pytest.raises(reel_look.PunchInLeavesBlack) as excinfo:
        reel_look.assert_covers_window(
            {"ZoomX": 2.3, "ZoomY": 2.3, "Pan": 0.0, "Tilt": 0.0},
            3840, 2160, 1080, 1920, flat)
    message = str(excinfo.value)
    assert "top 227" in message and "bottom 228" in message


def test_the_window_is_a_required_argument():
    """Covering the frame instead is the defect; it cannot be defaulted."""
    with pytest.raises(ValueError) as excinfo:
        reel_look.punch_in_properties(
            {"punch_in": 2.3}, _Subject(0.5, 0.32), 3840, 2160, 1080, 1920)
    assert "screen window" in str(excinfo.value)


def _upright_frame_project(tmp_path):
    """A project folder whose frame asset already matches the delivery.

    2160x3840 against a 1080x1920 reel, so no turn and no cover zoom:
    the render is small and the test measures identity, not geometry.
    """
    asset = _png(tmp_path / "TV 4k.png", (2160, 3840), window=True)
    look = {"asset": asset, "punch_in": 2.3, "power": {},
            "origin": "test declaration",
            "rotate": tv_frame.AUTO_ROTATE}
    return look


def _rendered_frames(path):
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=nb_frames", "-of", "csv=p=0",
         str(path)],
        capture_output=True, encoding="utf-8", check=True)
    return int(out.stdout.strip())


def _overlay_dir(project_folder):
    from library.tools.project_layout import Area, ProjectLayout
    return os.path.join(
        str(ProjectLayout(str(project_folder)).read_dir(Area.SCRATCH)),
        "reel_look", "frame_overlays")


def test_the_overlay_name_shape_matches_shared_and_legacy_renders():
    """The verifier reads live timelines that still carry per-length names.

    The shared render (`tv_frame_<stamp>`) must match, and the legacy
    per-length renders (`tv_frame_<stamp>_<N>f`) must keep matching
    until the captain re-points those timelines - a shape that dropped
    either would misread the set as an out-of-band overlay or miss it.
    """
    import re
    shape = re.compile(reel_look.FRAME_OVERLAY_NAME_SHAPE)
    assert shape.match("tv_frame_1f8e8d06ff")
    assert shape.match("tv_frame_1f8e8d06ff_950f")
    assert not shape.match("tv_frame_1f8e8d06ff_950f_tight")
    assert not shape.match("vox_00")

    from types import SimpleNamespace
    items = [
        SimpleNamespace(track_index=2,
                        source_file="/s/tv_frame_1f8e8d06ff.mov"),
        SimpleNamespace(track_index=2,
                        source_file="/s/tv_frame_1f8e8d06ff_950f.mov"),
        SimpleNamespace(track_index=1,
                        source_file="/s/tv_frame_1f8e8d06ff.mov"),
    ]
    found = reel_look.frame_overlay_items(items, {"punch_in": 2.3})
    assert len(found) == 2


def _reel_09_pictures():
    """Reel 09 at 24 fps, base and with its one-second Akshita cover
    over the Craig-to-Craig join: the cover splits Craig's shot and
    every later shot moves up the picture."""
    base = [_placement("/craig.mxf", 0, 5.5),
            _placement("/akshita.mxf", 132, 16.0),
            _placement("/craig.mxf", 516, 4.5),
            _placement("/akshita.mxf", 624, 33.0)]
    offset = [_placement("/craig.mxf", 0, 5.5),
              _placement("/akshita.mxf", 132, 16.0),
              _placement("/craig.mxf", 516, 1.0),
              _placement("/akshita.mxf", 540, 1.0),
              _placement("/craig.mxf", 564, 2.5),
              _placement("/akshita.mxf", 624, 33.0)]
    return base, offset


@pytest.mark.parametrize("case", [
    pytest.param("anchored-shot-follows-cutaway", id="remap-to-same-shot"),
    pytest.param("anchored-window-crosses-seam", id="refuse-split-window"),
    pytest.param("whole-shot-move-on-split-shot", id="refuse-split-lock"),
])
def test_motion_mapping_follows_the_shot_and_refuses_ambiguous_spans(case):
    """See `docs/evidence/variant_motion_remap.md` for the incident."""
    base, offset = _reel_09_pictures()

    if case == "anchored-shot-follows-cutaway":
        move = {"target_block_position": 3, "effect_type": "slow_zoom_in",
                "timeline_start": 27.1, "timeline_end": 30.97,
                "params": {"zoom_start": 1.0, "zoom_end": 1.025}}
        remapped, locked = reel_look.remap_motion_positions(
            [move], base, offset, 24.0, locked_closing_positions=[1])

        assert [entry["target_block_position"] for entry in remapped] == [5]
        assert remapped[0]["timeline_start"] == 27.1
        assert locked == [1]
    elif case == "anchored-window-crosses-seam":
        move = {"target_block_position": 2, "effect_type": "slow_zoom_in",
                "timeline_start": 21.6, "timeline_end": 24.5,
                "params": {"zoom_start": 1.0, "zoom_end": 1.02}}
        with pytest.raises(reel_look.ReelLookRefused, match="straddles"):
            reel_look.remap_motion_positions([move], base, offset, 24.0)
    else:
        with pytest.raises(reel_look.ReelLookRefused, match="2 piece"):
            reel_look.remap_motion_positions(
                [], base, offset, 24.0, locked_closing_positions=[2])


# --------------------------------------------------------------------------
# From test_reel_motion_reaches_every_row.py
#
# A treatment planned for a clip on ANY picture row must reach that clip.
#
# A two-angle reel built through `timeline_layout.plan_layout` draws every
# planned drift, on both rows. Reel 09's history: docs/evidence/reel_look.md.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.pipeline_skills import read_receipts
from library.tools.timeline_layout import plan_layout


class _Clip_2:
    def __init__(self, source_file, track_index, speaker):
        self.source_file = source_file
        self.track_type = "video"
        self.track_index = track_index
        self.timeline_start = 0.0
        self.source_in = 0.0
        self.speaker = speaker


def _placement_2(source_file, track_index, record_frame, speaker, fps=24.0):
    seconds = 5.0
    return {
        "clip": _Clip_2(source_file, track_index, speaker),
        "source_in": 0.0,
        "source_out": seconds,
        "record": record_frame / fps,
        "snapped_record": record_frame,
        "speaker": speaker,
    }


def _two_angle_plan():
    """The Reel 09 shape: Akshita on V1, Craig on V2, the set above."""
    return plan_layout({
        "angles": [
            {"key": "1", "label": "Akshita",
             "speech_name": "Akshita CH1", "program_channel": 1},
            {"key": "2", "label": "Craig",
             "speech_name": "Craig CH1", "program_channel": 1},
        ],
        "has_broll": False,
        "has_frame": True,
        "caption_spans": [],
        "has_transitions": False,
        "has_explainer": False,
        "has_semantic": False,
        "mg_spans": [],
        "has_generators": False,
        "timed_text_spans": [],
        "music_spans": [],
        "sfx_spans": [],
    })


def _angle_key(clip):
    return str(int(clip.track_index))


def _placements():
    # The Reel 09 arrangement: the outer shots ride Craig's row (V2),
    # the inner two Akshita's (V1).
    return [
        _placement_2("/tmp/cr0.mxf", 2, 0, "Craig"),
        _placement_2("/tmp/ak1.mxf", 1, 120, "Akshita"),
        _placement_2("/tmp/ak2.mxf", 1, 240, "Akshita"),
        _placement_2("/tmp/cr3.mxf", 2, 360, "Craig"),
    ]


def _motion(count=4):
    return [{
        "target_block_position": i,
        "effect_type": "slow_zoom_in",
        "params": {"zoom_start": 1.0, "zoom_mid": 1.02,
                   "zoom_end": 1.04},
    } for i in range(count)]


def _manifest():
    return reel_look.fusion_manifest(
        _placements(), {"power": {}}, _motion(), 24.0,
        track_plan=_two_angle_plan().serializable(),
        angle_key=_angle_key)


def test_manifest_groups_clips_by_the_plan_rows():
    manifest = _manifest()
    labels = {row: [c["label"] for c in spec["clips"]]
              for row, spec in manifest["tracks"].items()}
    assert labels == {
        "V1": [reel_look.clip_label(1), reel_look.clip_label(2)],
        "V2": [reel_look.clip_label(0), reel_look.clip_label(3)],
    }


# ── The pass itself, driven against a fake Resolve ────────────────────

class _FakeMediaPoolItem:
    def __init__(self, path, frames=600, fps="24", resolution="1080x1920"):
        self._props = {
            "File Path": path, "Frames": str(frames),
            "FPS": fps, "Resolution": resolution,
        }

    def GetClipProperty(self, key=None):
        return self._props if key is None else self._props.get(key, "")


class _FakeComp:
    def __init__(self):
        self.locked = False

    def Lock(self):
        self.locked = True

    def Unlock(self):
        self.locked = False

    def GetToolList(self):
        class _Tool:
            def __init__(self, regid):
                self._regid = regid

            def GetAttrs(self):
                return {"TOOLS_RegID": self._regid}

            def Delete(self):
                return True
        return {1: _Tool("MediaIn"), 2: _Tool("Merge"), 3: _Tool("MediaOut")}

    def AddTool(self, _name):
        assert self.locked, "Fusion node creation must hold comp.Lock()"
        class _Dummy:
            def Delete(self):
                return True
        return _Dummy()

    def FindTool(self, _name):
        return None


class _FakeTimelineItem:
    def __init__(self, path, start, end):
        self.mpi = _FakeMediaPoolItem(path)
        self.imported = []
        self._start, self._end = start, end

    def GetMediaPoolItem(self):
        return self.mpi

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetFusionCompNameList(self):
        return ["Composition 1"] if self.imported else []

    def DeleteFusionCompByName(self, _name):
        self.imported = []
        return True

    def ImportFusionComp(self, path):
        with open(path, encoding="utf-8") as f:
            self.imported.append(f.read())
        return _FakeComp()

    def GetFusionCompByName(self, _name):
        return _FakeComp()


class _FakeTimeline:
    def __init__(self, items_by_track):
        self.items_by_track = items_by_track

    def GetSetting(self, _key):
        return "24"

    def GetItemListInTrack(self, _kind, index):
        return self.items_by_track.get(index, [])


class _FakeResolve:
    def __init__(self, timeline):
        self._timeline = timeline

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self

    def GetCurrentTimeline(self):
        return self._timeline

    def OpenPage(self, _name):
        return True


class _FakeDvr:
    def __init__(self, timeline):
        self.timeline = timeline

    def scriptapp(self, _name):
        return _FakeResolve(self.timeline)


@pytest.fixture
def fusion_module():
    import library.tools.execution.apply_fusion_comps as afc
    return afc


def _timeline():
    return _FakeTimeline({
        1: [_FakeTimelineItem("/tmp/ak1.mxf", 120, 240),
            _FakeTimelineItem("/tmp/ak2.mxf", 240, 360)],
        2: [_FakeTimelineItem("/tmp/cr0.mxf", 0, 120),
            _FakeTimelineItem("/tmp/cr3.mxf", 360, 480)],
    })


def test_drift_on_each_row_gets_a_comp_and_draws(fusion_module,
                                                 monkeypatch, tmp_path):
    """The Reel 09 receipt shape, closed: four planned, four checked."""
    timeline = _timeline()
    monkeypatch.setattr(fusion_module, "dvr", _FakeDvr(timeline))

    assert fusion_module.apply_fusion_comps(
        json.loads(json.dumps(_manifest())), str(tmp_path)) is True

    for row in (1, 2):
        for item in timeline.items_by_track[row]:
            assert item.imported, (
                f"{item.mpi.GetClipProperty('File Path')} got no comp - "
                f"its planned drift never reached the picture")

    result = read_receipts(str(tmp_path), "render")["verify_treatment"][
        "result"]
    assert result["clips_checked"] == len(result["rows"])
    drift_rows = [r for r in result["rows"] if "motion_over_time" in r]
    assert {r["label"] for r in drift_rows} == {
        reel_look.clip_label(i) for i in range(4)}
    assert all(r["motion_over_time"] for r in drift_rows), (
        "a planned drift that moves no frame is the shipped defect")
    assert not any(r["undone"] for r in drift_rows)


# --------------------------------------------------------------------------
# From test_reel_framing.py
#
# The picture a built reel puts on the frame, and the F12 gate that reads it.
# Both directions are pinned (AGENTS.md 10.4): the gate passes the declared
# framing and fails the same reel under the engine default.
#
# History: `docs/evidence/framing_intent.md` (test_reel_framing.py).

# The delivery frame every reel in this engine is built into.
FRAME_W, FRAME_H = 1080, 1920

# The podcast's source: 3840x2160 MXF, measured by step 1.02.
SRC_W, SRC_H = 3840, 2160

# What Resolve reports for an item nobody has touched, which is what all
# 376 items of the field test carry.
_HARVEST = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
            "CropLeft": 0.0, "CropRight": 0.0,
            "CropTop": 0.0, "CropBottom": 0.0}


def _item(transform=None, source="/footage/LC4930.MXF", track=1):
    return TimelineItem(
        track_type="video", track_index=track, start_frame=0, end_frame=100,
        duration_frames=100, source_start_frame=0, source_end_frame=100,
        source_file=source, speaker="Craig", name="LC4930.MXF",
        transform=dict(_HARVEST if transform is None else transform))


_SIZES = {"/footage/LC4930.MXF": {"width": SRC_W, "height": SRC_H,
                                  "rotation": 0}}


# ── The geometry ─────────────────────────────────────────────────────

class TestDeliveredPicture:

    def test_untouched_landscape_delivers_the_measured_strip(self):
        """The number `render_qa` measured on a real export, from metadata."""
        picture = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, _HARVEST)
        assert picture.rect == (0, 656, 1080, 1264)
        assert picture.framing_intent == LETTERBOX
        assert round(picture.covered_fraction, 4) == 0.3167
        assert not picture.stretched
        assert not picture.crop_unread


    def test_fill_zoom_covers_the_whole_frame(self):
        ceiling = max_zoom(SRC_W, SRC_H, FRAME_W, FRAME_H)
        picture = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                    {"ZoomX": ceiling, "ZoomY": ceiling})
        assert picture.covered_fraction == pytest.approx(1.0, abs=1e-3)
        assert picture.framing_intent == pytest.approx(FILL)
        assert picture.left < 0 and picture.right > FRAME_W

    def test_the_intent_survives_a_round_trip(self):
        """`declared_picture` runs `_conform_fields`' formula forwards and
        `delivered_picture` runs it backwards; they must agree, or a
        disagreement between them would be two spellings of one geometry
        rather than a real difference in the picture."""
        for intent in (0.0, 1.0):
            declared = declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, intent)
            assert declared.framing_intent == pytest.approx(intent, abs=1e-6)


    def test_a_rotated_source_is_read_at_its_display_size(self):
        assert display_size(1920, 1080, rotation=90) == (1080, 1920)
        picture = delivered_picture(1920, 1080, FRAME_W, FRAME_H,
                                    _HARVEST, rotation=90)
        assert picture.covered_fraction == pytest.approx(1.0)
        assert picture.framing_intent == FILL

    def test_a_source_that_already_covers_fills_at_every_intent(self):
        """`source_covers_frame`: there are no bars to give, so LETTERBOX
        and FILL are the same picture and neither is a defect."""
        for intent in (LETTERBOX, 0.5, FILL):
            declared = declared_picture(1080, 1920, FRAME_W, FRAME_H, intent)
            assert declared.framing_intent == FILL
            assert declared.covered_fraction == pytest.approx(1.0)

    def test_a_source_with_no_dimensions_refuses(self):
        with pytest.raises(ReelFramingError):
            delivered_picture(0, 0, FRAME_W, FRAME_H, _HARVEST)


# ── The comparison ───────────────────────────────────────────────────

class TestDisagreement:

    def test_one_pixel_is_the_same_picture_and_two_is_not(self):
        """The only tolerance is the resolution of the medium: one real
        number rounded by two rules can land a pixel apart.

        The tolerance is in PIXELS, and a Tilt unit is not a pixel: on
        this geometry one unit draws `(2160/1920) * (1080/3840)` =
        0.3164 px, so a pixel of movement is Tilt 3.16 and two pixels
        is Tilt 6.32.  Spelling these as `PIXEL` and `PIXEL + 1` was
        the picture path's own version of the defect - it asked for
        two pixels and moved two thirds of one.
        """
        # History gain throughout: the 2026-09-11 read (one unit drew
        # 0.3164 px then; 0.6328 under today's).
        one_pixel_of_tilt = 1.0 / ((2160 / 1920) * (1080 / 3840))
        assert one_pixel_of_tilt == pytest.approx(3.1605, abs=0.001)
        base = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, _HARVEST,
                                 draw_gain=1.0)
        # NEGATIVE, because positive Tilt moves the picture UP - measured,
        # and the other half of what this path had wrong: it added Tilt to
        # the centre, so every vertical aim went the wrong way as well as
        # 3.16x short.
        near = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                 dict(_HARVEST, Tilt=-one_pixel_of_tilt),
                                 draw_gain=1.0)
        far = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                dict(_HARVEST, Tilt=-2 * one_pixel_of_tilt),
                                draw_gain=1.0)
        assert near.top == base.top + PIXEL
        assert far.top == base.top + 2 * PIXEL
        assert disagreement(near, base) is None
        assert disagreement(far, base) is not None

    def test_a_stretch_is_named_as_a_stretch(self):
        delivered = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                      dict(_HARVEST, ZoomX=2.0, ZoomY=1.0))
        assert delivered.stretched
        why = disagreement(delivered,
                           declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                            LETTERBOX))
        assert "stretched" in why

    def test_a_non_zero_crop_is_refused_rather_than_assumed(self):
        """AGENTS.md 5: judge a Resolve call by what it RETURNS. Every
        Crop* in the field test reads back 0.0, so their units have never
        been observed and are not guessed at here."""
        delivered = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                      dict(_HARVEST, CropLeft=100.0))
        assert delivered.crop_unread
        why = disagreement(delivered,
                           declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                            LETTERBOX))
        assert "crop" in why and "will not assume" in why


# ── F12, the gate ────────────────────────────────────────────────────

class TestF12:

    def test_it_passes_the_framing_the_project_declared(self):
        """The correct-output direction, on the geometry the captain's
        twenty harvest reels really carry."""
        findings = check_delivered_framing(
            "Reel 01 (harvest)", [_item()], FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=LETTERBOX)
        assert findings == []

    def test_it_fails_the_same_reel_under_the_engine_default(self):
        """The failing direction. Nothing about the timeline changed -
        only what the project says it wanted."""
        findings = check_delivered_framing(
            "Reel 01 (harvest)", [_item()], FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=DEFAULT_FRAMING_INTENT)
        assert [f.finding_class for f in findings] == [FindingClass.F12]
        assert findings[0].severity == "error"
        assert findings[0].detail["delivered"]["covered_fraction"] == 0.3167
        assert findings[0].detail["declared"]["covered_fraction"] == 1.0


    def test_an_unresolved_declaration_warns_rather_than_passing(self):
        findings = check_delivered_framing(
            "Reel 01 (harvest)", [_item()], FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=None)
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "cannot be compared" in findings[0].message


    def test_caption_cards_are_not_graded_as_footage(self):
        """V3 carries 1080x1920 overlays, which fill by construction.
        Grading them would report a defect on every reel that has them."""
        captions = [_item(source="/overlays/sub_x.mov", track=3)]
        findings = check_delivered_framing(
            "Reel 01 (harvest)", captions, FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=DEFAULT_FRAMING_INTENT)
        assert findings == []

    def test_a_timeline_with_no_resolution_is_not_graded(self):
        """There is nothing for the picture to be a fraction OF, and F10
        already reports a timeline that does not say its own shape."""
        assert check_delivered_framing(
            "Reel 01 (harvest)", [_item()], 0, 0,
            source_sizes=_SIZES, declared_intent=FILL) == []


# --------------------------------------------------------------------------
# From test_cut_in_anchored_window.py
#
# A comp keys its zoom to the anchored `effect_window_frames` and holds
# neutral outside it; no window (or a full-range one) keys as before.
# Asserted on the serialized comp's own splines.
#
# History: docs/evidence/composed_edit.md.

def _size_keys(comp_text):
    splines = parse_splines(comp_text)
    names = [n for n in splines if n.endswith("Size")]
    assert len(names) == 1, f"one zoom spline, got {sorted(splines)}"
    return splines[names[0]]


def _comp(zoom, window_frames=None, clip_dur=216):
    effects = {"zoom_start": zoom, "zoom_mid": zoom, "zoom_end": zoom}
    if window_frames is not None:
        effects["effect_window_frames"] = list(window_frames)
    return build_effect_comp(effects, clip_dur, (1920, 1080))


def test_anchored_cut_in_holds_neutral_outside_the_window():
    """Finding 36's exact shape: constant 1.15 over 216 played
    frames, anchored to 0.2-7.185 s = comp frames [6, 215]. Frame 2
    (0.07 s, before the word) holds neutral; the punch draws only
    inside the anchored span."""
    keys = _size_keys(_comp(1.15, [6, 215]))
    assert value_at(keys, 2) == 1.0
    assert value_at(keys, 100) == 1.15
    assert value_at(keys, 215) == 1.15


def test_no_window_or_a_full_range_window_punches_the_whole_item():
    """No window (an unanchored entry spanning the block) keeps the
    whole-item behaviour - a scalar Size, every frame punched - and a
    window covering everything played is no window: same scalar, no
    spline either."""
    for window in (None, [0, 215]):
        comp_text = _comp(1.15, window)
        assert "Size = Input { Value = 1.15, }" in comp_text, window
        assert parse_splines(comp_text) == {}


def test_windowed_drift_ramps_inside_and_holds_outside():
    """A drift keys its eased ramp to the window and holds neutral
    on both sides of it."""
    effects = {"zoom_start": 1.0, "zoom_mid": 1.02, "zoom_end": 1.04,
               "effect_window_frames": [10, 50]}
    keys = _size_keys(build_effect_comp(effects, 216, (1920, 1080)))
    assert value_at(keys, 0) == 1.0
    assert value_at(keys, 5) == 1.0
    assert 1.0 < value_at(keys, 30) < 1.04
    assert value_at(keys, 50) == 1.04
    assert value_at(keys, 100) == 1.0
