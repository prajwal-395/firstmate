"""The three orphan owners are consulted by the builders.

`span_retime`, `mix_intent` and `placed_assets` existed as stores,
matchers and appliers that no builder called - a declared-but-never-
enforced shape. Each test below runs the builder-side consultation
against a real shallow/deep pair and shows the verdict: a rebuild
that honours the declaration, and a rebuild without one that is byte
identical to before. No Resolve, no real project: every fixture is
synthetic under `tmp_path` (AGENTS.md 8).
"""

import inspect
import json
import os

import pytest

from library.tools import captain_edits
from library.tools import mix_intent
from library.tools import placed_assets


def _words(*tokens, start=10.0):
    words = []
    cursor = start
    for token in tokens:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return words


def _transcript():
    return {"segments": [{
        "text": "alpha beta gamma delta",
        "timeline_start": 10.0, "timeline_end": 12.0,
        "source_start": 50.0, "source_file": "/v/a.mov",
        "words": _words("alpha", "beta", "gamma", "delta")}]}


# ── span_retime: the ranges seam ───────────────────────────────────

def test_retime_ranges_trims_head_before_anything_derives():
    transcript = _transcript()
    spans = [{"master": (10.0, 12.0)}]
    edits = [{"kind": "span_retime", "anchor_phrase": "beta gamma",
              "edge": "head", "reason": "trim test"}]
    ranges, applied, held, stale = captain_edits.retime_ranges(
        [(10.0, 12.0)], spans, transcript, edits, fps=24.0)
    assert ranges == [(10.5, 12.0)]
    assert len(applied) == 1 and not held and not stale
    # The probe is evidence, not the build: the caller's spans stand.
    assert spans[0]["master"] == (10.0, 12.0)


def test_retime_ranges_splits_on_interior_trim():
    transcript = _transcript()
    spans = [{"master": (10.0, 11.0)}, {"master": (11.0, 12.0)}]
    edits = [{"kind": "span_retime", "anchor_phrase": "delta",
              "edge": "head", "reason": "interior trim"}]
    ranges, applied, _, stale = captain_edits.retime_ranges(
        [(10.0, 12.0)], spans, transcript, edits, fps=24.0)
    assert not stale and len(applied) == 1
    # Removed seconds stay removed: two ranges, not one bridged whole.
    assert ranges == [(10.0, 11.0), (11.5, 12.0)]


def test_retime_ranges_holds_on_rerun_and_noops_without_pins():
    transcript = _transcript()
    spans = [{"master": (10.0, 12.0)}]
    edits = [{"kind": "span_retime", "anchor_phrase": "beta gamma",
              "edge": "head", "reason": "rerun test"}]
    trimmed, _, _, _ = captain_edits.retime_ranges(
        [(10.0, 12.0)], spans, transcript, edits, fps=24.0)
    rerun_spans = [{"master": trimmed[0]}]
    _, applied2, held2, stale2 = captain_edits.retime_ranges(
        trimmed, rerun_spans, transcript, edits, fps=24.0)
    assert not applied2 and not stale2 and len(held2) == 1
    # No pins: the ranges pass through untouched.
    same, applied, held, stale = captain_edits.retime_ranges(
        [(10.0, 12.0)], spans, transcript, [], fps=24.0)
    assert same == [(10.0, 12.0)] and not (applied or held or stale)


def test_retime_ranges_matches_placement_rebuild():
    """The seam proof: trimming ranges, then placing, plays the same
    masters as placing, then trimming - so captions planned from the
    trimmed ranges describe the trimmed picture."""
    from types import SimpleNamespace

    from library.tools import reel_build

    transcript = _transcript()
    clips = [SimpleNamespace(timeline_start=10.0, timeline_end=12.0,
                             source_in=50.0, track_index=1,
                             speaker="A")]
    edits = [{"kind": "span_retime", "anchor_phrase": "beta gamma",
              "edge": "head", "reason": "seam test"}]
    probe = reel_build.placements([(10.0, 12.0)], clips, 24.0)
    assert probe[0]["master"] == (10.0, 12.0)
    trimmed, applied, _, _ = captain_edits.retime_ranges(
        [(10.0, 12.0)], probe, transcript, edits, fps=24.0)
    assert len(applied) == 1
    placed_after = reel_build.placements(trimmed, clips, 24.0)
    assert placed_after[0]["master"] == (10.5, 12.0)
    assert placed_after[0]["source_in"] == pytest.approx(50.5)


