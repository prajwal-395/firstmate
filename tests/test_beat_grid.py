"""P4.1: the real beat grid, and the two ways nobody was reaching it.

`plan_transitions` synthesised `[i * 60/bpm for i in ...]` from t=0. No
track's first beat lands at 0.000s, so every "beat-snapped" cut was
snapped to a grid offset from the music by the track's lead-in.

`plan_sfx` read `music_analysis["beat_grid"]["bars"]`, and the producer
emits no `beat_grid` key at all. So `bars` was always `[]` and the SFX
snapping never ran either - even though the standing plan credited
`plan_sfx` as the one honest consumer of the real grid.

The producer/consumer agreement is asserted here against the real
producer, `library/tools/analysis/music_pipeline.py`, so a rename on
either side fails rather than degrading to silence.
"""
import ast
import os
import re
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.beat_grid import (
    MIN_USABLE_BEATS,
    assert_music_starts_at_timeline_zero,
    beat_positions,
    bpm,
    downbeat_positions,
)

MUSIC_PIPELINE = os.path.join(PROJECT_ROOT, "library", "tools", "analysis",
                              "music_pipeline.py")


def analysis(beats=None, downbeats=None, tempo_bpm=120.0, **extra):
    a = {"tempo": {"bpm": tempo_bpm,
                   "beats": beats if beats is not None else [],
                   "downbeats": downbeats if downbeats is not None else []}}
    a.update(extra)
    return a


BEATS = [round(0.37 + i * 0.5, 3) for i in range(32)]


# ─────────────────────────────────────────────────────────
# The producer and the consumer must agree on the names
# ─────────────────────────────────────────────────────────

