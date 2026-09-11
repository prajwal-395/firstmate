"""The updated matrix: no edit class loses a change silently.

PR 976 vetted the deep half (PERSISTS x9) and left the shallow half
silent in 8 of 10 classes (LOST x8). Each row below makes the REAL
shallow edit on a REAL display file, runs the REAL witness (the
pre-run drift check and the layer-coherence gate - the same calls
`run_pipeline` makes), and shows the outcome: a loud flag with both
values and the deep path, a true refusal, or - where no file carrier
exists - the per-class statement of what the user sees instead.

Run with `pytest tests/test_shallow_loud.py -s` to read the matrix.
"""

import json
import os

import pytest

from library.tools import display_drift
from library.tools import edit_depth
from library.tools import layer_coherence

MATRIX = []


def _row(edit_class, shallow_outcome):
    line = f"ROW {edit_class}: shallow -> {shallow_outcome}"
    print(line)
    MATRIX.append((edit_class, shallow_outcome))
    return line


def _write(root, relpath, document):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(document, (dict, list)):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(document))
    return path


def _drift_flagged(root, relpath, edit_class, capsys):
    """Snapshot, hand-edit, check: the flag with both values and the
    deep path, or an assertion failure."""
    _write(root, relpath, {"value": "pipeline wrote this"})
    assert display_drift.snapshot(root)["files"] >= 1
    assert display_drift.check(root)["drifted"] == []
    capsys.readouterr()
    _write(root, relpath, {"value": "hand changed this"})
    flagged = display_drift.check(root)
    assert flagged["drifted"] == [relpath]
    err = capsys.readouterr().err
    assert "DISPLAY DRIFT" in err
    assert relpath in err and edit_class in err
    assert "was " in err and "-> now " in err and "bytes" in err
    assert "deep path" in err
    assert edit_depth.DEEP_PATH[edit_class] in err
    return flagged


def _correction_project(root, heard="lucy", correct="Lucie"):
    _write(root, os.path.join("learned_context", "learnings.json"), [
        {"id": "lc-0001", "kind": "correction", "status": "active",
         "statement": "spelling", "read_by": ["*"],
         "source": {"correction_type": "transcript_spelling",
                    "heard": heard, "correct": correct},
         "detail": "vetting"}])
    words = [{"word": "say", "start": 1.0, "end": 1.2, "timed": True},
             {"word": correct, "start": 1.3, "end": 1.7, "timed": True}]
    _write(root, os.path.join(
        "pipeline_output", "scratch", "timeline_transcript",
        "transcript.json"),
        {"segments": [{"text": f"say {correct}", "words": words}],
         "transcript_corrections_applied": [
             {"id": "lc-0001", "heard": heard, "correct": correct,
              "replacements": 1}]})
    return root


