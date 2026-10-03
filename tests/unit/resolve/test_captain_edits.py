"""The captain's edits survive a rebuild - as deltas, not replacements.

History: docs/evidence/resolve_test_history.md#test_captain_edits.
"""
from __future__ import annotations
import json
import pytest
from library.tools import captain_edits, external_inputs
from library.tools.external_inputs import ExternalStateError
from library.tools.project_layout import ProjectLayout
from library.tools import mix_intent
from library.tools import placed_assets
import inspect
import os
import sys
from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────

def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _write_edits_file(project, edits, source="captain, 2026-09-09"):
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    path = directory / "captain_edits.json"
    path.write_text(
        json.dumps({"key": "captain_edits", "source": source,
                    "value": edits}),
        encoding="utf-8")
    return path


def _fix(anchor="so what do they", replacement="so what do they keep",
         reason="captain: keep the setup, lose the stumble"):
    return {"kind": "caption_fix", "anchor_phrase": anchor,
            "replacement": replacement, "reason": reason}


def _drop(anchor="so what do they",
          reason="captain: drop the fragment at 22 seconds"):
    return {"kind": "drop_fragment", "anchor_phrase": anchor,
            "reason": reason}


def _words(text, start=10.0, step=0.4):
    out = []
    cursor = start
    for token in text.split():
        out.append({"word": token, "source_start": round(cursor, 3),
                    "source_end": round(cursor + step - 0.05, 3)})
        cursor += step
    return out


def _block(position, text, tl_start, src_id="clip_001"):
    words = _words(text)
    return {
        "position": position, "block_type": "speech",
        "content": {"text": text, "passage_ref": position},
        "clip_id": src_id, "source_clip_id": src_id,
        "source_start": words[0]["source_start"],
        "source_end": words[-1]["source_end"],
        "word_timestamps": [
            {"word": w["word"], "source_start": w["source_start"],
             "source_end": w["source_end"]} for w in words],
        "alignment_method": "test",
        "duration_seconds": round(words[-1]["source_end"]
                                  - words[0]["source_start"], 3),
        "timeline_start": tl_start,
        "timeline_end": round(tl_start + words[-1]["source_end"]
                              - words[0]["source_start"], 3),
    }


def _spine():
    blocks = [
        _block(1, "welcome back to the show", 0.0),
        _block(2, "so what do they want from us", 4.0),
        _block(3, "lucy built the whole thing herself", 9.0),
    ]
    return {"total_estimated_duration_seconds": 14.0,
            "frame_rate": 30.0, "structure": blocks}


def _entries():
    return [
        {"id": "sub_1_001", "timeline_start": 0.0, "timeline_end": 1.5,
         "text": "welcome back to the show", "word_count": 5,
         "emphasis_words": [], "spine_block_position": 1,
         "words": []},
        {"id": "sub_3_001", "timeline_start": 9.0, "timeline_end": 11.0,
         "text": "lucy built the whole thing herself",
         "word_count": 6, "emphasis_words": ["built"],
         "spine_block_position": 3, "words": []},
    ]


# ── 1. The delta validates ───────────────────────────────────────────


def test_a_malformed_edit_is_refused_at_write_time():
    """An unknown kind, a caption fix with no replacement, an edit with
    no reason, and an edit anchored to a frame number - frames are not
    stable across a rebuild (PR 847 refused delete-by-moved-frame for
    the same reason), so it is refused at write time rather than lost at
    rebuild time."""
    no_replacement = _fix()
    del no_replacement["replacement"]
    no_reason = _drop()
    del no_reason["reason"]
    rows = (
        ({"kind": "color_grade", "anchor_phrase": "hello", "reason": "taste"},
         "kind"),
        (no_replacement, "replacement"),
        ({**_drop(), "anchor_frame": 528}, "frame"),
        (no_reason, ""),
    )
    for edit, needle in rows:
        with pytest.raises(captain_edits.CaptainEditError) as exc:
            captain_edits.validate_edits([edit])
        assert needle in str(exc.value).lower(), edit


# ── 2. The external key checks drift ─────────────────────────────────


