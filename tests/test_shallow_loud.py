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


class _SimpleShot:
    """The two fields `reel_build.placements` reads off a master clip."""

    def __init__(self, timeline_start, timeline_end, track_index=1,
                 speaker="A", source_in=50.0):
        self.timeline_start = timeline_start
        self.timeline_end = timeline_end
        self.track_index = track_index
        self.speaker = speaker
        self.source_in = source_in
        self.track_type = "video"
        self.source_file = "/f/shot.mov"


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


@pytest.mark.parametrize("edit_class,relpath", [
    ("clip_timing", os.path.join("pipeline_output", "steps",
                                 "5_04_compile_manifest",
                                 "assembly_manifest.json")),
])
def test_row_hand_edit_flags_with_both_values_and_deep_path(
        tmp_path, capsys, edit_class, relpath):
    """B1 collapse: the four row-flag tests share one helper and one
    property, so one parametrized test. Each case still calls `_row`,
    which is what `test_matrix_covers_every_edit_class` counts."""
    root = str(tmp_path)
    _drift_flagged(root, relpath, edit_class, capsys)
    _row(edit_class,
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


def test_row_ending_refuses_an_element_that_cannot_draw(tmp_path):
    """No file display carries a reel's ending - a hand trim of the last
    timeline item dies on rebuild with nothing to fingerprint. What IS
    loud is the build: an element the ending shot cannot hold refuses by
    name with both counts, and an anchor whose words the reel no longer
    plays reports STALE rather than silently changing nothing."""
    from library.tools import reel_build
    from library.tools import reel_ending

    ending = {"reel": "R", "ends_on": {"anchor_phrase": "beta gamma"},
              "tail_element": "tv_power_tail", "reason": "vetting"}
    with pytest.raises(reel_ending.TailElementHasNoRoom) as refusal:
        reel_ending.assert_tail_fits(
            [{"source_in": 0.0, "source_out": 12 / 24.0}], ending, 24.0)
    message = str(refusal.value)
    assert "19 frames" in message and "plays 12" in message
    assert "tail_hold: freeze" in message

    transcript = {"segments": [{
        "text": "alpha beta gamma",
        "words": [{"word": w, "start": 10.0 + i * 0.5,
                   "end": 10.4 + i * 0.5, "timed": True}
                  for i, w in enumerate(("alpha", "beta", "gamma"))]}]}
    shot = _SimpleShot(10.0, 11.5)
    ranges = [(10.0, 11.5)]
    probe = reel_build.placements(ranges, [shot], 24.0)
    _, record = reel_ending.apply_ending(
        ranges, probe, transcript,
        {**ending, "ends_on": {"anchor_phrase": "words never said"}}, 24.0)
    assert len(record["stale"]) == 1
    assert "STALE" in record["stale"][0]["reason"]
    assert edit_depth.reachability("ending")["reachable"] is True
    _row("ending",
         "REFUSED (element with no room names both counts) + STALE "
         "(anchor the reel no longer plays)")


def test_row_caption_timing_refuses_and_reports_stale():
    """Same shape: a hand-dragged caption card has no file carrier, and
    the placer re-places from the plan's seconds. What IS loud is the
    pin - one matching no card reports STALE, one trimming a card out of
    existence refuses."""
    from library.tools import caption_timing

    card = {"segment_id": "sub_a_c_500780-504042",
            "binding": {"speaker": "akshita", "source_clip_id": "c",
                        "source_start": 500.78, "source_end": 504.042},
            "timeline_start": 1600 / 24.0, "timeline_end": 1678 / 24.0}
    _, applied, _, stale = caption_timing.apply_pins(
        [card], [{"scope": {"speaker": "craig"}, "offset_frames": 3,
                  "reason": "words that moved"}], 24.0)
    assert not applied and len(stale) == 1
    assert "STALE" in stale[0]["reason"]
    with pytest.raises(caption_timing.CaptionTimingError) as refusal:
        caption_timing.apply_pins(
            [card], [{"scope": {"source_start": 500.78},
                      "head_frames": 999, "reason": "too far"}], 24.0)
    assert "draws nothing" in str(refusal.value)
    assert edit_depth.reachability("caption_timing")["reachable"] is True
    _row("caption_timing",
         "STALE (pin matching no card) + REFUSED (pin that would trim a "
         "card out of existence)")


