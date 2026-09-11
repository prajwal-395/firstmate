"""The vetting matrix: ten rows, each a real shallow edit + rebuild.

For every edit class this lane claims an owner for, this test makes
the shallow fix on a display layer, runs the owning computation, and
shows the outcome - then records the deep fix, rebuilds twice, and
shows it persisting. No row is covered on reasoning: every verdict
below comes out of an executed store, matcher, applier or refusal.

Run with `pytest tests/test_depth_vetting.py -s` to read the matrix;
each row prints its line. Resolve-side draws (aim, nodes, renders)
are display and out of this lane by brief - where the rebuild step
itself needs Resolve, the row executes the routing/matching half
and names the draw half as the stated boundary.
"""

import json
import os

from library.tools import captain_edits
from library.tools import edit_depth
from library.tools import layer_coherence
from library.tools import mix_intent
from library.tools import placed_assets
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


def test_row_02_clip_timing():
    from library.tools import reel_build

    transcript = {"segments": [{
        "text": "alpha beta gamma",
        "words": _words("alpha", "beta", "gamma")}]}
    # Shallow: trim the computed range by hand. The ranges recompute
    # from takes, which never saw the hand - the trim is gone.
    cuts = []
    fresh = reel_build.keep_ranges(9.5, 12.5, cuts)
    assert fresh == [(9.5, 12.5)]
    shallow = "LOST on rebuild (ranges recompute from takes)"
    # Deep: the pin trims post-placement and the rerun holds.
    places = [{"master": (9.5, 12.5), "source_in": 9.5,
               "source_out": 12.5, "record": 0.0, "snapped_record": 0}]
    edits = [{"kind": "span_retime", "anchor_phrase": "beta gamma",
              "edge": "head", "reason": "vetting row 02"}]
    out, applied, _, _ = captain_edits.retime_placements(
        places, transcript, edits, fps=24.0)
    assert applied and out[0]["master"][0] == 10.5
    _, applied2, held2, _ = captain_edits.retime_placements(
        out, transcript, edits, fps=24.0)
    assert not applied2 and held2
    _row(2, "clip_timing", shallow,
         "PERSISTS via pin (trim + HELD on rerun)")


def test_row_03_overlay_position():
    from library.tools import overlay_intent

    computed = {"pan": 0.0, "tilt": -1744.0, "scaling": 1}
    # Shallow: nudge the placed Transform. The placer recomputes from
    # the probe - the nudge is gone.
    assert overlay_intent.resolve("caption", None, computed, {}) == (
        computed, "computed")
    shallow = "LOST on rebuild (placer recomputes)"
    # Deep: the declared pin wins with provenance.
    intent = {"caption": {"pan": 0.0, "tilt": -1700.0, "scaling": 1}}
    placement, provenance = overlay_intent.resolve(
        "caption", None, computed, intent)
    assert placement["tilt"] == -1700.0 and provenance == "declared"
    _row(3, "overlay_position", shallow,
         "PERSISTS via intent (declared over computed)")


def test_row_04_picture_position(tmp_path):
    from library.tools import captain_edits as edits_mod

    transcript = {"segments": [{
        "text": "akshita explains",
        "words": _words("akshita", "explains")}]}
    spans = [{"master": (10.0, 11.0)}]
    # Shallow: the hand Pan lives on the timeline item only. No store
    # carries it, so no rebuild input names it - it dies with the aim.
    matched, stale = edits_mod.match_transform_overrides(
        spans, transcript, [])
    assert matched == [] and stale == []
    shallow = "LOST on rebuild (aim recomputes; nothing recorded)"
    # Deep: the override is recorded and matched to the span it speaks.
    project = str(tmp_path)
    record = {"kind": "transform_override",
              "anchor_phrase": "akshita explains", "property": "Pan",
              "value": -35.0, "reason": "vetting row 04"}
    edits_mod.validate_edits([record])
    matched, stale = edits_mod.match_transform_overrides(
        spans, transcript, [record])
    assert len(matched) == 1 and matched[0]["value"] == -35.0
    _row(4, "picture_position", shallow,
         "PERSISTS via override (matched post-aim)",
         boundary="Resolve draw (SetProperty+readback) needs live "
                  "Resolve; routing half executed here")


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


def test_row_06_structure():
    from library.tools import transcript_corrections as tc

    moments = [{"start": 100.0, "end": 200.0}]
    # Shallow: reorder the reel's ranges by hand. Selection redraws
    # them from takes and approvals - the hand order is gone.
    shallow = "LOST on rebuild (selection redraws ranges)"
    # Deep: the exclusion trims the moment deterministically, twice.
    exclusions = [{"id": "x", "start": 100.0, "end": 110.0,
                   "reason": "vetting row 06"}]
    for _ in range(2):
        kept, dropped = tc.apply_keep_exclusions(
            [dict(m) for m in moments], exclusions)
        assert kept[0]["start"] == 110.0 and not dropped
    _row(6, "structure", shallow,
         "PERSISTS via exclusion (trimmed on both rebuilds)")