def test_builder_threading_names_ranges():
    """`build_reel_timeline` takes precomputed (trimmed) ranges instead
    of recomputing them from the moment - the static half of the seam
    (the two call sites pass `ranges=ranges`; the default preserves
    every other caller)."""
    from library.tools import reel_build

    assert "ranges" in inspect.signature(
        reel_build.build_reel_timeline).parameters


# ── placed_assets: the compile seam ────────────────────────────────

def _compile_fixture(root, media):
    # `compile_manifest(out_dir)` reads step outputs directly out of
    # `out_dir` and the project root beside it (`_project_root =
    # dirname(out_dir)`), which is the orchestrator's shape
    # (`write_dir(Area.OUTPUT_ROOT)`).
    out = os.path.join(root, "pipeline_output")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "mesh_spine.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"audio_spine": {
            "structure": [{
                "position": "hook", "timeline_start": 0.0,
                "timeline_end": 2.333, "duration_seconds": 2.333,
                "block_type": "hook", "source_start": 10.123,
                "source_end": 12.456, "clip_id": "c1",
                "content": {"text": "say it"}}],
            "frame_rate": 30.0}}, handle)
    with open(os.path.join(out, "assign_aroll.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"hook_assignment": {
            "spine_block_position": "hook", "clip_id": "c1"},
            "a_roll_assignments": []}, handle)
    with open(os.path.join(out, "catalog.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"project_fps": 30.0, "clip_catalog": [{
            "clip_id": "c1", "path": media, "width": 1920,
            "height": 1080}]}, handle)
    with open(os.path.join(out, "plan_subtitles.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"subtitles": [{
            "id": "sub_001", "timeline_start": 0.0, "timeline_end": 0.7,
            "text": "say it", "words": [
                {"word": "say", "start": 0.0, "end": 0.3}]}]}, handle)
    return out


