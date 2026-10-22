"""The reel-built Project Model: fragments, revisions and refusals.

Covers ``library/tools/reel_project_model.py`` without Resolve and
without a real project: every store lands under ``tmp_path``, and the
only media probed is the committed synthetic fixture.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from library.tools import reel_project_model as rpm
from library.tools.project_model import validate_project_model

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_MEDIA = REPO_ROOT / "tests/fixtures/project_model_e2e/media"


def _placements():
    return [
        {"source_file": "/m/a.MXF", "source_in": 10.0, "source_out": 15.0,
         "record_start": 0.0, "record_frame": 0,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll",
         "speaker": "Ada", "master_start": 10.0, "master_end": 15.0},
        {"source_file": "/m/a.MXF", "source_in": 10.0, "source_out": 15.0,
         "record_start": 0.0, "record_frame": 0,
         "track_type": "audio", "track_index": 1, "track_name": "Speech",
         "speaker": "Ada", "master_start": 10.0, "master_end": 15.0},
    ]


def _track_plan():
    return {"video_tracks": [
        {"index": 1, "media_type": "video", "role": "a_roll",
         "name": "A-Roll", "occupant": "main"}],
        "audio_tracks": []}


def _transcript():
    return {"segments": [
        {"speaker": "Ada", "text": "hello world",
         "timeline_start": 10.0, "timeline_end": 15.0,
         "source_file": "/m/a.MXF", "source_start": 10.0,
         "source_end": 15.0}]}


def _record(reel="Reel 01", placements=None):
    return {"reel_placements": {reel: placements if placements is not None
                                else _placements()},
            "track_plans": {reel: _track_plan()},
            "reel_fps": 24.0}


def test_sec_to_ticks_refuses_what_cannot_be_an_interval():
    assert rpm.sec_to_ticks(1.0, 2.0) == (120000, 240000)
    assert rpm.sec_to_ticks(2.0, 2.0) is None
    assert rpm.sec_to_ticks(2.0, 1.0) is None
    assert rpm.sec_to_ticks(float("nan"), 1.0) is None
    assert rpm.sec_to_ticks("x", 1.0) is None


def test_blank_model_validates():
    validate_project_model(rpm.blank_model("project-x"))


def test_clip_uses_equal_placements_and_empty_spans_refuse():
    model = rpm.blank_model("project-x")
    placements = _placements() + [
        {"source_file": "/m/a.MXF", "source_in": 5.0, "source_out": 5.0,
         "record_start": 5.0, "record_frame": 120,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll",
         "speaker": "Ada", "master_start": 5.0, "master_end": 5.0},
        {"source_file": "", "source_in": 1.0, "source_out": 2.0,
         "record_start": 6.0, "record_frame": 144,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll",
         "speaker": "Ada", "master_start": 1.0, "master_end": 2.0},
    ]
    # A-roll rows ride the recorded plan role; B-roll rows default.
    out = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", placements,
        {("video", 1): "a_roll"}, _transcript())
    assert len(out["use_ids"]) == 2
    assert [row["role"] for row in model["clip_uses"]] == ["a_roll", "speech"]
    assert [row["reason"] for row in out["skipped"]] == [
        "empty source span", "no source file"]
    video_use = model["clip_uses"][0]
    assert video_use["source_interval"] == {
        "clock_id": video_use["source_interval"]["clock_id"],
        "start": 1_200_000, "end": 1_800_000}
    assert video_use["timeline_interval"]["start"] == 0
    assert video_use["timeline_interval"]["end"] == 600_000
    assert any(row["operation"] == "reel.assemble" for row in model["edits"])
    validate_project_model(model)


def test_audio_clip_use_roles_follow_recorded_track_plan():
    placements = [
        {"source_file": "/m/music.wav", "source_in": 2.0,
         "source_out": 7.0, "record_start": 0.0, "record_frame": 0,
         "record_frames": 120, "track_type": "audio", "track_index": 2},
        {"source_file": "/m/hit.wav", "source_in": 0.0,
         "source_out": 1.0, "record_start": 1.0, "record_frame": 24,
         "record_frames": 24, "track_type": "audio", "track_index": 3},
    ]
    model = rpm.blank_model("project-x")
    track_plan = {"audio_tracks": [
        {"index": 2, "role": "music"}, {"index": 3, "role": "sfx"}]}
    report = rpm.build_reel_fragment(
        model, "Reel 01", placements, track_plan, fps=24.0)
    assert report["clip_uses_skipped"] == []
    assert [row["role"] for row in model["clip_uses"]] == ["music", "sfx"]
    assert [row["timeline_interval"]["start"] for row in model["clip_uses"]] == [
        0, 120_000]


def test_recorded_post_header_placement_keeps_resolve_timing_and_transform():
    model = rpm.blank_model("project-x")
    reel_clock = rpm._reel_clock(model, "reel-22", "Reel 22", 24.0)
    out = rpm.overlay_uses_from_records(model, "reel-22", reel_clock, [{
        "kind": "post header", "overlay_path": "/media/header_tight.png",
        "timeline_start_frame": 12, "source_start_frame": 0,
        "duration_frames": 72,
        "transform": {"ZoomX": 1.25, "ZoomY": 1.25},
        "native_position": [540.0, 310.0],
    }], 24.0)
    use = model["clip_uses"][0]
    assert use["role"] == "post_header"
    assert use["timeline_interval"] == {
        "clock_id": reel_clock, "start": 60_000, "end": 420_000}
    effects = {row["class_id"]: row for row in model["effects"]}
    assert effects["transform.scale"]["parameters"] == {"scale": 1.25}
    assert effects["transform.position"]["parameters"] == {
        "position": [540.0, 310.0]}
    assert out["skipped"] == []


def test_declared_word_gap_ducking_targets_music_clip_use():
    model = rpm.blank_model("project-x")
    placements = [
        {"source_file": "/m/speech.wav", "source_in": 0.0,
         "source_out": 5.0, "record_start": 0.0, "record_frame": 0,
         "record_frames": 120, "master_start": 10.0, "master_end": 15.0,
         "track_type": "audio", "track_index": 1},
        {"source_file": "/m/music.wav", "source_in": 2.0,
         "source_out": 7.0, "record_start": 0.0, "record_frame": 0,
         "record_frames": 120, "track_type": "audio", "track_index": 2},
    ]
    plan = {"audio_tracks": [
        {"index": 1, "role": "speech"},
        {"index": 2, "role": "music"}]}
    mix = {
        "music_ducking_plan": {"enabled": True, "duck_db": 6.0,
                               "release_ms": 200.0},
        "music_automation": [{"word_intervals": [[11.0, 12.0]],
                               "word_gap_level_db": -12.0}],
    }
    report = rpm.build_reel_fragment(
        model, "Reel 01", placements, plan, audio_mix_spec=mix, fps=24.0)
    assert len(report["word_gap_ducking_effects"]) == 1
    effect = model["effects"][0]
    assert effect["class_id"] == "audio.ducking"
    assert effect["target_id"] == model["clip_uses"][1]["id"], (
        "the ducking targets the MUSIC clip use, by the model's own "
        "id rather than by its position in the placement list")
    assert effect["interval"]["start"] == 120_000
    assert effect["interval"]["end"] == 240_000
    assert effect["parameters"] == {
        "full_db": -12.0, "depth_db": 6.0, "ramp_ticks": 24_000}
    validate_project_model(model)


def test_speaker_label_reaches_roster_person():
    model = rpm.blank_model("project-x")
    model["entities"].append({"id": "person-ada", "kind": "person",
                              "label": "Ada", "object_class_id": None})
    assert rpm.person_for_speaker(model, "ada") == "person-ada"
    assert rpm.person_for_speaker(model, "Ben") != "person-ada"
    assert next(e for e in model["entities"]
                if e["label"] == "Ben")["kind"] == "speaker"


def test_fragment_carries_producer_moves_and_says_when_absent():
    """Step 4.03 (PR 2263) is the one move producer: carried, never minted.

    A present ``move_effects`` list rides verbatim onto the model;
    an absent one is recorded as "no move_effects in
    enhancement_spec" rather than derived here.
    """
    placements = [dict(_placements()[0])]
    transcript = _transcript()
    move = {"id": "effect:reel-01:vfx_001",
            "class_id": "move.slow_zoom_in",
            "target_id": "use:reel-01:0000",
            "interval": {"clock_id": "clock:reel:reel-01",
                         "start": 0, "end": 5000},
            "parameters": {"zoom_start": 1.0, "zoom_end": 1.2},
            "keyframes": []}
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(
        model, "Reel 01", placements, _track_plan(), transcript,
        move_effects=[dict(move)])
    assert report["move_effects"] == ["effect:reel-01:vfx_001"]
    assert report["move_effects_skipped"] == []
    assert next(row for row in model["effects"]
                if row["id"] == "effect:reel-01:vfx_001")["target_id"] == \
        "use:reel-01:0000"
    validate_project_model(model)

    bare = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(
        bare, "Reel 01", placements, _track_plan(), transcript)
    assert report["move_effects"] == []
    assert report["move_effects_skipped"] == [
        {"reason": "no move_effects in enhancement_spec"}]
    validate_project_model(bare)


def test_fragment_migrates_recorded_static_scale_and_native_position():
    placements = [dict(_placements()[0])]
    placements[0]["transform"] = {"ZoomX": 2.1386, "ZoomY": 2.1386,
                                  "Pan": -6.5, "Tilt": -174.01}
    placements[0]["native_position"] = [120.0, -480.0]
    placements[0]["native_scale"] = 0.60148125
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(model, "Reel 01", placements,
                                     _track_plan(), _transcript())
    assert len(report["static_transforms"]) == 2
    effects = {row["class_id"]: row for row in model["effects"]}
    assert effects["transform.scale"]["parameters"] == {
        "scale": 0.60148125}
    assert effects["transform.position"]["parameters"] == {
        "position": [120.0, -480.0]}
    assert report["static_transforms_skipped"] == []
    validate_project_model(model)


def test_fragment_refuses_non_uniform_static_zoom_without_minting():
    placements = [dict(_placements()[0])]
    placements[0]["transform"] = {"ZoomX": 2.0, "ZoomY": 1.5}
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(model, "Reel 01", placements,
                                     _track_plan(), _transcript())
    assert report["static_transforms"] == []
    assert report["static_transforms_skipped"] == [{
        "index": 0,
        "reason": "non-uniform ZoomX/ZoomY has no uniform native scale",
    }]


def test_fragment_refuses_picture_zoom_without_measured_native_scale():
    placements = [dict(_placements()[0])]
    placements[0]["transform"] = {"ZoomX": 2.0, "ZoomY": 2.0}
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(model, "Reel 01", placements,
                                     _track_plan(), _transcript())
    assert report["static_transforms"] == []
    assert report["static_transforms_skipped"] == [{
        "index": 0,
        "reason": "Resolve zoom needs measured source and sequence dimensions for the native scaleToFit conversion",
    }]


def test_transform_migration_keeps_placement_identity_after_a_skipped_use():
    placements = [dict(_placements()[0]), dict(_placements()[0])]
    placements[0]["source_file"] = ""
    placements[0]["transform"] = {"ZoomX": 3.0, "ZoomY": 3.0}
    placements[1]["source_file"] = "/m/b.MXF"
    placements[1]["record_start"] = 5.0
    placements[1]["source_in"] = 20.0
    placements[1]["source_out"] = 25.0
    placements[1]["transform"] = {"ZoomX": 2.0, "ZoomY": 2.0}
    placements[1]["native_scale"] = 2.0
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(model, "Reel 01", placements,
                                     _track_plan())
    assert report["clip_uses"] == 1
    assert len(report["static_transforms"]) == 1
    assert model["effects"][0]["target_id"] == \
        model["clip_uses"][0]["id"]


def test_fragment_migrates_measured_overlay_clip_and_position_effect():
    record = {"kind": "caption", "overlay_path": "/m/caption.mov",
              "timeline_start_frame": 24, "duration_frames": 48,
              "source_start_frame": 0, "native_position": [540.0, 1500.0],
              "transform": {"ZoomX": 1.25, "ZoomY": 1.25,
                            "Pan": 0.0, "Tilt": 0.0}}
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(
        model, "Reel 01", [], _track_plan(), overlay_placements=[record],
        fps=24.0)
    assert report["overlay_uses"] == [model["clip_uses"][0]["id"]]
    assert report["overlay_transforms_skipped"] == []
    assert len(report["overlay_transform_effects"]) == 2
    use = next(row for row in model["clip_uses"]
               if row["id"] == report["overlay_uses"][0])
    assert use["role"] == "subtitle_overlay"
    assert use["timeline_interval"] == {
        "clock_id": "clock:reel:reel-01", "start": 120000, "end": 360000}
    effect = next(row for row in model["effects"]
                  if row["class_id"] == "transform.position")
    assert effect["class_id"] == "transform.position"
    assert effect["parameters"] == {"position": [540.0, 1500.0]}
    scale = next(row for row in model["effects"]
                 if row["class_id"] == "transform.scale")
    assert scale["parameters"] == {"scale": 1.25}
    validate_project_model(model)
    assert effect["target_id"] == use["id"]


def test_overlay_transforms_get_distinct_effect_ids_and_targets():
    records = [
        {"kind": "caption", "overlay_path": f"/m/caption-{index}.mov",
         "timeline_start_frame": index * 24, "duration_frames": 12,
         "source_start_frame": 0, "native_position": [540.0, 1500.0],
         "transform": {"ZoomX": 1.0, "ZoomY": 1.0}}
        for index in range(3)]
    model = rpm.blank_model("project-x")
    rpm.build_reel_fragment(model, "Reel 01", [], _track_plan(),
                            overlay_placements=records, fps=24.0)
    positions = [row for row in model["effects"]
                 if row["class_id"] == "transform.position"]
    assert len(positions) == 3
    assert len({row["id"] for row in positions}) == 3
    assert {row["target_id"] for row in positions} == {
        row["id"] for row in model["clip_uses"]}


def test_fractional_rate_overlay_frames_meet_without_rounding_overlap():
    records = [
        {"kind": "caption", "overlay_path": f"/m/caption-{index}.mov",
         "timeline_start_frame": index, "duration_frames": 1,
         "source_start_frame": index}
        for index in range(2)]
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(
        model, "Reel 01", [], _track_plan(), overlay_placements=records,
        fps=23.976)
    uses = [next(row for row in model["clip_uses"] if row["id"] == use_id)
            for use_id in report["overlay_uses"]]
    first, second = [use["timeline_interval"] for use in uses]
    assert first["end"] == second["start"] == 5005


def test_fractional_rate_picture_frames_meet_without_rounding_overlap():
    fps = 23.976
    placements = []
    for frame in range(2):
        placements.append({
            "source_file": f"/m/picture-{frame}.mov",
            "source_in": 10.0, "source_out": 10.0 + 1 / fps,
            "record_start": frame / fps, "record_frame": frame,
            "record_frames": 1, "track_type": "video", "track_index": 1,
            "track_name": "A-Roll", "master_start": 10.0,
            "master_end": 10.0 + 1 / fps,
        })
    model = rpm.blank_model("project-x")
    rpm.build_reel_fragment(model, "Reel 01", placements, _track_plan(),
                            fps=fps)
    intervals = [use["timeline_interval"] for use in model["clip_uses"]]
    assert intervals[0]["end"] == intervals[1]["start"] == 5005


def test_fragment_keeps_end_card_overlay_on_the_render_spine():
    record = {"kind": "end_card", "overlay_path": "/m/logo.mov",
              "timeline_start_frame": 24, "duration_frames": 24,
              "source_start_frame": 0}
    model = rpm.blank_model("project-x")
    report = rpm.build_reel_fragment(
        model, "Reel 01", [], _track_plan(), overlay_placements=[record],
        fps=24.0)
    use_id = report["overlay_uses"][0]
    use = next(row for row in model["clip_uses"] if row["id"] == use_id)
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == report["frame_map"])
    assert use["role"] == "end_card"
    assert frame_map["segments"][0]["source_clip_use"] == use_id


def test_carry_move_effects_verbatim_and_invalid_dropped():
    model = rpm.blank_model("project-x")
    good = {"id": "effect:mint:1", "class_id": "move.slow_zoom_in",
            "target_id": "use:x", "interval": {"clock_id": "c",
                                               "start": 0, "end": 5},
            "parameters": {"zoom_start": 1.0}, "keyframes": []}
    bad = {"id": "effect:mint:2", "class_id": "move.slow_zoom_in"}
    out = rpm.carry_move_effects(model, [good, bad, "nope"])
    assert out["carried"] == ["effect:mint:1"]
    assert len(out["skipped"]) == 2


def test_face_only_person_reaches_a_roll_and_renders():
    """MU-07: a face-only person with no transcript speaker reaches
    A-roll clip uses and rendered ranges through M7 on-screen cover.

    On main the no-speaker placement mints a use with empty
    ``entity_links``, so ``resolve_person`` finds no clip use and no
    rendered range.
    """
    from library.tools import project_model_evidence as evidence
    model = rpm.blank_model("project-mu07")
    model["entities"].append({"id": "person_001", "kind": "person",
                              "label": None, "object_class_id": None})
    rpm.ensure_provenance(model)
    asset = rpm.asset_for_source(model, "/m/face.MXF", "video")
    assert asset is not None
    asset_id, source_clock = asset
    model["observations"].append({
        "id": "obs-face-1", "asset_id": asset_id,
        "interval": {"clock_id": source_clock,
                     "start": 1_200_000, "end": 1_320_000},
        "modality": "face", "payload": {"face_track": "track-1"},
        "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
    })
    model["identity_links"].append({
        "id": "link-person_001-face",
        "entity_id": "person_001", "observation_id": "obs-face-1",
        "method": "face_embedding_cluster", "status": "verified",
        "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
    })
    model["observations"].append({
        "id": "obs-on-screen-1", "asset_id": asset_id,
        "interval": {"clock_id": source_clock,
                     "start": 1_200_000, "end": 1_800_000},
        "modality": "event_span",
        "payload": {"predicate": "on_screen", "face_track": "track-1",
                    "voice_track": None},
        "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
    })
    placements = [{
        "source_file": "/m/face.MXF", "source_in": 10.0,
        "source_out": 15.0, "record_start": 0.0, "record_frame": 0,
        "track_type": "video", "track_index": 1, "track_name": "A-Roll",
        "master_start": 10.0, "master_end": 15.0,
    }]
    out = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", placements,
        {("video", 1): "a_roll"}, None)
    assert len(out["use_ids"]) == 1
    use = next(row for row in model["clip_uses"]
               if row["id"] == out["use_ids"][0])
    assert use["role"] == "a_roll"
    assert use["entity_links"] == ["person_001"]
    assert "obs-on-screen-1" in out["evidence_ids"]
    link = next(row for row in model["identity_links"]
                if row.get("observation_id") == "obs-on-screen-1")
    assert link["entity_id"] == "person_001"
    assert link["status"] == "candidate"
    assert link["confidence"] == 1.0
    map_id = rpm.frame_map_for_reel(
        model, "reel-01", "clock:reel:reel-01", out["use_ids"])
    model["assets"].append({
        "id": "asset:render:reel-01", "kind": "render",
        "uri": "/out/reel01.mp4", "content_digest": None,
        "metadata": {}, "provenance": [rpm.PROVENANCE_ID],
    })
    model["receipts"].append({
        "id": "receipt:reel-01", "status": "ok", "renderer": "resolve",
        "input_revision": "rev-1", "output_asset_id": "asset:render:reel-01",
        "details": {},
    })
    model["renders"].append({
        "id": "render:reel-01", "input_revision": "rev-1",
        "output_asset_id": "asset:render:reel-01",
        "receipt_id": "receipt:reel-01", "frame_map_id": map_id,
        "settings": {"expected_frames": 120},
    })
    validate_project_model(model)
    resolved = evidence.resolve_person(model, "person_001")
    assert resolved is not None
    assert [row["id"] for row in resolved["clip_uses"]] == \
        out["use_ids"]
    assert len(resolved["rendered_ranges"]) == 1
    assert (resolved["rendered_ranges"][0]["clip_use_id"]
            == out["use_ids"][0])


def test_ambiguous_on_screen_cover_links_both_persons():
    """MU-07: two persons covering one speaker-less use are linked as
    both, with their covered fractions, never collapsed to one."""
    model = rpm.blank_model("project-mu07-ambiguous")
    for person_id, track in (("person_001", "track-1"),
                             ("person_002", "track-2")):
        model["entities"].append({"id": person_id, "kind": "person",
                                  "label": None, "object_class_id": None})
        model["observations"].append({
            "id": f"obs-face-{track}", "asset_id": "asset-x",
            "interval": {"clock_id": "clock-x",
                         "start": 1_200_000, "end": 1_320_000},
            "modality": "face", "payload": {"face_track": track},
            "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
        })
        model["identity_links"].append({
            "id": f"link-{person_id}-face",
            "entity_id": person_id,
            "observation_id": f"obs-face-{track}",
            "method": "face_embedding_cluster", "status": "verified",
            "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
        })
    asset = rpm.asset_for_source(model, "/m/two.MXF", "video")
    assert asset is not None
    asset_id, source_clock = asset
    for obs in model["observations"]:
        if obs["modality"] == "face":
            obs["asset_id"] = asset_id
            obs["interval"]["clock_id"] = source_clock
    # Each person covers half of the 10-15 s use.
    model["observations"].append({
        "id": "obs-on-screen-1", "asset_id": asset_id,
        "interval": {"clock_id": source_clock,
                     "start": 1_200_000, "end": 1_500_000},
        "modality": "event_span",
        "payload": {"predicate": "on_screen", "face_track": "track-1",
                    "voice_track": None},
        "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
    })
    model["observations"].append({
        "id": "obs-on-screen-2", "asset_id": asset_id,
        "interval": {"clock_id": source_clock,
                     "start": 1_500_000, "end": 1_800_000},
        "modality": "event_span",
        "payload": {"predicate": "on_screen", "face_track": "track-2",
                    "voice_track": None},
        "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
    })
    placements = [{
        "source_file": "/m/two.MXF", "source_in": 10.0,
        "source_out": 15.0, "record_start": 0.0, "record_frame": 0,
        "track_type": "video", "track_index": 1, "track_name": "A-Roll",
        "master_start": 10.0, "master_end": 15.0,
    }]
    out = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", placements,
        {("video", 1): "a_roll"}, None)
    use = next(row for row in model["clip_uses"]
               if row["id"] == out["use_ids"][0])
    assert sorted(use["entity_links"]) == ["person_001", "person_002"]
    assert sorted(out["evidence_ids"]) == [
        "obs-on-screen-1", "obs-on-screen-2"]
    confidences = sorted(
        row["confidence"] for row in model["identity_links"]
        if row.get("method") == "on_screen_coverage")
    assert confidences == [0.5, 0.5]
    validate_project_model(model)


def test_maintain_commits_one_revision_and_partial_rebuild_keeps_siblings(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    first = rpm.maintain_from_build(str(project), _record("Reel 01"),
                                    transcript=_transcript())
    assert first["status"] == "committed"
    assert first["revision"] == 1
    # A partial rebuild of Reel 02 leaves Reel 01's rows alone.
    second = rpm.maintain_from_build(str(project), _record("Reel 02"),
                                     transcript=_transcript())
    assert second["status"] == "committed"
    assert second["revision"] == 2
    from library.tools import project_model_store as store
    _revision, model = store.load_project_model(project)
    reel_ones = [row["id"] for row in model["clip_uses"]
                 if row["id"].startswith("use:reel-01:")]
    reel_twos = [row["id"] for row in model["clip_uses"]
                 if row["id"].startswith("use:reel-02:")]
    assert reel_ones and reel_twos
    # Re-maintaining Reel 01 replaces, never duplicates, its rows.
    third = rpm.maintain_from_build(str(project), _record("Reel 01"),
                                    transcript=_transcript())
    assert third["status"] == "committed"
    _revision, model = store.load_project_model(project)
    assert (sum(1 for row in model["clip_uses"]
                if row["id"].startswith("use:reel-01:"))
            == len(reel_ones))
    validate_project_model(model)


def test_maintain_reuses_stored_evidence_when_fingerprint_matches(tmp_path, monkeypatch):
    """A rebuild with unchanged evidence skips re-migration item-for-item.

    The fingerprint covers catalog digests and lane file stats; a match
    reuses the stored evidence base instead of re-migrating (58 s on the
    measured build). The reel fragment still rebuilds, and the merged
    model validates.
    """
    project = tmp_path / "proj"
    project.mkdir()
    first = rpm.maintain_from_build(str(project), _record("Reel 01"),
                                    transcript=_transcript())
    assert first["status"] == "committed"
    assert "migrated" in first["evidence"]
    from library.tools import project_model_store as store
    _revision, before = store.load_project_model(project)
    evidence_before = {key: before.get(key) for key in (
        "assets", "streams", "clocks", "entities", "observations",
        "identity_links", "masks", "object_classes", "tracks", "relations")}

    calls: list[str] = []
    from library.tools import project_model_evidence as evidence_mod
    original = evidence_mod.migrate_project_evidence

    def counted(*args, **kwargs):
        calls.append("migrate")
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence_mod, "migrate_project_evidence", counted)
    second = rpm.maintain_from_build(str(project), _record("Reel 01"),
                                     transcript=_transcript())
    assert second["status"] == "committed"
    assert "reused stored evidence" in second["evidence"]
    assert calls == []
    _revision, after = store.load_project_model(project)
    for key in evidence_before:
        assert after.get(key) == evidence_before[key]
    validate_project_model(after)


def test_maintain_commits_reel_only_patch_when_evidence_is_unchanged(tmp_path):
    """A fingerprint hit appends a KB-sized merge_patch, not a full model.

    The event log grew a 350 MB replace event per build; the patch
    carries only reel-scoped keys and replays to the identical model.
    A first build with no stored head still commits a full replace.
    """
    import json
    project = tmp_path / "proj"
    project.mkdir()
    first = rpm.maintain_from_build(str(project), _record("Reel 01"),
                                    transcript=_transcript())
    assert first["status"] == "committed"
    events_path = project / "project_model" / "events.jsonl"
    first_line = events_path.read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(first_line)["kind"] == "replace"

    second = rpm.maintain_from_build(str(project), _record("Reel 02"),
                                     transcript=_transcript())
    assert second["status"] == "committed"
    assert "reused stored evidence" in second["evidence"]
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    patch_event = json.loads(lines[1])
    assert patch_event["kind"] == "merge_patch"
    assert sorted(patch_event["payload"]) == sorted(rpm.REEL_COMMIT_KEYS)
    assert len(lines[1].encode("utf-8")) < 1024 * 1024
    from library.tools import project_model_store as store
    _revision, model = store.load_project_model(project)
    assert {row["id"] for row in model["clip_uses"]
            if row["id"].startswith("use:reel-01:")}
    assert {row["id"] for row in model["clip_uses"]
            if row["id"].startswith("use:reel-02:")}
    validate_project_model(model)


def test_maintain_without_placements_refuses_by_name(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    out = rpm.maintain_from_build(str(project), {"timelines_built": []})
    assert out["status"] == "refused"
    assert "reel_placements" in out["reason"]


def test_show_reads_reels_and_refuses_unknown(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    rpm.maintain_from_build(str(project), _record("Reel 01"),
                            transcript=_transcript())
    payload = rpm.show(str(project))
    assert payload["revision"] == 1
    assert payload["reels"][0]["uses"] == 2
    assert payload["reels"][0]["speech"] == 1
    one = rpm.show(str(project), "Reel 01")
    assert one["reels"][0]["reel"] == "reel-01"
    with pytest.raises(ValueError, match="names nothing"):
        rpm.show(str(project), "Reel 99")
    with pytest.raises(FileNotFoundError):
        rpm.show(str(tmp_path / "empty"))


def test_note_deliver_records_render_off_the_file(tmp_path):
    media = sorted(FIXTURE_MEDIA.glob("*.mp4"))
    assert media, "the synthetic fixture ships renderable media"
    project = tmp_path / "proj"
    project.mkdir()
    rpm.maintain_from_build(str(project), _record("Reel 01"),
                            transcript=_transcript())
    out = rpm.note_deliver(str(project), 1, "Reel 01", str(media[0]))
    assert out["status"] == "committed"
    assert out["frames"] and out["frames"] > 0
    from library.tools import project_model_store as store
    _revision, model = store.load_project_model(project)
    render = next(row for row in model["renders"]
                  if row["id"] == "render:reel-01")
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == render["frame_map_id"])
    assert (frame_map["segments"][-1]["to"]["end"]
            == render["settings"]["expected_frames"] * 5_000)
    validate_project_model(model)


def test_note_deliver_refuses_without_clip_uses(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    media = sorted(FIXTURE_MEDIA.glob("*.mp4"))
    out = rpm.note_deliver(str(project), 1, "Reel 01", str(media[0]))
    assert out["status"] == "refused"
    assert "no Project Model" in out["reason"]
    out = rpm.note_deliver(str(project), 1, "Reel 01", "/no/such/file.mp4")
    assert out["status"] == "refused"


def _two_source_placements():
    return [
        {"source_file": "/m/a.MXF", "source_in": 10.0,
         "source_out": 15.0, "record_start": 0.0, "record_frame": 0,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll",
         "master_start": 10.0, "master_end": 15.0},
        {"source_file": "/m/b.MXF", "source_in": 20.0,
         "source_out": 25.0, "record_start": 5.0, "record_frame": 120,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll",
         "master_start": 20.0, "master_end": 25.0},
    ]


def test_two_source_frame_map_resolves_per_segment_clock():
    """MU-21: a two-source reel maps a tick on the second source to
    the second render range, and refuses a tick asked on a clock that
    does not carry it.

    On main ``map_tick`` checks only ``map["from_clock"]`` (the first
    source), so the second source's tick maps against the wrong clock
    instead of refusing, and the second clock itself is a mismatch.
    """
    from library.tools import resolve_swapback as swap
    model = rpm.blank_model("project-mu21")
    out = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", _two_source_placements(),
        {("video", 1): "a_roll"}, None)
    assert len(out["use_ids"]) == 2
    map_id = rpm.frame_map_for_reel(
        model, "reel-01", "clock:reel:reel-01", out["use_ids"])
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == map_id)
    first, second = frame_map["segments"]
    assert first["from"]["clock_id"] != second["from"]["clock_id"]
    assert frame_map["from_clock"] == first["from"]["clock_id"]
    assert (first["from"]["start"], first["from"]["end"]) == (1_200_000, 1_800_000)
    assert (second["from"]["start"], second["from"]["end"]) == (2_400_000, 3_000_000)
    assert (first["to"]["start"], first["to"]["end"]) == (0, 600_000)
    assert (second["to"]["start"], second["to"]["end"]) == (600_000, 1_200_000)
    # The second source's tick lands on the second render range.
    assert swap.map_tick(
        frame_map, second["from"]["clock_id"], 2_400_000) == 600_000
    assert swap.map_tick(
        frame_map, second["from"]["clock_id"], 2_999_999) == 1_199_999
    # Asked on the first clock, that tick refuses: on main it
    # answered with the second range against the wrong clock.
    with pytest.raises(swap.SwapbackRefused):
        swap.map_tick(frame_map, first["from"]["clock_id"], 2_400_000)
    with pytest.raises(swap.SwapbackRefused):
        swap.map_tick(frame_map, "clock:src:no-such-source", 2_400_000)
    validate_project_model(model)


def test_same_tick_on_two_sources_answers_per_clock():
    """MU-21: two sources covering the same numeric tick each answer
    their own render range. On main the shared tick is ambiguous
    across clocks instead of resolving per segment."""
    from library.tools import resolve_swapback as swap
    model = rpm.blank_model("project-mu21-overlap")
    placements = [_two_source_placements()[0],
                  dict(_two_source_placements()[0],
                       source_file="/m/b.MXF",
                       record_start=5.0, record_frame=120)]
    out = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", placements,
        {("video", 1): "a_roll"}, None)
    map_id = rpm.frame_map_for_reel(
        model, "reel-01", "clock:reel:reel-01", out["use_ids"])
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == map_id)
    first, second = frame_map["segments"]
    assert first["from"]["clock_id"] != second["from"]["clock_id"]
    assert (first["from"]["start"], first["from"]["end"]) == (1_200_000, 1_800_000)
    assert (second["from"]["start"], second["from"]["end"]) == (1_200_000, 1_800_000)
    assert swap.map_tick(
        frame_map, first["from"]["clock_id"], 1_440_000) == 240_000
    assert swap.map_tick(
        frame_map, second["from"]["clock_id"], 1_440_000) == 840_000
    validate_project_model(model)


# ── A-10: the reel boundary is anchored to the frame grid ─────────


RATES = ((24, 1), (25, 1), (30, 1), (60, 1),
         (24000, 1001), (30000, 1001), (60000, 1001))
"""Every rate the benchmark measures: the three whole rates and the
three NTSC-family rates, which no float spells exactly."""

SPAN_FRAMES = (1, 2, 3, 5, 10, 24, 25, 30, 48, 60, 100, 120, 240, 300,
               600, 1000, 1800, 3600)
"""The span lengths the benchmark measures, in frames."""


def _grid_placement(num: int, den: int, frames: int, **extra) -> dict:
    """A placement whose source span is an exact number of frames."""
    fps = Fraction(num, den)
    placement = {
        "source_file": "/m/a.MXF",
        "source_fps": float(fps),
        "source_in": float(Fraction(frames) / fps),
        "source_out": float(Fraction(2 * frames) / fps),
        "record_frame": 0,
        "record_frames": frames,
        "track_type": "video", "track_index": 1, "track_name": "A-Roll",
        "master_start": 0.0, "master_end": 1.0,
    }
    placement.update(extra)
    return placement


def test_the_clip_use_source_span_lands_on_the_source_frame_grid():
    """A-10: the clip use's source span must convert to whole frames.

    The strict adapter converts the span to frames with ``scale_exact``
    and refuses a fractional result (``inexact_tick``), so a span a
    tick off the source's frame grid is a build that REFUSES on
    ordinary NTSC footage. The span was read off the float seconds the
    build recorded, and ``floor``/``ceil`` of a float second is a tick
    off the grid often enough to matter: measured before the fix, 19
    of 126 spans were off it.
    """
    off_grid = []
    for num, den in RATES:
        for frames in SPAN_FRAMES:
            model = rpm.blank_model("project-grid")
            rpm.clip_uses_from_placements(
                model, "reel-01", "Reel 01",
                [_grid_placement(num, den, frames)],
                {("video", 1): "a_roll"}, None, 24.0)
            assert model["clip_uses"], (num, den, frames)
            use = model["clip_uses"][0]
            clock = next(row for row in model["clocks"]
                         if row["id"] == use["source_interval"]["clock_id"])
            ticks_per_second = int(clock["ticks_per_second"])
            for tick in (use["source_interval"]["start"],
                         use["source_interval"]["end"]):
                if (tick * num) % (ticks_per_second * den):
                    off_grid.append((num, den, frames, tick))
    assert off_grid == [], f"{len(off_grid)} spans off the frame grid"


def test_the_clip_use_timeline_span_lands_on_the_reel_frame_grid():
    """A-10: the timeline span is read off the reel's frame grid too.

    The adapter converts the timeline interval with the same exact
    predicate, so a span a tick off the reel's grid refuses the build
    exactly as the source span did.
    """
    off_grid = []
    for num, den in RATES:
        fps = Fraction(num, den)
        for frames in SPAN_FRAMES:
            model = rpm.blank_model("project-reel-grid")
            placement = {
                "source_file": "/m/a.MXF",
                "source_fps": float(fps),
                "source_in": 0.0,
                "source_out": float(Fraction(frames) / fps),
                # No record frame: the writer falls back to the
                # recorded seconds, which is where the off-grid span
                # came from.
                "record_start": 1.0,
                "track_type": "video", "track_index": 1,
                "track_name": "A-Roll",
                "master_start": 0.0, "master_end": 1.0,
            }
            rpm.clip_uses_from_placements(
                model, "reel-01", "Reel 01", [placement],
                {("video", 1): "a_roll"}, None, float(fps))
            assert model["clip_uses"], (num, den, frames)
            use = model["clip_uses"][0]
            clock = next(row for row in model["clocks"]
                         if row["id"] == use["timeline_interval"]["clock_id"])
            ticks_per_second = int(clock["ticks_per_second"])
            for tick in (use["timeline_interval"]["start"],
                         use["timeline_interval"]["end"]):
                if (tick * num) % (ticks_per_second * den):
                    off_grid.append((num, den, frames, tick))
    assert off_grid == [], f"{len(off_grid)} spans off the frame grid"


def test_the_frame_map_carries_the_measured_retime_rate():
    """A-10: a piece played at three times its source speed is
    recorded as ``retimed`` at ``3/1``.

    The map wrote ``one_to_one`` at ``1/1`` for every piece whatever
    the build did, so the map said the footage played at its own speed
    when it played three times as fast.
    """
    model = rpm.blank_model("project-retime")
    placement = {
        "source_file": "/m/a.MXF", "source_fps": 24.0,
        "source_in": 0.0, "source_out": 3.0,
        "record_frame": 0, "record_frames": 24,
        "rate": 3.0,
        "track_type": "video", "track_index": 1, "track_name": "A-Roll",
        "master_start": 0.0, "master_end": 1.0,
    }
    rpm.build_reel_fragment(
        model, "Reel 01", [placement], _track_plan(), fps=24.0,
        validate=False)
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == "map:reel-01")
    segment = frame_map["segments"][0]
    assert segment["mapping_kind"] == "retimed"
    assert segment["rate"] == {"numerator": 3, "denominator": 1}
    # The rate is the placement's speed: three source frames for every
    # reel frame, which is what the source and timeline intervals say.
    source = model["clip_uses"][0]["source_interval"]
    timeline = model["clip_uses"][0]["timeline_interval"]
    assert source["end"] - source["start"] == 3 * (timeline["end"]
                                                    - timeline["start"])


def test_a_placement_at_its_own_speed_stays_one_to_one():
    """A-10: a piece the build played at its own speed is still
    ``one_to_one`` at ``1/1`` - the retime rate says what happened,
    and it says nothing when nothing happened."""
    model = rpm.blank_model("project-one-to-one")
    rpm.build_reel_fragment(
        model, "Reel 01", _placements(), _track_plan(), fps=24.0,
        validate=False)
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == "map:reel-01")
    for segment in frame_map["segments"]:
        assert segment["mapping_kind"] == "one_to_one"
        assert segment["rate"] == {"numerator": 1, "denominator": 1}


# ── The reel build's own speech rows ───────────────────────────────


def _speech_model(duration=100.0, rate=120_000):
    """A model with one source, its clock, and the reel build's rows."""
    model = rpm.blank_model("project-x")
    model["clocks"].append({
        "id": "clock-a",
        "domain": {"kind": "source", "asset_id": "asset-a"},
        "ticks_per_second": rate,
    })
    model["assets"].append({
        "id": "asset-a", "kind": "video", "uri": "/m/a.MXF",
        "content_digest": "a" * 64,
        "metadata": {"duration_seconds": duration},
        "provenance": ["prov-reel-build"],
    })
    model["streams"].append({
        "id": "stream-a", "asset_id": "asset-a", "clock_id": "clock-a",
        "stream_index": 0, "metadata": {"codec_type": "video"},
    })
    model["provenance"].append({
        "id": "prov-reel-build", "producer": "reel_project_model",
        "producer_version": "1", "source_digest": None,
    })
    return model