def test_a_caption_fix_drifted_from_its_speech_is_refused(tmp_path):
    """The check that captions have nowhere else: the anchor's words
    must still be in the speech they caption, where the pipeline has
    already measured it. A fix whose anchor is gone is drift, refused
    by name rather than applied to the wrong seconds."""
    project = _project(tmp_path)
    _write_edits_file(project, [_fix(anchor="zebras on mars")])
    state = {"step_outputs": {
        "mesh_spine": {"audio_spine": _spine()},
        "speech_sequence": {"body_sequence": [
            {"text": b["content"]["text"]} for b in _spine()["structure"]]}}}
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project), state)
    assert "zebras on mars" in str(exc.value)
    # The check reaches a verdict without state: on a first run, before
    # any spine exists, a well-formed edit verifies and the drift
    # question moves to apply time, where it reports STALE loudly.
    supplied = external_inputs.load(str(project), {})
    assert set(supplied) == {"captain_edits"}


# ── 3. Caption fixes apply, word-anchored ────────────────────────────

def test_a_caption_fix_rewrites_text_and_rederives_the_rest():
    entries = _entries()
    fixed, applied, stale = captain_edits.apply_caption_fixes(
        entries, [_fix(anchor="lucy built",
                       replacement="Lucie built")])
    assert stale == []
    assert len(applied) == 1
    assert "Lucie built" in fixed[1]["text"]
    assert "lucy" not in fixed[1]["text"].lower().replace("lucie", "")
    # Timing untouched: a text change must never move the picture/sound.
    assert fixed[1]["timeline_start"] == 9.0
    assert fixed[1]["timeline_end"] == 11.0
    # What depends on the text is re-derived, not left stale.
    assert fixed[1]["word_count"] == len(fixed[1]["text"].split())
    # Untouched entries pass through byte-identical.
    assert fixed[0]["text"] == "welcome back to the show"
    # A fix that matches nothing reports stale and changes nothing.
    entries = _entries()
    _, applied, stale = captain_edits.apply_caption_fixes(
        entries, [_fix(anchor="zebras on mars", replacement="zebras")])
    assert applied == []
    assert len(stale) == 1
    assert "zebras on mars" in stale[0]["reason"]
    assert entries[1]["text"].startswith("lucy")


# ── 4. Drops remove speech and move everything after ─────────────────

def test_a_drop_removes_the_block_and_rederives_later_timings():
    spine = _spine()
    before = [b["timeline_start"] for b in spine["structure"]]
    blocks, applied, stale = captain_edits.apply_drop_fragments(
        spine["structure"], [_drop()])
    assert stale == []
    assert len(applied) == 1
    assert [b["position"] for b in blocks] == [1, 3]
    # Everything after the cut moved earlier by the removed duration -
    # picture and sound move TOGETHER, or a caption pinned to an old
    # time becomes a lie.
    removed = sum(float(b.get("duration_seconds", 0))
                  for b in spine["structure"] if b["position"] == 2)
    kept = spine["structure"][0]["duration_seconds"]
    assert blocks[1]["timeline_start"] == pytest.approx(kept, abs=0.01)
    assert blocks[1]["timeline_start"] < before[2] - removed + 0.01


# ── 5. The captain can read what is in force ─────────────────────────


def test_stale_edits_are_announced_not_silenced(capsys):
    captain_edits.report_stale(
        [{"anchor_phrase": "zebras on mars",
          "reason": "STALE: no longer applies"}])
    out = capsys.readouterr().err
    assert "STALE" in out
    assert "zebras on mars" in out


# ── 6. The captain's exact test: edit, rebuild, still there ──────────

