"""Which part of the track plays is the model's decision, and it travels.

`compile_manifest` wrote `"source_in": 0.0` as a literal, so every track
played from its head whatever the model said - and step 2.04's frozen
handoff has asked for splices since it was written. That is AGENTS.md
10.2 exactly: a capability is only real where the renderer reads it.

These tests hold both halves:

  * the decision REACHES the manifest, and the beat grid is mapped
    through the same offset;
  * nothing in the pipeline chooses a section, scores one, or prefers
    one, and a selection that declares none plays from the head of the
    file as the ABSENCE of a decision.
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
STEP = REPO / "library" / "steps" / "step_2_04_music_selection"

from library.tools.beat_grid import (  # noqa: E402
    assert_music_offset_is_the_chosen_section,
    beat_positions,
)
from library.tools.music_measurement import track_sections  # noqa: E402
from library.tools.music_section import (  # noqa: E402
    UNDECLARED_SOURCE_IN,
    UNSUPPORTED_BY_THE_MEASUREMENTS,
    MusicSectionError,
    read_section,
    resolve_section,
    section_offset_seconds,
    validate_section,
)

TRACK = 198.6      # 001's Sickick instrumental
EDIT = 60.0


# ── Declared, and not declared ────────────────────────────────────────

def test_no_section_is_the_absence_of_a_decision():
    section = read_section({"title": "t", "audio_path": "/t.wav"})
    assert section.declared is False
    assert section.source_in == UNDECLARED_SOURCE_IN == 0.0
    assert "absence of a decision" in section.reason
    assert validate_section({"title": "t"}, TRACK, EDIT) == []


def test_a_declared_section_is_read_verbatim():
    section = read_section({"section": {"source_in": 60.0,
                                        "why": "the intro swings 31 dB"}})
    assert section.declared is True
    assert section.source_in == 60.0
    assert section.why == "the intro swings 31 dB"
    assert section.source_out(EDIT) == 120.0


@pytest.mark.parametrize("declared", [
    "60", ["60"], {"source_in": "sixty"}, {"source_in": None},
    {"source_in": True}, {"why": "no number"}, {"source_in": -3},
])
def test_a_malformed_section_raises(declared):
    """A number the placement depends on is not quietly read as zero."""
    with pytest.raises(MusicSectionError):
        read_section({"section": declared})


def test_a_section_past_the_end_of_the_track_is_refused():
    errors = validate_section({"section": {"source_in": 400.0}}, TRACK, EDIT)
    assert errors and "after the file ends" in errors[0]


def test_a_section_too_close_to_the_end_is_refused_with_the_arithmetic():
    errors = validate_section({"section": {"source_in": 180.0}}, TRACK, EDIT)
    assert len(errors) == 1
    assert "would be silent" in errors[0]
    # and it says what WOULD work, rather than only that this does not
    assert "138.6" in errors[0]


def test_the_last_playable_section_is_accepted():
    assert validate_section({"section": {"source_in": TRACK - EDIT}},
                            TRACK, EDIT) == []


def test_resolve_raises_rather_than_sliding_the_section_to_fit():
    """Moving the start is choosing which part plays."""
    with pytest.raises(MusicSectionError, match="silent"):
        resolve_section({"section": {"source_in": 180.0}}, TRACK, EDIT)
    section = resolve_section({"section": {"source_in": 60.0}}, TRACK, EDIT)
    assert section.source_in == 60.0


# ── One reading of the offset, and the grid moves with it ─────────────

def test_the_offset_has_one_reading():
    assert section_offset_seconds(None) == 0.0
    assert section_offset_seconds({}) == 0.0
    assert section_offset_seconds({"section": {"source_in": 12.5}}) == 12.5


def test_the_beat_grid_is_mapped_through_the_chosen_section():
    analysis = {"tempo": {"bpm": 120.0,
                          "beats": [round(0.37 + i * 0.5, 3)
                                    for i in range(32)]}}
    unmoved = beat_positions(analysis, None)
    moved = beat_positions(analysis, {"section": {"source_in": 5.0}})
    assert unmoved[0] == 0.37
    # The first beat at or after 5.0s is 5.37s into the file, and it is
    # 0.37s into the edit.
    assert moved[0] == 0.37
    assert all(t >= 0 for t in moved)
    # Beats before the section starts are not in the edit at all.
    assert moved == [round(t - 5.0, 4) for t in unmoved if t >= 5.0]
    assert len(moved) < len(unmoved)


def test_a_bed_placed_somewhere_other_than_the_chosen_section_raises():
    manifest = {"tracks": {"A2": {"clips": [
        {"source_in": 0.0, "timeline_in": 0.0}]}}}
    with pytest.raises(ValueError, match="section"):
        assert_music_offset_is_the_chosen_section(
            manifest, {"section": {"source_in": 60.0}})


def test_a_bed_that_does_not_start_the_timeline_raises():
    manifest = {"tracks": {"A2": {"clips": [
        {"source_in": 60.0, "timeline_in": 4.0}]}}}
    with pytest.raises(ValueError, match="timeline"):
        assert_music_offset_is_the_chosen_section(
            manifest, {"section": {"source_in": 60.0}})


# ── The decision reaches the manifest ─────────────────────────────────

def _compile_with(music_selection, tmp_path):
    """Drive the real compiler, with only its file loader stubbed."""
    a_roll = tmp_path / "vid1.mov"
    a_roll.write_text("dummy", encoding="utf-8")
    music = tmp_path / "bed.wav"
    music.write_text("dummy", encoding="utf-8")

    inputs = {
        "a_roll_assignments": [{
            "clip_id": "clip_1", "source_clip_id": "clip_1",
            "source_file": str(a_roll), "video_in": 10.317, "video_out": 70.317,
            "timeline_start": 0.0, "timeline_end": 60.0,
        }],
        "b_roll_assignments": [],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [],
        "enhancement_spec": [],
        "color_grade_spec": {},
        "audio_mix_spec": {},
        "music_selection": dict(music_selection, audio_path=str(music)),
        "audio_spine": {
            "structure": [{
                "block_type": "speech", "position": 1, "clip_id": "clip_1",
                "source_start": 10.317, "source_end": 70.317,
                "timeline_start": 0.0, "timeline_end": 60.0,
                "content": {"clip_id": "clip_1", "link_group_id": "lg_1"},
            }],
            "frame_rate": 30.0,
        },
        "clip_catalog": [{"clip_id": "clip_1", "path": str(a_roll),
                          "width": 1080, "height": 1920}],
        "semantic_analysis": {"semantic_analysis_documents": [{
            "clip_id": "clip_1",
            "analysis": {"motion": "The camera is mounted and still.",
                         "scene": "A speaker in a car."},
            "assessment": {"clip_type": "a-roll", "keywords": ["speaker"]},
        }]},
    }
    from library.steps.step_5_04_compile_manifest.step import compile_manifest
    from library.tools import music_audit_trail as audit
    # A resolved selection owes its audit sidecar: the pre-render check
    # refuses without it, so stage what 2.04's post-bridge writes on a
    # real run. The compile reads the project root off `out_dir`.
    audit.write_audit_trail(str(tmp_path), inputs["music_selection"])
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        return compile_manifest(str(tmp_path / "pipeline_output"))


def test_the_chosen_section_is_where_the_bed_is_placed(tmp_path):
    manifest = _compile_with(
        {"title": "bed", "duration_seconds": 198.6,
         "section": {"source_in": 60.0, "why": "the intro swings 31 dB"}},
        tmp_path)
    clip = manifest["tracks"]["A2"]["clips"][0]
    assert clip["source_in"] == 60.0
    assert clip["timeline_in"] == 0.0
    assert clip["source_out"] == pytest.approx(120.0)


def test_no_section_still_plays_from_the_head(tmp_path):
    manifest = _compile_with(
        {"title": "bed", "duration_seconds": 198.6}, tmp_path)
    clip = manifest["tracks"]["A2"]["clips"][0]
    assert clip["source_in"] == 0.0


def test_a_section_that_cannot_cover_the_timeline_fails_the_build(tmp_path):
    with pytest.raises(MusicSectionError, match="silent"):
        _compile_with({"title": "bed", "duration_seconds": 90.0,
                       "section": {"source_in": 60.0}}, tmp_path)


# ── What the model decides FROM, and what nothing decides for it ──────

def test_every_playable_section_is_measured_and_the_last_one_is_included():
    """One row per span that could play, including the tail."""
    per_second = [-20.0] * 199
    rows = track_sections(per_second, 60.0)
    assert [r["start_seconds"] for r in rows] == [0.0, 60.0, 120.0, 139.0]
    assert rows[-1]["end_seconds"] == 199.0
    for row in rows:
        assert set(row) == {"start_seconds", "end_seconds",
                            "mean_dbfs", "spread_db"}


def test_a_track_shorter_than_the_edit_has_no_playable_section():
    assert track_sections([-20.0] * 30, 60.0) == []


def test_the_rows_are_a_description_and_not_a_ranking():
    """No score, no rank, no recommendation, no order but time."""
    rows = track_sections(
        [-40.0] * 60 + [-14.0] * 60 + [-30.0] * 79, 60.0)
    assert [r["start_seconds"] for r in rows] == [0.0, 60.0, 120.0, 139.0]
    for row in rows:
        for banned in ("score", "rank", "best", "recommended", "preferred"):
            assert banned not in row
    # the loudest section is not first, and nothing says it is best
    assert rows[1]["mean_dbfs"] > rows[0]["mean_dbfs"]


BEST_SECTION_WORDS = ("best_section", "pick_section", "choose_section",
                      "select_section", "score_section", "rank_section",
                      "flattest", "steadiest")


def test_nothing_writes_a_best_section_rule():
    """A "best section" heuristic is a hardcoded creative value.

    Checked in the modules that could hold one: the resolver, the
    measurement, and both halves of the step.
    """
    for path in (REPO / "library" / "tools" / "music_section.py",
                 REPO / "library" / "tools" / "music_measurement.py",
                 STEP / "bridge.py",
                 STEP / "post_bridge.py"):
        body = path.read_text(encoding="utf-8").split('"""', 2)[-1]
        for word in BEST_SECTION_WORDS:
            assert word not in body, f"{path.name} names {word!r}"


def test_the_limits_of_the_measurements_are_recorded_rather_than_filled_in():
    assert "the shape of a non-zero section" in UNSUPPORTED_BY_THE_MEASUREMENTS
    for reason in UNSUPPORTED_BY_THE_MEASUREMENTS.values():
        assert len(reason) > 80


def test_the_model_is_asked_for_the_section_without_touching_the_frozen_prompt():
    """handoff.md is under a captain freeze; the schema comes from the
    manifest, and that is where the question is asked."""
    import json
    manifest = json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))
    asked = manifest["interface"]["llm_outputs"][0]["description"]
    assert "section (object" in asked
    assert "track_sections" in asked
    assert "your decision" in asked
    assert "section" in manifest["interface"]["outputs"][1]["expected_schema"]

    handoff = (STEP / "handoff.md").read_text(encoding="utf-8")
    assert "OUTPUT_SCHEMA: auto-injected from manifest.json" in handoff
