"""The reels-path look: what it refuses, and what it places.

`library/tools/reel_look.py` is the reels path's route to the declared
TV-frame look.  These cover the two things that cost real time on
2026-09-09: a frame asset whose aspect cannot frame the delivery
reaching a render, and the frame's own track under per-speaker picture
rows (captain's ruling on Reel 09: two picture rows, the set above
them, no collapse).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import reel_look
from library.tools import tv_frame
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


def test_a_frame_whose_cover_would_upscale_it_is_refused(tmp_path):
    """The first thing that genuinely cannot be framed."""
    asset = _png(tmp_path / "small.png", (540, 960), window=True)
    with pytest.raises(ValueError) as excinfo:
        tv_frame.assert_frameable(_look(asset), 1080, 1920)
    message = str(excinfo.value)
    assert "upscale" in message
    assert "540x960" in message and "1080x1920" in message


def test_a_frame_with_no_window_is_refused(tmp_path):
    """The second: a frame with no window is a slate over the picture."""
    asset = _png(tmp_path / "slate.png", (3840, 2160), window=False)
    with pytest.raises(ValueError) as excinfo:
        tv_frame.assert_frameable(_look(asset), 1080, 1920)
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


def test_power_effects_land_on_the_first_and_last_picture():
    effects = reel_look.power_effects(
        {"power": {}}, reel_look.clip_label(0), reel_look.clip_label(2))
    assert effects[reel_look.clip_label(0)]["tv_power_head"] is True
    assert effects[reel_look.clip_label(2)]["tv_power_tail"] is True
    assert "tv_power_tail" not in effects[reel_look.clip_label(0)]


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
    assert request["context"]["shots"][0]["word_spans"] == []

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
    span = request["context"]["shots"][0]["word_spans"][0]
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


def test_no_ken_burns_moves_outside_cta_sections(tmp_path):
    """The sparse-plan defect: the ask left body cadence unstated.

    It now calls for recurring body moves, and a revised answer cannot
    replace the existing closer/CTA move with a newly chosen one.
    """
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.fspath(tmp_path / "project")
    response_dir = str(ProjectLayout(project_folder).read_dir(
        Area.LLM_RESPONSES))
    os.makedirs(response_dir, exist_ok=True)
    closer = {
        "target_block_position": 1,
        "effect_type": "ken_burns",
        "params": {"zoom_start": 1.04, "zoom_end": 1.0},
        "rationale": "the existing CTA pull-back",
    }

    placements = [
        _placement("/body.mxf", 0, 12.0),
        _placement("/closer.mxf", 288, 4.0),
    ]
    placements[0]["source_in"] = 2.0
    placements[0]["source_out"] = 14.0
    placements[0]["clip"] = _Clip(
        "/body.mxf", timeline_start=100.0, source_in=2.0)
    words = [
        {"word": "we", "start": 101.0, "end": 101.2},
        {"word": "bridge", "start": 105.5, "end": 105.7},
        {"word": "next", "start": 106.1, "end": 106.3},
        {"word": "idea", "start": 110.5, "end": 110.7},
    ]
    spine = reel_look.motion_spine(
        placements, 24.0,
        [{"source_file": "/body.mxf", "timeline_start": 100.0,
          "timeline_end": 112.0, "source_start": 2.0,
          "source_end": 14.0, "words": words}])
    says = [{"timeline_start": 0.0, "timeline_end": 12.0,
             "text": "We explain the idea."}]
    reel_look.write_motion_request(
        42, "Reel 42", spine, says, project_folder)
    response_path = os.path.join(response_dir, "reel_motion_42.json")
    with open(response_path, "w", encoding="utf-8") as handle:
        json.dump(reel_look.bind_motion_answer(
            project_folder, 42, {"reel_motion_plan": [closer]}), handle)
    path = reel_look.write_motion_request(
        42, "Reel 42", spine, says, project_folder)
    with open(path, "r", encoding="utf-8") as handle:
        request = json.load(handle)

    assert "one move per roughly 6-8 seconds" in request["prompt"]
    assert "body" in request["prompt"].lower()
    assert request["context"]["shots"][0]["word_spans"][0] == {
        "word": "we", "start": 1.0, "end": 1.2}
    assert request["context"]["locked_closing_moves"] == [closer]

    body_in = {
        "target_block_position": 0,
        "anchor": {"word": "we"},
        "anchor_end": {"word": "bridge", "edge": "end"},
        "effect_type": "ken_burns",
        "params": {"zoom_start": 1.0, "zoom_end": 1.03},
        "rationale": "the explanation builds toward the point",
    }
    body_out = {
        "target_block_position": 0,
        "anchor": {"word": "next"},
        "anchor_end": {"word": "idea", "edge": "end"},
        "effect_type": "ken_burns",
        "params": {"zoom_start": 1.03, "zoom_end": 1.0},
        "rationale": "the speaker opens the frame for the conclusion",
    }
    altered_closer = dict(closer)
    altered_closer["params"] = {"zoom_start": 1.0, "zoom_end": 1.1}
    with open(response_path, "w", encoding="utf-8") as handle:
        json.dump({"reel_motion_plan": [body_in, body_out, altered_closer]},
                  handle)
    answer = reel_look.read_motion_answer(project_folder, 42)
    assert answer == [body_in, body_out, closer]
    assert reel_look.locked_closing_positions(project_folder, 42) == {1}

    resolved, record = reel_look.resolve_motion(answer, spine, 24.0)
    assert record["resolved"] == 3
    assert [move["effect_type"] for move in record["moves"]] == [
        "slow_zoom_in", "slow_zoom_out", "slow_zoom_out"]
    assert [move["target_block_position"] for move in record["moves"]] == [
        0, 0, 1]


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


def test_build_motion_still_refuses_a_word_end_in_the_next_frame():
    fps = 24000 / 1001
    next_frame_edge = 227 / fps
    placement = _placement("/a.mxf", 0, 9.426, fps)
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
    with pytest.raises(reel_look.ReelLookRefused,
                       match="subtle 1.0-1.05 body scale envelope"):
        reel_look.fusion_manifest(
            [placement], {"power": {}}, [punch], fps)


def test_whole_shot_body_ken_burns_cannot_bypass_subtle_scale_contract():
    import pytest

    fps = 24.0
    placement = _placement("/body.mxf", 0, 12.0, fps)
    spine = reel_look.motion_spine([placement], fps)
    resolved, _ = reel_look.resolve_motion([
        {"target_block_position": 0,
         "effect_type": "ken_burns",
         "params": {"zoom_start": 1.0, "zoom_end": 1.07},
         "rationale": "the old whole-shot body push"},
    ], spine, fps)

    with pytest.raises(reel_look.ReelLookRefused,
                       match="exceeds the subtle 1.0-1.05 body scale"):
        reel_look.fusion_manifest(
            [placement], {"power": {}}, resolved, fps)


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
    import unittest.mock as mock
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