def test_a_caption_fix_and_a_drop_survive_a_rebuild(tmp_path):
    """Make an edit, rebuild, show the edit is still there - both
    kinds, through the REAL step functions, twice."""
    from library.steps.step_4_01_plan_subtitles.step import (
        generate_subtitles)

    project = _project(tmp_path)
    edits = [_fix(anchor="lucy built",
                  replacement="Lucie built",
                  reason="captain: her name is Lucie"),
             _drop(anchor="so what do they",
                   reason="captain: drop the fragment at 22 seconds")]
    _write_edits_file(project, edits)

    def rebuild(spine):
        spine = json.loads(json.dumps(spine))  # a rebuild re-reads
        blocks, _, _ = captain_edits.apply_drop_fragments(
            spine["structure"],
            captain_edits.load_edits(str(project)))
        spine["structure"] = blocks
        plan = generate_subtitles(
            spine, caption_case="as_written",
            project_folder=str(project))["subtitle_plan"]
        plan["subtitle_entries"], _, _ = captain_edits.apply_caption_fixes(
            plan["subtitle_entries"],
            captain_edits.load_edits(str(project)))
        return spine, plan

    spine_a, plan_a = rebuild(_spine())
    spine_b, plan_b = rebuild(_spine())  # the rebuild after the rebuild

    assert [b["position"] for b in spine_a["structure"]] == [1, 3]
    assert [b["position"] for b in spine_b["structure"]] == [1, 3]
    texts = [e["text"] for e in plan_b["subtitle_entries"]]
    assert any("Lucie built" in t for t in texts)
    assert not any("so what do they" in t for t in texts)
    # The rebuilt timeline carries both: no dropped speech, corrected
    # captions, later blocks shifted onto the cut.
    assert spine_b["structure"][1]["timeline_start"] < 9.0
    assert plan_b["total_subtitles"] == plan_a["total_subtitles"]


# --------------------------------------------------------------------------
# From test_orphan_owners.py
#
# The three orphan classes get owners, vetted at data level.
#
# Timing (Reel 13's trims), hand audio levels and hand-placed assets
# had no owning layer: a rebuild could not keep them because nothing
# recorded them. Each test below makes the shallow edit, runs the
# owning computation, and shows the outcome - then records the deep
# edit and shows it persisting through a second rebuild.
#
# No Resolve, no real project: every fixture is synthetic under
# `tmp_path` (AGENTS.md 8). What is executed is the real store,
# matcher and applier - the Resolve-side draw is display, and display
# is out of this lane by brief.

def _transcript():
    words = []
    cursor = 10.0
    for token in ["alpha", "beta", "gamma", "delta", "epsilon"]:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return {"segments": [{"text": "alpha beta gamma delta epsilon",
                          "words": words}]}


def _placements():
    return [
        {"master": (9.5, 12.5), "source_in": 9.5, "source_out": 12.5,
         "record": 0.0, "snapped_record": 0},
        {"master": (12.5, 15.0), "source_in": 12.5,
         "source_out": 15.0, "record": 3.0, "snapped_record": 72},
    ]


# ── Orphan 1: clip timing ──────────────────────────────────────────

def test_retime_pin_trims_and_second_rebuild_holds():
    transcript = _transcript()
    edits = [{"kind": "span_retime", "anchor_phrase": "beta gamma",
              "edge": "head", "reason": "trim test"}]
    places = _placements()
    out, applied, held, stale = captain_edits.retime_placements(
        places, transcript, edits, fps=24.0)
    assert not stale and not held and len(applied) == 1
    assert out[0]["master"] == (10.5, 12.5)
    assert out[0]["source_in"] == pytest.approx(10.5)
    # Everything after moves up: picture and sound together.
    assert out[1]["record"] == pytest.approx(2.0)
    # The second rebuild reports HELD, not stale, not re-applied.
    _, applied2, held2, stale2 = captain_edits.retime_placements(
        out, transcript, edits, fps=24.0)
    assert not applied2 and not stale2 and len(held2) == 1


def test_retime_refuses_timecode_and_extension():
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits(
            [{"kind": "span_retime", "anchor_phrase": "beta gamma",
              "edge": "head", "reason": "x", "start": 10.5}])
    transcript = _transcript()
    # Anchor straddling the span's head: moving the head onto it
    # would play seconds nothing approved - an extension, not a trim.
    edits = [{"kind": "span_retime", "anchor_phrase": "alpha beta",
              "edge": "head", "reason": "extend test"}]
    spans = [{"master": (10.2, 12.5), "source_in": 10.2,
              "source_out": 12.5, "record": 0.0, "snapped_record": 0}]
    _, _, stale = captain_edits.match_span_retimes(
        spans, transcript, edits)
    assert stale and "EXTEND" in stale[0]["reason"]