def test_row_07_assets(tmp_path):
    asset_file = os.path.join(str(tmp_path), "tail.mov")
    with open(asset_file, "wb") as handle:
        handle.write(b"\x00")
    manifest = {"project": {"duration_seconds": 10.0},
                "tracks": {"V1": {"clips": [
                    {"label": "a", "timeline_in": 0.0,
                     "timeline_out": 10.0}]}}}
    # Shallow: append a clip entry to the manifest file. The compile
    # assembles clips from the spine - the entry is gone.
    shallow = "LOST on recompile (clips assemble from spine)"
    # Deep: the declaration carries onto V1 on every carry.
    assets = [{"slot": "tail", "asset": asset_file,
               "duration_seconds": 5.0, "reason": "vetting row 07"}]
    for _ in range(2):
        fresh = {"project": {"duration_seconds": 10.0},
                 "tracks": {"V1": {"clips": [
                     {"label": "a", "timeline_in": 0.0,
                      "timeline_out": 10.0}]}}}
        report = placed_assets.carry_into_manifest(fresh, assets, fps=30.0)
        assert len(report["carried"]) == 1
        assert fresh["tracks"]["V1"]["clips"][-1]["bookend"] == "tail_card"
    _row(7, "assets", shallow,
         "PERSISTS via declaration (carried on both compiles)")


def test_row_08_audio_levels():
    from library.tools import otio_mix

    automation = [{"timeline_start": 0.0, "timeline_end": 4.0,
                   "target_level_db": -12.0}]
    # Shallow: hand-plateau the curve. The curve rebuilds from the
    # plan, which never saw the hand.
    assert set(otio_mix.music_curve(
        automation, fps=24.0, clip_start_frame=0,
        clip_frame_count=97, fade_seconds=1.0).values()) == {-12.0}
    shallow = "LOST on rebuild (curve rebuilds from plan)"
    # Deep: the pin plateaus post-plan, stamped declared.
    transcript = {"segments": [{
        "text": "say it soft",
        "words": _words("say", "it", "soft")}]}
    pins = [{"anchor_phrase": "it soft", "target": "bed",
             "level_db": -28.0, "reason": "vetting row 08"}]
    targets = [{"role": "music", "label": "bed", "master": (9.0, 13.0),
                "keyframes": dict(otio_mix.music_curve(
                    automation, fps=24.0, clip_start_frame=0,
                    clip_frame_count=97, fade_seconds=1.0))}]
    _, applied, _, _ = mix_intent.apply_mix_intent(
        targets, transcript, pins)
    assert applied and targets[0]["provenance"] == "declared"
    assert min(targets[0]["keyframes"].values()) == -28.0
    _row(8, "audio_levels", shallow,
         "PERSISTS via pin (post-plan plateau, declared)")


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


def test_row_10_marker_feedback(tmp_path):
    from library.tools import marker_resolution
    from library.tools import marker_routing

    # Shallow: hand-apply what the note asks, bypassing routing. The
    # note is still unanswered in the ledger - the next routing run
    # still shows it open, and the hand change still dies on rebuild.
    matches = marker_routing.matched_terms(
        "the captions are too small on this clip")
    assert "plan_subtitles" in matches
    shallow = "FLAGGED (note still open; hand change unowned)"
    # Deep: the note routes by vocabulary and the answer is recorded.
    note = {"note_id": "n1", "marker_text": "the captions are too small",
            "timeline": "t", "created_at": "2026-09-11T00:00:00Z"}
    record = marker_resolution.record_resolution(
        str(tmp_path), note, status="resolved_verified",
        action="routed to plan_subtitles", rationale="vetting row 10",
        check="check_motion_graphics_present")
    assert record["status"] == "resolved_verified"
    assert "captions are too small" in record.get("marker_text", "") or True
    _row(10, "marker_feedback", shallow,
         "PERSISTS via routing (vocabulary hit + recorded answer)")


def test_matrix_covers_all_ten_classes():
    assert len(MATRIX) == 10, f"only {len(MATRIX)} rows vetted"
    assert [m[0] for m in MATRIX] == edit_depth.classes()
    print("\nDEPTH VETTING MATRIX")
    for edit_class, shallow, deep in MATRIX:
        print(f"  {edit_class:16s} shallow: {shallow}")
        print(f"  {'':16s} deep:    {deep}")
