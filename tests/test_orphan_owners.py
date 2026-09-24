"""The three orphan classes get owners, vetted at data level.

Timing (Reel 13's trims), hand audio levels and hand-placed assets
had no owning layer: a rebuild could not keep them because nothing
recorded them. Each test below makes the shallow edit, runs the
owning computation, and shows the outcome - then records the deep
edit and shows it persisting through a second rebuild.

No Resolve, no real project: every fixture is synthetic under
`tmp_path` (AGENTS.md 8). What is executed is the real store,
matcher and applier - the Resolve-side draw is display, and display
is out of this lane by brief.
"""

import json

import pytest

from library.tools import captain_edits
from library.tools import mix_intent
from library.tools import placed_assets


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