# ── Orphan 2: hand audio levels ────────────────────────────────────

def _targets():
    return [
        {"role": "music", "label": "bed", "start_frame": 0,
         "level_db": 0.0, "master": (9.0, 13.0),
         "keyframes": {0: -12.0, 48: -12.0, 96: -12.0}},
        {"role": "sfx", "label": "sting", "start_frame": 240,
         "level_db": -6.0, "master": (10.8, 11.8),
         "keyframes": {0: -6.0, 20: -6.0, 24: -60.0}},
    ]


def test_mix_pin_holds_bed_and_clip_post_plan():
    transcript = _transcript()
    pins = mix_intent.validate_pins([
        {"anchor_phrase": "beta gamma", "target": "bed",
         "level_db": -28.0, "reason": "bed buries the words"},
        {"anchor_phrase": "gamma", "target": "clip",
         "level_db": -12.0, "reason": "tame the sting"},
    ])
    targets, applied, skipped, stale = mix_intent.apply_mix_intent(
        _targets(), transcript, pins)
    assert not stale and not skipped
    bed = next(t for t in targets if t["role"] == "music")
    assert bed["provenance"] == "declared"
    assert min(bed["keyframes"].values()) == pytest.approx(-28.0)
    sting = next(t for t in targets if t["role"] == "sfx")
    assert sting["level_db"] == pytest.approx(-12.0)
    # Shape preserved: the de-click ramp is still a ramp.
    assert min(sting["keyframes"].values()) < -12.0


def test_mix_intent_refuses_absurd_and_partial():
    with pytest.raises(mix_intent.MixIntentError):
        mix_intent.validate_pins(
            [{"anchor_phrase": "beta", "target": "bed",
              "level_db": -99.0, "reason": "x"}])
    with pytest.raises(mix_intent.MixIntentError):
        mix_intent.validate_pins(
            [{"anchor_phrase": "beta", "target": "bed",
              "level_db": -20.0, "reason": ""}])
    with pytest.raises(mix_intent.MixIntentError):
        mix_intent.validate_pins(
            [{"anchor_phrase": "beta", "target": "bed",
              "level_db": -20.0, "reason": "x", "start": 1.0}])


# ── Orphan 3: hand-placed assets ───────────────────────────────────

def _manifest():
    return {
        "project": {"duration_seconds": 10.0},
        "_spine_blocks": [{"position": "b1", "timeline_start": 0.0,
                           "timeline_end": 10.0}],
        "audio_mix": {"music_automation": [
            {"timeline_start": 0.0, "timeline_end": 10.0}]},
        "tracks": {
            "V1": {"clips": [{"label": "a", "timeline_in": 0.0,
                              "timeline_out": 10.0,
                              "timeline_in_frame": 0,
                              "timeline_out_frame": 300}]},
            "A1": {"clips": []},
        },
    }


def test_placed_tail_card_rides_the_manifest():
    asset_file = "/tmp/vetting_tailcard.mov"
    with open(asset_file, "wb") as handle:
        handle.write(b"\x00")
    manifest = _manifest()
    assets = placed_assets.validate_assets(
        [{"slot": "tail", "asset": asset_file,
          "duration_seconds": 5.0, "has_audio": False,
          "label": "tail card", "reason": "every episode ends here"}])
    report = placed_assets.carry_into_manifest(manifest, assets, fps=30.0)
    assert not report["refused"] and len(report["carried"]) == 1
    card = manifest["tracks"]["V1"]["clips"][-1]
    assert card["timeline_in"] == pytest.approx(10.0)
    assert card["bookend"] == "tail_card"
    assert card["provenance"] == "declared"
    assert manifest["project"]["duration_seconds"] == pytest.approx(15.0)