def _speech_transcript(source_start=10.0, source_end=15.0, text="hello world"):
    return {"segments": [
        {"speaker": "Ada", "text": text, "timeline_start": 10.0,
         "timeline_end": 15.0, "source_file": "/m/a.MXF",
         "source_start": source_start, "source_end": source_end}]}


def test_the_reel_builds_own_rows_carry_a_contract():
    """The defect: the reel build's rows said nothing about themselves.

    431 rows of one reel carried no evidence contract at all, so a
    reader could not tell a transcribed utterance from an instrument's
    reading. The reel build is the producer of these rows: it declares
    what they are, and it declares them under the registry's own type -
    the same one the M1 transcript lane files its utterances under.
    """
    model = _speech_model()
    rpm.speech_observations(model, _speech_transcript(), "Ada", "/m/a.MXF",
                            "asset-a", "clock-a", "speaker-ada")
    row = model["observations"][0]
    contract = row["payload"]["evidence_contract"]
    assert contract["measurement_type"] == "speech.utterance"
    assert contract["evidence_type"] == "inference"
    assert contract["coverage"] == "complete"
    assert contract["uncertainty"] == pytest.approx(1.0)


def test_the_reel_build_replaces_the_row_it_owns():
    """The defect: a row left behind by an earlier build survived.

    The reel build wrote its rows once and never wrote them again, so a
    row left on an older grid stayed in the model forever - the model
    kept a measurement the producer no longer stands behind. The
    reel build owns these rows, so it replaces them.
    """
    model = _speech_model()
    rpm.speech_observations(model, _speech_transcript(), "Ada", "/m/a.MXF",
                            "asset-a", "clock-a", "speaker-ada")
    stale = model["observations"][0]
    stale["interval"] = {"clock_id": "clock-a", "start": 1,
                          "end": 120000 * 120 * 120}
    stale["payload"].pop("evidence_contract")
    rpm.speech_observations(model, _speech_transcript(), "Ada", "/m/a.MXF",
                            "asset-a", "clock-a", "speaker-ada")
    assert len(model["observations"]) == 1
    row = model["observations"][0]
    assert row["interval"] == {"clock_id": "clock-a", "start": 1200000,
                                "end": 1800000}
    assert row["payload"]["evidence_contract"]["measurement_type"] \
        == "speech.utterance"


def test_the_reel_build_refuses_a_span_past_the_source():
    """The defect: a row could claim audio the source does not hold.

    The reel build minted its rows from the transcript's own seconds, and
    a transcript row that runs past the source's end became a span of
    frames that are not there. The writer refuses it instead.
    """
    model = _speech_model(duration=12.0)
    rpm.speech_observations(model, _speech_transcript(source_start=10.0,
                                                       source_end=15.0),
                            "Ada", "/m/a.MXF", "asset-a", "clock-a",
                            "speaker-ada")
    assert model["observations"] == []