def _declare(root, assets):
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    with open(os.path.join(external, "placed_assets.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"version": 1, "assets": assets}, handle)


def test_compile_carries_declared_tail_card(tmp_path):
    from library.steps.step_5_04_compile_manifest.step import (
        compile_manifest)

    root = str(tmp_path)
    media = os.path.join(root, "a.mov")
    with open(media, "wb") as handle:
        handle.write(b"\x00")
    tail = os.path.join(root, "tail.mov")
    with open(tail, "wb") as handle:
        handle.write(b"\x00")
    steps = _compile_fixture(root, media)
    baseline = compile_manifest(steps)
    assert all(c.get("bookend") is None
               for c in baseline["tracks"]["V1"]["clips"])
    _declare(root, [{"slot": "tail", "asset": tail,
                     "duration_seconds": 5.0, "has_audio": False,
                     "label": "tail card",
                     "reason": "every episode ends here"}])
    carried = compile_manifest(steps)
    card = carried["tracks"]["V1"]["clips"][-1]
    assert card["bookend"] == "tail_card"
    assert card["provenance"] == "declared"
    assert carried["project"]["duration_seconds"] == pytest.approx(
        baseline["project"]["duration_seconds"] + 5.0)


def test_compile_head_card_shifts_picture_and_plan(tmp_path):
    from library.steps.step_5_04_compile_manifest.step import (
        compile_manifest)

    root = str(tmp_path)
    media = os.path.join(root, "a.mov")
    with open(media, "wb") as handle:
        handle.write(b"\x00")
    head = os.path.join(root, "head.mov")
    with open(head, "wb") as handle:
        handle.write(b"\x00")
    steps = _compile_fixture(root, media)
    _declare(root, [{"slot": "head", "asset": head,
                     "duration_seconds": 3.0, "has_audio": False,
                     "label": "logo", "reason": "opens every episode"}])
    manifest = compile_manifest(steps)
    clips = manifest["tracks"]["V1"]["clips"]
    assert clips[0]["label"].startswith("placed_head")
    assert clips[1]["timeline_in"] == pytest.approx(3.0)
    # The plan rides with the picture: no subtitle lands a card early.
    assert manifest["subtitles"][0]["timeline_start"] == pytest.approx(
        3.0)
    assert manifest["subtitles"][0]["words"][0]["start"] == pytest.approx(
        3.0)
    # A silent card claims no audio stream.
    assert (len(manifest["tracks"]["A1"]["clips"])
            == len([c for c in clips if not c.get("video_only")]))


def test_compile_without_declaration_is_unchanged(tmp_path):
    from library.steps.step_5_04_compile_manifest.step import (
        compile_manifest)

    root = str(tmp_path)
    media = os.path.join(root, "a.mov")
    with open(media, "wb") as handle:
        handle.write(b"\x00")
    steps = _compile_fixture(root, media)
    first = compile_manifest(steps)
    second = compile_manifest(steps)
    assert first == second


def test_compile_refuses_unreadable_declaration(tmp_path):
    from library.steps.step_5_04_compile_manifest.step import (
        compile_manifest)

    root = str(tmp_path)
    media = os.path.join(root, "a.mov")
    with open(media, "wb") as handle:
        handle.write(b"\x00")
    steps = _compile_fixture(root, media)
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    with open(os.path.join(external, "placed_assets.json"), "w",
              encoding="utf-8") as handle:
        handle.write("{not json")
    with pytest.raises(ValueError, match="placed_assets"):
        compile_manifest(steps)


def test_head_carry_shifts_compiled_positions_not_history(tmp_path):
    asset = os.path.join(str(tmp_path), "head.mov")
    with open(asset, "wb") as handle:
        handle.write(b"\x00")
    manifest = {
        "project": {"duration_seconds": 10.0},
        "tracks": {
            "V1": {"clips": [{
                "label": "a", "timeline_in": 0.0, "timeline_out": 10.0,
                "timeline_in_frame": 0, "timeline_out_frame": 300}]},
            "V2": {"clips": []}},
        "subtitles": [{
            "timeline_start": 1.0, "timeline_end": 2.0,
            "timeline_start_frame": 30, "timeline_end_frame": 60,
            "words": [{"word": "hi", "start": 1.0, "end": 1.5}]}],
        "subtitle_overlay": {"segments": [{
            "timeline_start": 1.0, "timeline_end": 2.0}]},
        "transitions": [{
            "transition_id": "t1", "transition_type": "hard_cut",
            "cut_point_timeline": 5.0, "cut_point_original": 5.0,
            "duration_frames": 0}],
        "vfx": [{"timeline_start": 4.0, "timeline_end": 6.0}],
        "fusion_effects": {"transitions": [{"after_clip": 0}]},
    }
    report = placed_assets.carry_into_manifest(
        manifest, [{"slot": "head", "asset": asset,
                    "duration_seconds": 2.0, "reason": "shift test"}],
        fps=30.0)
    assert len(report["carried"]) == 1
    assert manifest["subtitles"][0]["timeline_start"] == pytest.approx(
        3.0)
    assert manifest["subtitle_overlay"]["segments"][0][
        "timeline_start"] == pytest.approx(3.0)
    assert manifest["transitions"][0][
        "cut_point_timeline"] == pytest.approx(7.0)
    # The planner's coordinate is history and stands.
    assert manifest["transitions"][0]["cut_point_original"] == 5.0
    assert manifest["vfx"][0]["timeline_start"] == pytest.approx(6.0)
    # Transition indices count V1 clips: the card takes index 0.
    assert manifest["fusion_effects"]["transitions"][0][
        "after_clip"] == 1


# ── mix_intent: the OTIO seam ──────────────────────────────────────

def _mix_manifest(bed_file="/m/bed.wav"):
    return {"tracks": {
        "V1": {"clips": [{
            "source_file": "/v/a.mov", "source_in": 49.0,
            "source_out": 60.0, "timeline_in": 10.0,
            "timeline_out": 21.0, "timeline_in_frame": 240}]},
        "A2": {"clips": [{
            "source_file": bed_file, "source_in": 0.0,
            "source_out": 30.0, "timeline_in": 10.0,
            "timeline_out": 40.0, "timeline_in_frame": 240}]}}}


def _mix_targets():
    return [{"role": "music", "label": "bed",
             "source_file": "/m/bed.wav", "start_frame": 240,
             "level_db": 0.0, "keyframes": {0: -12.0, 100: -12.0}}]


def _mix_pin():
    return [{"anchor_phrase": "beta gamma", "target": "bed",
             "level_db": -28.0, "reason": "bed buries the words"}]


def test_declared_mix_holds_pin_in_edit_clock():
    transcript = _transcript()
    targets, applied, skipped, stale = mix_intent.apply_declared_mix(
        _mix_targets(), _mix_manifest(), transcript, _mix_pin(),
        fps=24.0)
    assert not skipped and not stale and len(applied) == 1
    # Words at master-timeline 10.5-11.4s play at edit 11.5-12.4s
    # (source offset 40s, V1 maps 49s onto edit 10s).
    assert applied[0]["master_span"] == [11.5, 12.4]
    assert targets[0]["provenance"] == "declared"
    assert min(targets[0]["keyframes"].values()) == pytest.approx(
        -28.0)


def test_declared_mix_stale_where_words_reach_no_clip():
    transcript = _transcript()
    manifest = _mix_manifest()
    manifest["tracks"]["V1"]["clips"][0]["source_in"] = 55.0
    manifest["tracks"]["V1"]["clips"][0]["source_out"] = 60.0
    targets = _mix_targets()
    before = dict(targets[0]["keyframes"])
    _, applied, _, stale = mix_intent.apply_declared_mix(
        targets, manifest, transcript, _mix_pin(), fps=24.0)
    assert not applied and stale
    assert "plays nowhere in this edit" in stale[0]["reason"]
    # The plan mix stands: a pin about dropped seconds must not bend it.
    assert targets[0]["keyframes"] == before
    assert "provenance" not in targets[0]


def test_declared_mix_skips_ambiguous_targets_loudly():
    transcript = _transcript()
    # No exact (file, start) hit, and two clips share the basename:
    # holding either would be a guess, so the target is skipped loudly.
    manifest = {"tracks": {"V1": {"clips": []}, "A2": {"clips": [
        {"source_file": "/a/bed.wav", "timeline_in": 10.0,
         "timeline_out": 40.0, "timeline_in_frame": 240},
        {"source_file": "/b/bed.wav", "timeline_in": 10.0,
         "timeline_out": 40.0, "timeline_in_frame": 240}]}}}
    targets = _mix_targets()
    _, applied, skipped, _ = mix_intent.apply_declared_mix(
        targets, manifest, transcript, _mix_pin(), fps=24.0)
    assert not applied and len(skipped) == 1
    assert "no single manifest clip" in skipped[0]["reason"]


def test_no_pins_passes_targets_through():
    targets = _mix_targets()
    snapshot = json.dumps(targets, sort_keys=True)
    out, applied, skipped, stale = mix_intent.apply_declared_mix(
        targets, _mix_manifest(), _transcript(), [], fps=24.0)
    assert not (applied or skipped or stale)
    assert json.dumps(out, sort_keys=True) == snapshot


class _StubTimeline:
    def __init__(self):
        self.exported = False

    def GetName(self):
        return "stub"

    def Export(self, path, kind):
        self.exported = True
        return False


def _stub_project(tmp_path, pins=None, transcript=None):
    from library.tools.timeline_transcript import transcript_path

    root = str(tmp_path)
    if pins is not None:
        external = os.path.join(root, "external")
        os.makedirs(external, exist_ok=True)
        with open(os.path.join(external, "mix_intent.json"), "w",
                  encoding="utf-8") as handle:
            handle.write(pins if isinstance(pins, str)
                         else json.dumps({"version": 1, "pins": pins}))
    if transcript is not None:
        path = str(transcript_path(root))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(transcript, handle)
    return root


def test_deliver_mix_refuses_unreadable_intent_before_resolve(tmp_path):
    from library.tools.execution import deliver_audio_mix

    root = _stub_project(tmp_path, pins="{not json")
    timeline = _StubTimeline()
    with pytest.raises(RuntimeError, match="mix_intent"):
        deliver_audio_mix.deliver_mix(
            None, None, None, timeline, _mix_manifest(), fps=24.0,
            project_folder=root)
    assert timeline.exported is False


def test_deliver_mix_consults_pins_even_where_resolve_declines(tmp_path):
    from library.tools.execution import deliver_audio_mix

    root = _stub_project(tmp_path, pins=_mix_pin(),
                         transcript=_transcript())
    timeline = _StubTimeline()
    report = deliver_audio_mix.deliver_mix(
        None, None, None, timeline, _mix_manifest(), fps=24.0,
        project_folder=root)
    assert report["delivered"] is False  # stub declines the export
    assert len(report["mix_intent"]["applied"]) == 1
    assert report["mix_intent"]["applied"][0]["level_db"] == -28.0