def test_placed_head_card_shifts_picture_and_plan():
    asset_file = "/tmp/vetting_headcard.mov"
    with open(asset_file, "wb") as handle:
        handle.write(b"\x00")
    manifest = _manifest()
    assets = [{"slot": "head", "asset": asset_file,
               "duration_seconds": 3.0, "has_audio": False,
               "label": "logo", "reason": "opens every episode"}]
    report = placed_assets.carry_into_manifest(manifest, assets, fps=30.0)
    assert len(report["carried"]) == 1
    clips = manifest["tracks"]["V1"]["clips"]
    assert clips[0]["timeline_in"] == pytest.approx(0.0)
    assert clips[0]["label"].startswith("placed_head")
    assert clips[1]["timeline_in"] == pytest.approx(3.0)
    # The plan rides with the picture: no subtitle lands a card early.
    assert manifest["_spine_blocks"][0]["timeline_start"] == pytest.approx(3.0)
    assert manifest["audio_mix"]["music_automation"][0][
        "timeline_start"] == pytest.approx(3.0)


def test_placed_assets_refuse_mid_reel_relative_and_missing():
    with pytest.raises(placed_assets.PlacedAssetError):
        placed_assets.validate_assets(
            [{"slot": "middle", "asset": "/abs/card.mov",
              "duration_seconds": 5.0, "reason": "x"}])
    with pytest.raises(placed_assets.PlacedAssetError):
        placed_assets.validate_assets(
            [{"slot": "tail", "asset": "relative/card.mov",
              "duration_seconds": 5.0, "reason": "x"}])
    manifest = _manifest()
    report = placed_assets.carry_into_manifest(
        manifest, [{"slot": "tail", "asset": "/abs/never_rendered.mov",
                    "duration_seconds": 5.0, "reason": "x"}])
    assert len(report["refused"]) == 1
    assert "not on disk" in report["refused"][0]["reason"]
    assert len(manifest["tracks"]["V1"]["clips"]) == 1


# --------------------------------------------------------------------------
# From test_orphan_wiring.py
#
# The three orphan owners are consulted by the builders.
#
# `span_retime`, `mix_intent` and `placed_assets` existed as stores,
# matchers and appliers that no builder called - a declared-but-never-
# enforced shape. Each test below runs the builder-side consultation
# against a real shallow/deep pair and shows the verdict: a rebuild
# that honours the declaration, and a rebuild without one that is byte
# identical to before. No Resolve, no real project: every fixture is
# synthetic under `tmp_path` (AGENTS.md 8).

def _words_2(*tokens, start=10.0):
    words = []
    cursor = start
    for token in tokens:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return words


def _transcript_2():
    return {"segments": [{
        "text": "alpha beta gamma delta",
        "timeline_start": 10.0, "timeline_end": 12.0,
        "source_start": 50.0, "source_file": "/v/a.mov",
        "words": _words_2("alpha", "beta", "gamma", "delta")}]}


# ── span_retime: the ranges seam ───────────────────────────────────

def test_retime_ranges_trims_head_before_anything_derives():
    transcript = _transcript_2()
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
    transcript = _transcript_2()
    spans = [{"master": (10.0, 11.0)}, {"master": (11.0, 12.0)}]
    edits = [{"kind": "span_retime", "anchor_phrase": "delta",
              "edge": "head", "reason": "interior trim"}]
    ranges, applied, _, stale = captain_edits.retime_ranges(
        [(10.0, 12.0)], spans, transcript, edits, fps=24.0)
    assert not stale and len(applied) == 1
    # Removed seconds stay removed: two ranges, not one bridged whole.
    assert ranges == [(10.0, 11.0), (11.5, 12.0)]


def test_retime_ranges_matches_placement_rebuild():
    """The seam proof: trimming ranges, then placing, plays the same
    masters as placing, then trimming - so captions planned from the
    trimmed ranges describe the trimmed picture."""
    from types import SimpleNamespace

    from library.tools import reel_build

    transcript = _transcript_2()
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


