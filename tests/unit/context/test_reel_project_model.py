"""The reel-built Project Model: fragments, revisions and refusals.

Covers ``library/tools/reel_project_model.py`` without Resolve and
without a real project: every store lands under ``tmp_path``, and the
only media probed is the committed synthetic fixture.
"""
from __future__ import annotations

import json
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


def test_sec_to_ms_refuses_what_cannot_be_an_interval():
    assert rpm.sec_to_ms(1.0, 2.0) == (1000, 2000)
    assert rpm.sec_to_ms(2.0, 2.0) is None
    assert rpm.sec_to_ms(2.0, 1.0) is None
    assert rpm.sec_to_ms(float("nan"), 1.0) is None
    assert rpm.sec_to_ms("x", 1.0) is None


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
        "start": 10000, "end": 15000}
    assert video_use["timeline_interval"]["start"] == 0
    assert video_use["timeline_interval"]["end"] == 5000
    assert any(row["operation"] == "reel.assemble" for row in model["edits"])
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
                     "start": 10000, "end": 11000},
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
                     "start": 10000, "end": 15000},
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
    assert out["use_ids"] == ["use:reel-01:0000"]
    use = next(row for row in model["clip_uses"]
               if row["id"] == "use:reel-01:0000")
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
    assert [row["id"] for row in resolved["clip_uses"]] == [
        "use:reel-01:0000"]
    assert len(resolved["rendered_ranges"]) == 1
    assert (resolved["rendered_ranges"][0]["clip_use_id"]
            == "use:reel-01:0000")


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
                         "start": 10000, "end": 11000},
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
                     "start": 10000, "end": 12500},
        "modality": "event_span",
        "payload": {"predicate": "on_screen", "face_track": "track-1",
                    "voice_track": None},
        "confidence": None, "provenance_id": rpm.PROVENANCE_ID,
    })
    model["observations"].append({
        "id": "obs-on-screen-2", "asset_id": asset_id,
        "interval": {"clock_id": source_clock,
                     "start": 12500, "end": 15000},
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
               if row["id"] == "use:reel-01:0000")
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
            == render["settings"]["expected_frames"])
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
    assert out["use_ids"] == ["use:reel-01:0000", "use:reel-01:0001"]
    map_id = rpm.frame_map_for_reel(
        model, "reel-01", "clock:reel:reel-01", out["use_ids"])
    frame_map = next(row for row in model["frame_maps"]
                     if row["id"] == map_id)
    first, second = frame_map["segments"]
    assert first["from"]["clock_id"] != second["from"]["clock_id"]
    assert frame_map["from_clock"] == first["from"]["clock_id"]
    assert (first["from"]["start"], first["from"]["end"]) == (10000, 15000)
    assert (second["from"]["start"], second["from"]["end"]) == (20000, 25000)
    assert (first["to"]["start"], first["to"]["end"]) == (0, 5000)
    assert (second["to"]["start"], second["to"]["end"]) == (5000, 10000)
    # The second source's tick lands on the second render range.
    assert swap.map_tick(
        frame_map, second["from"]["clock_id"], 20000) == 5000
    assert swap.map_tick(
        frame_map, second["from"]["clock_id"], 24999) == 9999
    # Asked on the first clock, that tick refuses: on main it
    # answered with the second range against the wrong clock.
    with pytest.raises(swap.SwapbackRefused):
        swap.map_tick(frame_map, first["from"]["clock_id"], 20000)
    with pytest.raises(swap.SwapbackRefused):
        swap.map_tick(frame_map, "clock:src:no-such-source", 20000)
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
    assert (first["from"]["start"], first["from"]["end"]) == (10000, 15000)
    assert (second["from"]["start"], second["from"]["end"]) == (10000, 15000)
    assert swap.map_tick(
        frame_map, first["from"]["clock_id"], 12000) == 2000
    assert swap.map_tick(
        frame_map, second["from"]["clock_id"], 12000) == 7000
    validate_project_model(model)
