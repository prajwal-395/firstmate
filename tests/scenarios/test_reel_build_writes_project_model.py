"""A real reel build writes its Project Model.

Runs the real step path on a fixture reel - the real placement
derivation (``reel_build.placements``), the real record serializer
(``serializable_placements``), the real store writer
(``reel_project_model.maintain_from_build``) - and validates the
written model against the schema: identities linked to
masks/speech/A-roll, clip uses equal to the timeline, effects
present. Everything lands under ``tmp_path``; no real project is
touched.
"""
from __future__ import annotations

import json
from pathlib import Path

from library.tools import reel_project_model as rpm
from library.tools.project_model import validate_project_model
from library.tools.reel_build import placements, serializable_placements
from library.tools.timeline_ingest import TimelineClip

FPS = 24.0


def _clip(track_type, index, name, speaker, source_in, tl_start, tl_end):
    duration = tl_end - tl_start
    return TimelineClip(
        resolve_item_id=f"{name}-{tl_start}", track_type=track_type,
        track_index=index, track_name=name, speaker=speaker,
        source_file="/m/a.MXF", source_in=source_in,
        source_out=source_in + duration,
        source_in_frame=int(source_in * FPS),
        source_out_frame=int(source_in * FPS) + int(duration * FPS),
        source_frames=100000, timeline_start=tl_start,
        timeline_end=tl_end, name="clip")


def _transcript():
    return {"segments": [
        {"speaker": "Ada", "text": "the first idea",
         "timeline_start": 10.0, "timeline_end": 15.0,
         "source_file": "/m/a.MXF", "source_start": 110.0,
         "source_end": 115.0},
        {"speaker": "Ben", "text": "the second view",
         "timeline_start": 20.0, "timeline_end": 26.0,
         "source_file": "/m/a.MXF", "source_start": 202.0,
         "source_end": 208.0},
    ]}


def _mask_for(entity_id, clock_id):
    return {"id": "mask:fixture:ada",
            "entity_id": entity_id,
            "interval": {"clock_id": clock_id, "start": 110000, "end": 115000},
            "representation": {"kind": "ellipse",
                               "normalized_bbox": [0.35, 0.1, 0.3, 0.65]},
            "source_model": "synthetic_fixture",
            "provenance_id": "prov-reel-build"}