def test_the_producer_really_emits_tempo_beats_and_downbeats():
    """Read off music_pipeline itself, not off a fixture.

    A fixture agreeing with the reader proves the fixture. This asserts
    the shipped producer builds the keys this module reads.
    """
    with open(MUSIC_PIPELINE, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    emitted.add(key.value)

    assert "tempo" in emitted
    assert "beats" in emitted
    assert "downbeats" in emitted
    assert "bpm" in emitted


def test_the_producer_emits_no_beat_grid_key():
    """The key plan_sfx used to ask for. It never existed."""
    with open(MUSIC_PIPELINE, encoding="utf-8") as f:
        src = f.read()
    assert "beat_grid" not in src


def test_no_consumer_reads_the_key_that_never_existed():
    consumers = [
        "library/steps/step_4_04_plan_sfx/post_bridge.py",
        "library/steps/step_4_02_plan_transitions/post_bridge.py",
        "library/steps/step_4_02_plan_transitions/bridge.py",
    ]
    offenders = []
    for rel in consumers:
        with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as f:
            code = "\n".join(l for l in f.read().splitlines()
                             if not l.lstrip().startswith("#"))
        if 'get("beat_grid"' in code or "get('beat_grid'" in code:
            offenders.append(rel)
    assert not offenders, (
        f"{offenders} read music_analysis['beat_grid'], which the producer "
        f"has never emitted. Use library/tools/beat_grid.py.")


def test_nobody_synthesises_a_grid_from_bpm_any_more():
    """`[i * 60/bpm ...]` from t=0 is the bug, not a fallback."""
    for rel in ("library/steps/step_4_02_plan_transitions/post_bridge.py",
                "library/steps/step_4_02_plan_transitions/bridge.py"):
        with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as f:
            code = "\n".join(l for l in f.read().splitlines()
                             if not l.lstrip().startswith("#"))
        assert "60.0 / bpm" not in code and "60 / bpm" not in code, (
            f"{rel} still synthesises a beat grid from BPM. The synthetic "
            f"grid starts at t=0 and no track's first beat does.")


# ─────────────────────────────────────────────────────────
# Reading the grid
# ─────────────────────────────────────────────────────────

def test_beats_are_returned_in_order():
    got = beat_positions(analysis(beats=list(reversed(BEATS))))
    assert got == sorted(BEATS)


def test_the_first_beat_is_not_assumed_to_be_zero():
    """The whole point: real grids have a lead-in."""
    got = beat_positions(analysis(beats=BEATS))
    assert got[0] == pytest.approx(0.37)
    assert got[0] != 0.0


def test_downbeats_are_separate_from_beats():
    got = downbeat_positions(analysis(beats=BEATS, downbeats=BEATS[::4]))
    assert got == sorted(BEATS[::4])
    assert len(got) < len(BEATS)


def test_bpm_comes_from_tempo():
    assert bpm(analysis(beats=BEATS, tempo_bpm=128.0)) == 128.0


class TestRefusesToInvent:
    """Empty means "do not snap", which every caller already honours."""

    def test_no_analysis(self):
        assert beat_positions(None) == []
        assert beat_positions({}) == []
        assert downbeat_positions(None) == []
        assert bpm(None) is None

    def test_unavailable_analysis(self):
        a = analysis(beats=BEATS)
        a["available"] = False
        assert beat_positions(a) == []

    def test_too_few_beats_is_not_a_rhythm(self):
        few = BEATS[:MIN_USABLE_BEATS - 1]
        assert beat_positions(analysis(beats=few)) == []

    def test_enough_beats_is(self):
        enough = BEATS[:MIN_USABLE_BEATS]
        assert len(beat_positions(analysis(beats=enough))) == MIN_USABLE_BEATS

    def test_malformed_entries_are_skipped_not_crashed(self):
        messy = list(BEATS) + ["x", None, {}, -1.0]
        assert beat_positions(analysis(beats=messy)) == sorted(BEATS)

    def test_missing_or_wrong_shaped_tempo(self):
        assert beat_positions({"tempo": None}) == []
        assert beat_positions({"tempo": []}) == []
        assert beat_positions({"tempo": {"beats": "nope"}}) == []

    def test_bad_bpm(self):
        assert bpm({"tempo": {"bpm": 0}}) is None
        assert bpm({"tempo": {"bpm": "fast"}}) is None
        assert bpm({"tempo": {}}) is None


# ─────────────────────────────────────────────────────────
# The time domain the grid depends on
# ─────────────────────────────────────────────────────────

def test_music_at_zero_passes():
    assert_music_starts_at_timeline_zero(
        {"tracks": {"A2": {"clips": [{"source_in": 0.0, "timeline_in": 0.0}]}}})


def test_no_music_passes():
    assert_music_starts_at_timeline_zero({"tracks": {}})
    assert_music_starts_at_timeline_zero({})


def test_offset_music_raises():
    """A snapped cut that is off by the music's offset looks exactly like
    a snapped cut that is correct."""
    with pytest.raises(ValueError, match="beat"):
        assert_music_starts_at_timeline_zero(
            {"tracks": {"A2": {"clips": [
                {"source_in": 4.5, "timeline_in": 0.0}]}}})


def test_compile_manifest_asserts_the_domain():
    path = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_5_04_compile_manifest", "step.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "assert_music_starts_at_timeline_zero(manifest)" in src, (
        "compile_manifest must assert the time domain the beat grid "
        "depends on, or an offset silently moves every snapped cut")


# ─────────────────────────────────────────────────────────
# P4.2: pacing, removed rather than left as a number nobody acts on
# ─────────────────────────────────────────────────────────

def test_no_template_declares_a_pacing_target():
    """Pacing config with no reader is the reads-as-coverage problem.

    Three templates spelled it `cuts_per_minute_min`/`_max` and a fourth
    `min_cuts_per_minute`/`max_cuts_per_minute`, and nothing in the
    repository read either spelling. If pacing control is wanted it is a
    re-cut loop and a design job - not a key in a YAML file.
    """
    import glob
    offenders = []
    for path in glob.glob(os.path.join(PROJECT_ROOT, "library", "templates", "*.yaml")):
        with open(path, encoding="utf-8") as f:
            code = "\n".join(l for l in f.read().splitlines()
                             if not l.lstrip().startswith("#"))
        if "cuts_per_minute" in code or "pacing:" in code:
            offenders.append(os.path.basename(path))
    assert not offenders, (
        f"{offenders} declare pacing config again. Nothing reads it; add a "
        f"reader in the same commit or leave it out.")


def test_the_cohesion_step_no_longer_scores_pacing():
    path = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_5_03_creative_cohesion", "step.py")
    with open(path, encoding="utf-8") as f:
        code = "\n".join(l for l in f.read().splitlines()
                         if not l.lstrip().startswith("#"))
    assert "extract_cuts_per_minute" not in code
    assert "Pacing mismatch" not in code


def test_the_brand_schema_has_no_pacing_slot():
    from library.schemas.brand_template import StyleSlots
    assert not hasattr(StyleSlots(), "pacing")
