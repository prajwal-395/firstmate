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
