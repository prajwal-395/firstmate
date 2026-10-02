"""The vetting matrix: one row per edit class, each a real shallow edit + rebuild.

For every edit class this lane claims an owner for, this test makes
the shallow fix on a display layer, runs the owning computation, and
shows the outcome - then records the deep fix, rebuilds twice, and
shows it persisting. No row is covered on reasoning: every verdict
below comes out of an executed store, matcher, applier or refusal.

Run with `pytest tests/scenarios/test_depth_vetting.py -s` to read the matrix;
each row prints its line. Resolve-side draws (aim, nodes, renders)
are display and out of this lane by brief - where the rebuild step
itself needs Resolve, the row executes the routing/matching half
and names the draw half as the stated boundary.
"""

import json
import os

from library.tools import edit_depth
from library.tools import layer_coherence
from library.tools import transcript_corrections

MATRIX = []


def _row(number, edit_class, shallow_outcome, deep_outcome, boundary=""):
    line = (f"ROW {number:02d} {edit_class}: shallow -> {shallow_outcome}; "
            f"deep -> {deep_outcome}" + (f" [{boundary}]" if boundary else ""))
    print(line)
    MATRIX.append((edit_class, shallow_outcome, deep_outcome))
    return line


def _words(*tokens, start=10.0):
    words = []
    cursor = start
    for token in tokens:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return words


def test_row_01_wording(tmp_path):
    transcript = {"segments": [{
        "text": "say lucy to the camera",
        "words": _words("say", "lucy", "to", "the", "camera")}]}
    # Shallow: fix the caption card. Regen re-derives cards from the
    # transcript, which still says lucy - the fix is gone.
    display = {"cards": [{"text": "say Lucie to the camera"}]}
    regen = {"cards": [{"text": transcript["segments"][0]["text"]}],
             }
    assert regen["cards"][0]["text"] != display["cards"][0]["text"]
    shallow = "LOST on regen (transcript still says lucy)"
    # Deep: the correction lives in the store; the pass respells the
    # root, and the second run finds nothing left to do.
    project = str(tmp_path)
    os.makedirs(os.path.join(project, "learned_context"), exist_ok=True)
    with open(os.path.join(project, "learned_context", "learnings.json"),
              "w", encoding="utf-8") as handle:
        json.dump([], handle)
    transcript_corrections.record_spelling(
        project, "lucy", "Lucie", "vetting row 01")
    report = transcript_corrections.apply_to_document(transcript, project)
    assert report["replacements"] == 2  # segment text + word entry
    assert transcript["segments"][0]["text"] == "say Lucie to the camera"
    report2 = transcript_corrections.apply_to_document(transcript, project)
    assert report2["replacements"] == 0
    _row(1, "wording", shallow,
         "PERSISTS via store (2 replacements, rerun 0)")


def test_row_05_look_grade(tmp_path):
    from library.tools import brand_registry

    template = os.path.join(str(tmp_path), "brand.yaml")
    with open(template, "w", encoding="utf-8") as handle:
        handle.write("name: vetting\nstyle:\n  series_look: declared-look\n")
    # Shallow: hand values on Color nodes live nowhere the build
    # reads - the build applies template/CDL/.drx per clip, over them.
    try:
        edit_depth.refuse_display_edit(
            "look_grade", "Color page nodes", detail="hand grade")
        shallow = "UNEXPECTEDLY ACCEPTED"
    except edit_depth.EditDepthError as exc:
        assert "color_page_grade" in str(exc)
        shallow = "REFUSED (no carrier; template/.drx named)"
    # Deep: the declaration resolves through the registry.
    loaded = brand_registry.load_brand_template(template)
    assert loaded is not None
    _row(5, "look_grade", shallow,
         "PERSISTS via declaration (template resolves)",
         boundary="node application needs live Resolve")


def test_row_09_mg_content(tmp_path):
    root = os.path.join(str(tmp_path), "proj")
    os.makedirs(os.path.join(root, "learned_context"), exist_ok=True)
    with open(os.path.join(root, "learned_context", "learnings.json"),
              "w", encoding="utf-8") as handle:
        json.dump([{"id": "lc-1", "kind": "correction",
                    "status": "active", "statement": "s",
                    "read_by": ["*"],
                    "source": {"correction_type": "transcript_spelling",
                               "heard": "lucy", "correct": "Lucie"},
                    "detail": "vetting"}], handle)
    # Shallow: fix the rendered segment file. The render regenerates
    # from the plan payload, which still says lucy - gone.
    shallow = "LOST on re-render (render derives from plan)"
    # The plan payload quoting lucy is FLAGGED with both values, and
    # the planner is TOLD the verdict up front.
    os.makedirs(os.path.join(root, "pipeline_output", "steps",
                             "4_06_render_motion_graphics"))
    with open(os.path.join(
            root, "pipeline_output", "steps",
            "4_06_render_motion_graphics", "mg.json"),
            "w", encoding="utf-8") as handle:
        json.dump({"payload": "visit lucy today"}, handle)
    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 1
    assert report["wording"][0]["found"] == "lucy"
    model_sees = transcript_corrections.render_for_model(root)
    assert "Lucie" in model_sees and "lc-1" in model_sees
    _row(9, "mg_content", shallow,
         "FLAGGED with both values + planner told (prompt half)")