def test_a_real_reel_build_writes_a_linked_model(tmp_path):
    """The whole path: derive, serialize, maintain, validate, resolve."""
    project = tmp_path / "proj"
    project.mkdir()
    master = [_clip("video", 1, "A-Roll", "Ada", 100.0, 0.0, 18.0),
              _clip("audio", 1, "Speech", "Ada", 100.0, 0.0, 18.0),
              _clip("video", 1, "A-Roll", "Ben", 200.0, 18.0, 40.0),
              _clip("audio", 1, "Speech", "Ben", 200.0, 18.0, 40.0)]
    # The reel keeps 10..15s of Ada and 20..26s of Ben: two cuts.
    derived = placements([(10.0, 15.0), (20.0, 26.0)], master, FPS)
    assert derived, "the real derivation must place the kept ranges"
    rows = serializable_placements(derived)
    assert len(rows) == len(derived)
    transcript = _transcript()
    record = {
        "reel_placements": {"Reel 01": rows},
        "track_plans": {"Reel 01": {
            "video_tracks": [{"index": 1, "media_type": "video",
                              "role": "a_roll", "name": "A-Roll",
                              "occupant": "main"}],
            "audio_tracks": []}},
        "reel_fps": FPS,
    }
    # The move arrives on the step 4.03 mint (PR 2263), already
    # shaped: target and interval off the post-migration model. The
    # writer carries it verbatim and never mints a second copy.
    spec = {"move_effects": [
        {"id": "effect:reel-01:vfx_001",
         "class_id": "move.slow_zoom_in",
         "target_id": "use:reel-01:0000",
         "interval": {"clock_id": "clock:reel:reel-01",
                      "start": 0, "end": 5000},
         "parameters": {"zoom_start": 1.0, "zoom_end": 1.15},
         "keyframes": []}]}
    # The mask is measured elsewhere (1.06 segmentation); the writer
    # carries it onto the speaker the transcript names. Resolve the
    # speaker entity first so the mask links the same identity the
    # A-roll does.
    probe = rpm.blank_model("project-probe")
    ada_entity = rpm.person_for_speaker(probe, "Ada")
    asset = rpm.asset_for_source(probe, "/m/a.MXF", "video")
    assert asset is not None
    out = rpm.maintain_from_build(
        str(project), record, transcript=transcript,
        enhancement_spec=spec,
        motion_graphics_overlay={"segments": []},
        masks=[_mask_for(ada_entity, asset[1])])
    assert out["status"] == "committed", out
    assert out["revision"] == 1

    from library.tools import project_model_store as store
    _revision, model = store.load_project_model(project)
    validate_project_model(model)

    # Clip uses equal the timeline: one per placed piece.
    assert len(model["clip_uses"]) == len(rows)
    video_uses = [row for row in model["clip_uses"]
                  if row["role"] == "a_roll"]
    assert len(video_uses) == 2
    first = video_uses[0]
    assert first["source_interval"]["start"] == 110000
    assert first["source_interval"]["end"] == 115000
    assert first["timeline_interval"]["start"] == 0
    assert first["timeline_interval"]["end"] == 5000

    # Identities: Ada's speaker entity links speech, mask and A-roll.
    ada_uses = [row for row in model["clip_uses"]
                if ada_entity in row["entity_links"] and row["role"] == "a_roll"]
    assert len(ada_uses) == 1
    ada_links = [link for link in model["identity_links"]
                 if link["entity_id"] == ada_entity]
    assert {link["observation_id"] for link in ada_links} == {
        obs["id"] for obs in model["observations"]
        if obs["modality"] == "speech"
        and obs["payload"]["speaker_entity_id"] == ada_entity}
    assert any(mask["entity_id"] == ada_entity for mask in model["masks"])

    # Effects present: the drift targets the covered clip use over
    # that use's own timeline interval.
    moves = [row for row in model["effects"]
             if row["class_id"] == "move.slow_zoom_in"]
    assert len(moves) == 1
    assert moves[0]["target_id"] == first["id"]
    assert moves[0]["interval"] == first["timeline_interval"]

    # The frame map covers the reel's clip uses.
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == "map:reel-01")
    assert {seg["source_clip_use"] for seg in frame_map["segments"]} == {
        row["id"] for row in model["clip_uses"]}

    # The read path sees the same reel.
    shown = rpm.show(str(project), "Reel 01")
    assert shown["reels"][0]["uses"] == len(rows)
    assert shown["reels"][0]["a_roll"] == 2
    assert shown["reels"][0]["effects"] == 1
    assert shown["reels"][0]["speech"] == 2
    assert shown["reels"][0]["masks"] == 1


def test_partial_rebuild_keeps_the_model_current(tmp_path):
    """A second build for one reel replaces only that reel's rows."""
    project = tmp_path / "proj"
    project.mkdir()
    master = [_clip("video", 1, "A-Roll", "Ada", 100.0, 0.0, 30.0),
              _clip("audio", 1, "Speech", "Ada", 100.0, 0.0, 30.0)]
    transcript = _transcript()
    plan = {"video_tracks": [{"index": 1, "media_type": "video",
                              "role": "a_roll", "name": "A-Roll",
                              "occupant": "main"}],
            "audio_tracks": []}
    first_rows = serializable_placements(
        placements([(10.0, 15.0)], master, FPS))
    rpm.maintain_from_build(
        str(project),
        {"reel_placements": {"Reel 01": first_rows},
         "track_plans": {"Reel 01": plan}, "reel_fps": FPS},
        transcript=transcript)
    second_rows = serializable_placements(
        placements([(20.0, 26.0)], master, FPS))
    out = rpm.maintain_from_build(
        str(project),
        {"reel_placements": {"Reel 02": second_rows},
         "track_plans": {"Reel 02": plan}, "reel_fps": FPS},
        transcript=transcript)
    assert out["status"] == "committed"
    assert out["revision"] == 2
    from library.tools import project_model_store as store
    _revision, model = store.load_project_model(project)
    validate_project_model(model)
    assert {row["id"] for row in model["clip_uses"]
            if row["id"].startswith("use:reel-01:")}
    assert {row["id"] for row in model["clip_uses"]
            if row["id"].startswith("use:reel-02:")}