def test_retime_ranges_keeps_play_order_with_early_master_cta():
    """A head pin must not re-sort the CTA before the body.

    The reel plays body ranges, then the closing CTA - which may come
    from anywhere in the episode, including EARLIER master seconds.
    The merge-back used to sort by master time, so a pin on Reel 16's
    body (2026-09-18) returned the 1168s CTA before the 1338s body and
    the ending truncated the body to the CTA's shot end and refused
    the build. Play order in, play order out.
    """
    transcript = {"segments": [
        {"text": "check it out",
         "words": _words_2("check", "it", "out", start=10.0)},
        {"text": "what you are saying reverts back if your brand "
                 "is mentioned here today",
         "words": _words_2("what", "you", "are", "saying", "reverts",
                          "back", "if", "your", "brand", "is",
                          "mentioned", "here", "today", start=30.0)}]}
    spans = [{"master": (30.0, 40.0)}, {"master": (10.0, 12.0)}]
    edits = [{"kind": "span_retime",
              "anchor_phrase": "if your brand is mentioned here",
              "edge": "head", "reason": "reel 16 re-pin"}]
    ranges, applied, _, stale = captain_edits.retime_ranges(
        [(30.0, 40.0), (10.0, 12.0)], spans, transcript, edits,
        fps=24.0)
    assert not stale and len(applied) == 1
    assert ranges == [(33.0, 40.0), (10.0, 12.0)]


def test_retime_ranges_leaves_untouched_range_bounds_bit_identical():
    """A trim on one span must not move another span's edge by dust.

    Measured on Reel 16 (2026-09-19): a body head trim rebuilt the
    untouched closer head 1168.2899999999997s as 1168.292s (probe
    frame-quantisation plus ms-rounding), and the opening "if" whose
    start the snap had landed exactly on the head fell outside every
    range and lost its caption while the audio still plays it.
    """
    closer = (1168.2899999999997, 1177.69)
    transcript = {"segments": [
        {"text": "alpha beta gamma delta",
         "timeline_start": 30.0, "timeline_end": 32.0,
         "source_start": 130.0, "source_file": "/v/a.mov",
         "words": _words_2("alpha", "beta", "gamma", "delta",
                         start=30.0)},
        {"text": "if you run loud",
         "timeline_start": 1168.2899999999997,
         "timeline_end": 1170.0,
         "source_start": 2533.6122916666664,
         "source_file": "/v/b.mov",
         "words": [
             {"word": "if", "start": 1168.2899999999997,
              "end": 1168.44, "timed": True},
             {"word": "you", "start": 1168.44,
              "end": 1168.55, "timed": True},
         ]},
    ]}
    spans = [{"master": (30.0, 40.0)}, {"master": closer}]
    edits = [{"kind": "span_retime", "anchor_phrase": "beta gamma",
              "edge": "head", "reason": "untouched-bound test"}]
    ranges, applied, _, stale = captain_edits.retime_ranges(
        [(30.0, 40.0), closer], spans, transcript, edits, fps=24.0)
    assert not stale and len(applied) == 1
    assert ranges[0] == (30.5, 40.0)
    assert ranges[1][0] == closer[0] and ranges[1][1] == closer[1]
    assert ranges[1][0] == 1168.2899999999997


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
    transcript = _transcript_2()
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
    transcript = _transcript_2()
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
    transcript = _transcript_2()
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


def test_deliver_mix_consults_pins_even_where_resolve_declines(tmp_path):
    from library.tools.execution import deliver_audio_mix

    root = _stub_project(tmp_path, pins=_mix_pin(),
                         transcript=_transcript_2())
    timeline = _StubTimeline()
    report = deliver_audio_mix.deliver_mix(
        None, None, None, timeline, _mix_manifest(), fps=24.0,
        project_folder=root)
    assert report["delivered"] is False  # stub declines the export
    assert len(report["mix_intent"]["applied"]) == 1
    assert report["mix_intent"]["applied"][0]["level_db"] == -28.0


# --------------------------------------------------------------------------
# From test_retime_drift.py
#
# A recorded trim whose anchor re-times must be loud before a build proceeds.
#
# History: docs/evidence/resolve_test_history.md#test_retime_drift.

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


FPS = 24000 / 1001

RECORDED_SO = 1407.830
RETIMED_SO = 1407.970


def _words_3(tokens, start):
    words = []
    cursor = start
    for token in tokens:
        words.append({"word": token, "start": round(cursor, 3),
                      "end": round(cursor + 0.32, 3), "timed": True})
        cursor = round(cursor + 0.4, 3)
    return words


def _transcript_3(so_start):
    return {"segments": [{
        "text": "so for small business owners the first step",
        "timeline_start": 1400.0, "timeline_end": 1420.0,
        "source_start": 3119.0, "source_file": "/v/speakertwo.mov",
        "words": _words_3(
            ["so", "for", "small", "business", "owners",
             "the", "first", "step"],
            start=so_start)}]}