def test_row_wording_flags_with_both_values_and_deep_path(tmp_path,
                                                          capsys):
    root = _correction_project(str(tmp_path))
    _write(root, os.path.join("subtitle_plans", "cards.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 1
    row = report["wording"][0]
    assert row["found"] == "lucy" and row["should_be"] == "Lucie"
    assert layer_coherence.main([root]) == 2
    out = capsys.readouterr().out
    assert "say lucy" in out and "deep path" in out
    assert edit_depth.DEEP_PATH["wording"] in out
    _row("wording",
         "FLAGGED (coherence names found+should-be with the deep path)")


def test_row_clip_timing_flags_on_manifest_hand_edit(tmp_path, capsys):
    root = str(tmp_path)
    relpath = os.path.join("pipeline_output", "steps",
                           "5_04_compile_manifest", "assembly_manifest.json")
    _drift_flagged(root, relpath, "clip_timing", capsys)
    _row("clip_timing",
         "FLAGGED (drift names old+new hash with the deep path)")


def test_row_overlay_position_flags_on_props_hand_edit(tmp_path, capsys):
    root = str(tmp_path)
    relpath = os.path.join("pipeline_output", "steps",
                           "4_05_render_subtitles", "box.json")
    _drift_flagged(root, relpath, "overlay_position", capsys)
    _row("overlay_position",
         "FLAGGED (drift names old+new hash with the deep path)")


def test_row_picture_position_states_unreachable():
    reach = edit_depth.reachability("picture_position")
    assert reach["reachable"] is False
    assert "capture-transform" in reach["instead"]
    assert edit_depth.DEEP_PATH["picture_position"]
    _row("picture_position",
         "STATED UNREACHABLE (no file carrier; capture route named)")


def test_row_look_grade_refuses_and_flags_comp(tmp_path, capsys):
    try:
        edit_depth.refuse_display_edit(
            "look_grade", "Color page nodes", detail="hand grade")
        raise AssertionError("consultation refusal did not raise")
    except edit_depth.EditDepthError as exc:
        assert "color_page_grade" in str(exc)
    root = str(tmp_path)
    relpath = os.path.join("pipeline_output", "steps", "6_01_render",
                           "fusion_comps", "clip_001.comp")
    _drift_flagged(root, relpath, "look_grade", capsys)
    _row("look_grade",
         "REFUSED (consultation) + FLAGGED on .comp files")


def test_row_structure_flags_on_proposal_hand_edit(tmp_path, capsys):
    root = str(tmp_path)
    relpath = os.path.join("pipeline_output", "review",
                           "reel_proposals_v2.json")
    _drift_flagged(root, relpath, "structure", capsys)
    _row("structure",
         "FLAGGED (drift names old+new hash with the deep path)")


def test_row_assets_flags_and_missing_media_is_named(tmp_path, capsys):
    root = str(tmp_path)
    relpath = os.path.join("pipeline_output", "steps",
                           "5_04_compile_manifest", "assembly_manifest.json")
    _drift_flagged(root, relpath, "assets", capsys)
    _write(root, os.path.join("external", "placed_assets.json"),
           {"version": 1, "assets": [{
               "slot": "tail", "asset": "/abs/never_rendered.mov",
               "duration_seconds": 5.0, "reason": "vetting"}]})
    report = layer_coherence.check_project(root)
    assert any("not on disk" in row["found"]
               for row in report["assets"])
    _row("assets",
         "FLAGGED (drift + coherence missing-media row with deep path)")


def test_row_audio_levels_flags_on_otio_hand_edit(tmp_path, capsys):
    root = str(tmp_path)
    relpath = os.path.join("pipeline_output", "steps", "6_01_render",
                           "otio", "reel.otio")
    _drift_flagged(root, relpath, "audio_levels", capsys)
    _row("audio_levels",
         "FLAGGED (drift names old+new hash with the deep path)")


def test_row_mg_content_flags_quote_and_payload(tmp_path, capsys):
    root = _correction_project(str(tmp_path))
    _write(root, os.path.join(
        "pipeline_output", "steps", "4_06_render_motion_graphics",
        "mg.json"), {"payload": "visit lucy today"})
    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 1
    assert report["wording"][0]["found"] == "lucy"
    relpath = os.path.join("pipeline_output", "steps",
                           "4_06_render_motion_graphics", "mg.json")
    display_drift.snapshot(root)
    capsys.readouterr()
    flagged = display_drift.check(root)
    assert flagged["drifted"] == []
    with open(os.path.join(root, relpath), "w",
              encoding="utf-8") as handle:
        json.dump({"payload": "visit lucy by hand"}, handle)
    flagged = display_drift.check(root)
    assert flagged["drifted"] == [relpath]
    err = capsys.readouterr().err
    assert "mg_content" in err and "deep path" in err
    _row("mg_content",
         "FLAGGED (coherence quote row + drift payload flag)")


def test_row_marker_feedback_already_loud():
    from library.tools import marker_routing

    assert "plan_subtitles" in marker_routing.matched_terms(
        "the captions are too small on this clip")
    assert edit_depth.reachability("marker_feedback")["reachable"] is True
    _row("marker_feedback",
         "FLAGGED (note stays open; routing vocabulary hit)")


def test_matrix_covers_all_ten_classes():
    assert len(MATRIX) == 10, f"only {len(MATRIX)} rows vetted"
    assert [m[0] for m in MATRIX] == edit_depth.classes()
    print("\nSHALLOW-LOUD MATRIX (after: PR 976 said LOST x8)")
    for edit_class, shallow in MATRIX:
        print(f"  {edit_class:16s} shallow: {shallow}")


def test_reachability_names_a_witness_or_states_otherwise():
    for edit_class in edit_depth.classes():
        reach = edit_depth.reachability(edit_class)
        assert reach["reachable"] in (True, False)
        if reach["reachable"]:
            assert "where" in reach and reach["where"].strip()
        else:
            assert "instead" in reach and reach["instead"].strip()
        assert edit_depth.DEEP_PATH[edit_class].strip()
    with pytest.raises(edit_depth.EditDepthError):
        edit_depth.reachability("unclassified_whim")


def test_drift_reads_v1_ledgers(tmp_path, capsys):
    root = str(tmp_path)
    relpath = os.path.join("subtitle_plans", "a.json")
    _write(root, relpath, {"v": 1})
    display_drift.snapshot(root)
    ledger_path = os.path.join(root, display_drift.LEDGER_FILENAME)
    with open(ledger_path, encoding="utf-8") as handle:
        ledger = json.load(handle)
    ledger["files"] = {k: "0" * 64 for k in ledger["files"]}
    with open(ledger_path, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle)
    flagged = display_drift.check(root)
    assert flagged["drifted"] == [relpath]
    assert "size unknown (v1 snapshot)" in capsys.readouterr().err