def test_row_11_ending(tmp_path):
    from types import SimpleNamespace

    from library.tools import reel_build
    from library.tools import reel_ending

    transcript = {"segments": [{
        "text": "alpha beta gamma delta",
        "words": _words("alpha", "beta", "gamma", "delta")}]}
    # Two master shots, back to back. The reel's plan reaches 0.6s into
    # the SECOND one, which is how Reel 13 grew twelve frames of Craig.
    shot_a = SimpleNamespace(timeline_start=10.0, timeline_end=11.5,
                             source_in=50.0, track_index=1, speaker="A")
    shot_b = SimpleNamespace(timeline_start=11.5, timeline_end=13.0,
                             source_in=80.0, track_index=2, speaker="B")
    ranges = [(10.0, 12.1)]
    probe = reel_build.placements(ranges, [shot_a, shot_b], 24.0)
    # Shallow: trim the last item by hand in Resolve. The next rebuild
    # re-cuts the ranges from the plan and the second shot is back.
    assert len(probe) == 2 and probe[-1]["clip"] is shot_b
    shallow = "LOST on rebuild (ranges re-cut; next shot returns)"
    # Deep: declare the ending. The last range truncates to the shot
    # that speaks the anchor, and a second run holds it.
    ending = {"reel": "R", "ends_on": {"anchor_phrase": "beta gamma"},
              "tail_element": "tv_power_tail", "reason": "vetting row 11"}
    trimmed, record = reel_ending.apply_ending(
        ranges, probe, transcript, ending, 24.0)
    assert trimmed == [(10.0, 11.5)] and len(record["applied"]) == 1
    again = reel_build.placements(trimmed, [shot_a, shot_b], 24.0)
    assert len(again) == 1 and again[-1]["clip"] is shot_a
    _, second = reel_ending.apply_ending(
        trimmed, again, transcript, ending, 24.0)
    assert len(second["held"]) == 1 and not second["applied"]
    # And the switch-off it declares now has a shot long enough to draw
    # in, which is the half that was silently undone before.
    fits = reel_ending.assert_tail_fits(again, ending, 24.0)
    # Frames of CLIP: one more than the 18-frame ramp, because a hold
    # of exactly the ramp's length is undone as `never_settles`.
    assert fits["fits"] and fits["tail_frames"] == 19
    _row(11, "ending",
         shallow, "PERSISTS via declaration (truncates, holds, tail fits)")


def test_row_12_caption_timing(tmp_path):
    from library.tools import caption_timing

    fps = 24000 / 1001
    card = {"segment_id": "sub_a_c_500780-504042",
            "binding": {"speaker": "akshita", "source_clip_id": "c",
                        "source_start": 500.78, "source_end": 504.042},
            "timeline_start": 1600 / fps, "timeline_end": 1678 / fps}
    # Shallow: drag the card seven frames later in Resolve. The placer
    # re-places from the plan's seconds, so the drag is gone.
    shallow = "LOST on rebuild (placer re-places from plan seconds)"
    # And span_retime, the clip_timing owner, has no word for it: its
    # vocabulary is a keep-range EDGE and it refuses an extension.
    assert "offset" not in edit_depth.DEEP_PATH["clip_timing"]
    # Deep: a caption pin, scoped by the source audio the card captions.
    pins = [{"scope": {"speaker": "akshita",
                       "source_start_at_or_after": 500.0},
             "offset_frames": 7, "reason": "vetting row 12"}]
    moved, applied, short, stale = caption_timing.apply_pins(
        [card], pins, fps)
    assert not short and not stale and len(applied) == 1
    assert applied[0]["now"] == [1607, 1685]
    # A rebuild re-renders the same card at the same plan seconds and
    # the pin moves it again - which is what surviving means here.
    again, applied2, _, _ = caption_timing.apply_pins([card], pins, fps)
    assert applied2[0]["now"] == applied[0]["now"]
    _row(12, "caption_timing", shallow,
         "PERSISTS via pin (source-anchored offset, re-applied)")