def _pin(recorded_edge=RECORDED_SO):
    edit = {"kind": "span_retime",
            "anchor_phrase": "So for small business",
            "edge": "head",
            "reason": "opens where judged, recorded 2026-09-18"}
    if recorded_edge is not None:
        edit["recorded_edge"] = recorded_edge
    return edit


def _spans():
    return [{"master": (1400.0, 1420.0)}]


def _write_transcript(project, transcript):
    path = (project / "pipeline_output" / "scratch"
            / "timeline_transcript" / "transcript.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(transcript), encoding="utf-8")


# ── The offline reproduction: the snap follows the transcript ────────

def test_match_follows_retimed_words_to_1407_97():
    matched, held, stale = captain_edits.match_span_retimes(
        _spans(), _transcript_3(RETIMED_SO), [_pin()])
    assert not stale and not held and len(matched) == 1
    assert matched[0]["new_edge"] == pytest.approx(RETIMED_SO)


# ── Outcome 2: resolves to a different place ─────────────────────────

def test_freshness_names_drifted_held_and_stale_anchors_before_a_build():
    drifted, stale = captain_edits.check_span_retime_freshness(
        _spans(), _transcript_3(RETIMED_SO), [_pin()], fps=FPS)
    assert not stale
    assert len(drifted) == 1
    record = drifted[0]
    assert record["anchor_phrase"] == "So for small business"
    assert record["edge"] == "head"
    assert record["recorded_edge"] == pytest.approx(RECORDED_SO)
    assert record["resolved_edge"] == pytest.approx(RETIMED_SO)
    assert record["frames_moved"] == 4
    assert record["span_index"] == 0

    # A fresh anchor reports nothing.
    drifted, stale = captain_edits.check_span_retime_freshness(
        _spans(), _transcript_3(RECORDED_SO), [_pin()], fps=FPS)
    assert drifted == [] and stale == []

    # A previous build already followed the words: the span edge sits
    # on them (HELD, nothing to trim), but they are not where the trim
    # was recorded - still drift, still loud.
    spans = [{"master": (RETIMED_SO, 1420.0)}]
    drifted, stale = captain_edits.check_span_retime_freshness(
        spans, _transcript_3(RETIMED_SO), [_pin()], fps=FPS)
    assert stale == []
    assert len(drifted) == 1
    assert drifted[0]["recorded_edge"] == pytest.approx(RECORDED_SO)
    assert drifted[0]["resolved_edge"] == pytest.approx(RETIMED_SO)
    assert drifted[0]["frames_moved"] == 4

    # Outcome 1, through the same check: a reworded anchor no longer
    # resolves, and is stale rather than drifted.
    reworded = {"segments": [{
        "text": "well for tiny business owners the first step",
        "timeline_start": 1400.0, "timeline_end": 1420.0,
        "source_start": 3119.0, "source_file": "/v/speakertwo.mov",
        "words": _words_3(
            ["well", "for", "tiny", "business", "owners",
             "the", "first", "step"],
            start=RECORDED_SO)}]}
    drifted, stale = captain_edits.check_span_retime_freshness(
        _spans(), reworded, [_pin()], fps=FPS)
    assert drifted == []
    assert len(stale) == 1
    assert stale[0]["anchor_phrase"] == "So for small business"


# ── Outcome 1: no longer resolves ────────────────────────────────────

def test_drifted_is_loud_on_stderr(capsys):
    drifted, _ = captain_edits.check_span_retime_freshness(
        _spans(), _transcript_3(RETIMED_SO), [_pin()], fps=FPS)
    lines = captain_edits.report_drifted(drifted)
    assert len(lines) == 1
    assert "So for small business" in lines[0]
    assert "1407.830" in lines[0] and "1407.970" in lines[0]
    captured = capsys.readouterr()
    assert "DRIFTED EDIT" in captured.err


# ── The write side stamps where the anchor resolved ──────────────────

def test_record_stamps_the_resolved_edge(tmp_path):
    project = _project(tmp_path)
    _write_transcript(project, _transcript_3(RECORDED_SO))
    edit, action = captain_edits.record_edit(
        str(project),
        {"kind": "span_retime",
         "anchor_phrase": "So for small business",
         "edge": "head",
         "reason": "opens where judged"},
        "captain, test")
    assert action == "recorded"
    assert edit["recorded_edge"] == pytest.approx(RECORDED_SO)
    assert captain_edits.load_edits(str(project)) == [edit]


def test_record_leaves_an_ambiguous_anchor_unstamped(tmp_path):
    project = _project(tmp_path)
    doubled = {"segments": [
        {"text": "so for small business cheers",
         "timeline_start": 1400.0, "timeline_end": 1410.0,
         "source_start": 3119.0, "source_file": "/v/a.mov",
         "words": _words_3(["so", "for", "small", "business", "cheers"],
                         start=1400.0)},
        {"text": "so for small business again",
         "timeline_start": 1500.0, "timeline_end": 1510.0,
         "source_start": 3219.0, "source_file": "/v/b.mov",
         "words": _words_3(["so", "for", "small", "business", "again"],
                         start=1500.0)},
    ]}
    _write_transcript(project, doubled)
    edit, _ = captain_edits.record_edit(
        str(project),
        {"kind": "span_retime",
         "anchor_phrase": "So for small business",
         "edge": "head",
         "reason": "ambiguous but spoken"},
        "captain, test")
    assert "recorded_edge" not in edit


def test_recorded_edge_must_be_a_real_second(tmp_path):
    project = _project(tmp_path)
    bad = dict(_pin(), recorded_edge="about there")
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.record_edit(str(project), bad)
    assert "recorded_edge" in str(exc.value)


# ── The build checks before it applies, and files what it found ─────

def _derive_project(tmp_path, edits):
    project = _project(tmp_path)
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    (directory / "captain_edits.json").write_text(
        json.dumps({"key": "captain_edits", "source": "captain, test",
                    "value": edits}),
        encoding="utf-8")
    return str(project)


def test_derive_files_drift_before_applying(tmp_path, capsys):
    from types import SimpleNamespace

    from library.tools import reel_build as build

    transcript = {"segments": [{
        "timeline_start": 10.0, "timeline_end": 14.0,
        "source_file": "clip_a.mov", "source_start": 100.0,
        "words": [
            {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
            {"word": "plays", "start": 11.0, "end": 11.4,
             "timed": True},
            {"word": "mind", "start": 12.5, "end": 13.0,
             "timed": True}]}]}
    pin = {"kind": "span_retime", "anchor_phrase": "plays",
           "edge": "head", "recorded_edge": 10.5,
           "reason": "opens on plays"}
    project = _derive_project(tmp_path, [pin])
    moment = SimpleNamespace(
        number=9, timeline_name="Reel 09",
        timeline_start=10.0, timeline_end=14.0)
    clips = [SimpleNamespace(
        timeline_start=0.0, timeline_end=30.0, source_in=90.0,
        track_index=1, track_type="video", speaker="X",
        source_file="clip_a.mov")]
    cuts, insisted = build.moment_cuts_and_insistences(
        moment, transcript, [], [])
    trims: dict = {}
    ranges, cards, ending = build.derive_reel_ranges_and_cards(
        moment, transcript, clips, project, FPS, "Reel 09",
        cuts, insisted, card_declarations=[], look_decl=None,
        reel_width=1080, reel_height=1920, collect_trims=trims)
    # Enumerable: the drift is filed beside applied/held/stale.
    assert len(trims["drifted"]) == 1
    assert trims["drifted"][0]["anchor_phrase"] == "plays"
    assert trims["drifted"][0]["recorded_edge"] == pytest.approx(10.5)
    assert trims["drifted"][0]["resolved_edge"] == pytest.approx(11.0)
    assert trims["stale"] == []
    # Loud: stderr says so before the trim below lands.
    captured = capsys.readouterr()
    assert "DRIFTED EDIT" in captured.err
    # And the trim still applies - drift is a state, not a refusal.
    assert len(trims["applied"]) == 1
    assert ranges == [(11.0, 14.0)]
    assert cards == [] and ending is None
