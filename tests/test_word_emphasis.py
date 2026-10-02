"""Per-word emphasis: measured from pitch, loudness and duration - never guessed.

Fidelity rung 5b. Step 1.05 measured clip-level pitch stats but no
per-word emphasis, so a plan asking "punch 15% on 'quit', whoosh
exactly on it" had no measurement to address - and rung 2's anchors
had no emphasis table to read. `measure_word_prosody` scores every
timed word from the Voz+MFA stamps against the speaker's own
baselines, and `view:emphasis` carries three scored words per spine
block to the steps that plan punches, sounds and cuts (4.02, 4.03,
4.04). The model still decides; the measurement is context.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.analysis.speech_advanced_pipeline import (
    WORD_EMPHASIS_FORMULA,
    measure_word_prosody,
)
from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon

STEPS = REPO / "library" / "steps"

ANCHOR_STEPS = {
    "step_4_02_plan_transitions",
    "step_4_03_plan_vfx",
    "step_4_04_plan_sfx",
}


def manifest(step_dir: str) -> dict:
    return json.loads((STEPS / step_dir / "manifest.json").read_text(
        encoding="utf-8"))


# ── The formula on synthetic contours ───────────────────────────────

def test_an_emphasized_word_outscores_its_neighbours():
    """Higher, louder, longer wins - the three terms all fire."""
    regions = [{"start": 0.0, "end": 2.0, "words": [
        {"word": "i", "start": 0.1, "end": 0.2},
        {"word": "quit", "start": 1.0, "end": 1.45},
        {"word": "now", "start": 1.6, "end": 1.8},
    ]}]
    pitch = [(round(t * 0.01, 3), 100.0) for t in range(200)
             if not 100 <= t < 145]
    pitch += [(round(t * 0.01, 3), 130.0) for t in range(100, 145)]
    inten = [(t / 20.0, 58.0) for t in range(40)]
    inten += [(1.0 + i * 0.05, 66.0) for i in range(9)]

    out = measure_word_prosody(regions, pitch, inten)
    scores = {w["word"]: w["emphasis"] for w in out["words"]}
    assert scores["quit"] > scores["i"]
    assert scores["quit"] > scores["now"]
    quit = next(w for w in out["words"] if w["word"] == "quit")
    assert quit["terms"] == 3
    # The three terms, stated: 130 vs 100 Hz is +4.5 st (/3 = 1.5).
    assert quit["f0_rel_semitones"] == pytest.approx(4.5, abs=0.05)


def test_one_octave_error_frame_does_not_decide_a_word():
    """Median inside the span: a single Praat spike is outvoted."""
    regions = [{"start": 0.0, "end": 1.0, "words": [
        {"word": "flat", "start": 0.1, "end": 0.5},
    ]}]
    pitch = [(round(0.1 + i * 0.01, 3), 110.0) for i in range(40)]
    pitch[20] = (0.3, 580.0)  # the spike this footage produces
    out = measure_word_prosody(regions, pitch, [])
    flat = out["words"][0]
    assert flat["f0_rel_semitones"] == pytest.approx(0.0, abs=0.1)


def test_an_unvoiced_word_still_scores_from_what_answered():
    """No voiced frames: no f0 term, and the score says so."""
    regions = [{"start": 0.0, "end": 1.0, "words": [
        {"word": "shh", "start": 0.1, "end": 0.4},
    ]}]
    inten = [(round(0.1 + i * 0.05, 3), 62.0) for i in range(6)]
    out = measure_word_prosody(regions, pitch_samples=[],
                                intensity_samples=inten)
    shh = out["words"][0]
    assert shh["f0_rel_semitones"] is None
    assert shh["loud_rel_db"] is not None
    assert shh["terms"] == 2
    assert shh["emphasis"] is not None


def test_no_speech_regions_is_an_empty_table_not_a_missing_one():
    out = measure_word_prosody(None, [(0.0, 110.0)], [(0.0, 60.0)])
    assert out["words"] == []
    assert out["baseline_f0_median_hz"] == pytest.approx(110.0)
    assert out["formula"] == WORD_EMPHASIS_FORMULA


def test_duration_is_expected_from_the_speakers_own_pace():
    """Twice as slow as the speaker's own letters-per-second: +1 term."""
    regions = [{"start": 0.0, "end": 2.0, "words": [
        {"word": "aa", "start": 0.0, "end": 0.2},
        {"word": "bb", "start": 0.5, "end": 0.9},
    ]}]
    out = measure_word_prosody(regions, [], [])
    by_word = {w["word"]: w for w in out["words"]}
    # 0.6 s over 4 letters: 0.15 s/letter; "bb" is 0.4/0.3 = 1.33x.
    assert by_word["bb"]["dur_ratio"] == pytest.approx(1.333, abs=0.01)
    assert by_word["aa"]["dur_ratio"] == pytest.approx(0.667, abs=0.01)


# ── The view: three scored words per block ───────────────────────────

def _profiles():
    return {"clip_017": {
        "clip_id": "clip_017",
        "prosody": {
            "method": "parselmouth-praat",
            "pitch_stats": {"mean_f0_hz": 121.7},
            "word_prosody": [
                {"word": "i", "start": 30.0, "end": 30.1,
                 "f0_rel_semitones": 0.4, "loud_rel_db": 1.0,
                 "dur_ratio": 0.9, "emphasis": 0.1, "terms": 3},
                {"word": "quit", "start": 33.135, "end": 33.295,
                 "f0_rel_semitones": 1.24, "loud_rel_db": -1.2,
                 "dur_ratio": 0.772, "emphasis": -0.087, "terms": 3},
                {"word": "only", "start": 34.853, "end": 35.0,
                 "f0_rel_semitones": 6.27, "loud_rel_db": 9.15,
                 "dur_ratio": 1.076, "emphasis": 1.494, "terms": 3},
                {"word": "quit", "start": 36.0, "end": 36.2,
                 "f0_rel_semitones": 2.0, "loud_rel_db": 2.0,
                 "dur_ratio": 1.0, "emphasis": 0.389, "terms": 3},
            ],
            "word_prosody_formula": WORD_EMPHASIS_FORMULA,
        },
    }}


def _spine():
    return {"structure": [
        {"position": 3, "block_type": "speech", "clip_id": "clip_017",
         "source_start": 30.0, "source_end": 40.0,
         "timeline_start": 8.38, "timeline_end": 18.38,
         "word_timestamps": [
             {"word": "i", "source_start": 30.0, "source_end": 30.1},
             {"word": "quit", "source_start": 33.135,
              "source_end": 33.295},
             {"word": "only", "source_start": 34.853,
              "source_end": 35.0},
             {"word": "quit", "source_start": 36.0, "source_end": 36.2},
         ],
         "alignment_method": "mfa"},
        {"position": 4, "block_type": "speech", "clip_id": "clip_099",
         "source_start": 0.0, "source_end": 5.0,
         "timeline_start": 18.38, "timeline_end": 23.38,
         "word_timestamps": [
             {"word": "hello", "source_start": 0.0, "source_end": 0.4},
         ],
         "alignment_method": "mfa"},
    ]}


def test_three_scored_words_reach_the_block_and_gaps_are_named():
    view = build_view("emphasis", {
        "prosody_analysis": {"profiles": _profiles()},
        "timed_spine": _spine(),
    })
    blocks = view["emphasis"]["blocks"]
    assert len(blocks) == 1
    row = blocks[0]
    assert row["block_position"] == 3
    assert row["most_emphasized_word"] == "only"
    assert row["most_emphasized_occurrence"] == 1
    # The second saying of "quit" pairs with the second profile row -
    # occurrence counts the spelling, in spoken order.
    quits = [w for w in row["top_words"] if w["word"] == "quit"]
    assert {q["occurrence"] for q in quits} <= {1, 2}
    assert "1.494" in json_to_toon(view)
    # A block without measurement is named, not absent.
    assert "4" in view["emphasis"]["not_measured"]
    # No prosody routed is no view, not an error.
    assert build_view("emphasis", {"timed_spine": _spine()}) == {}
    assert build_view("emphasis", {"prosody_analysis": {}}) == {}


def test_the_per_word_table_does_not_reach_the_prompt():
    """AGENTS.md 10.1: No raw value list reaches a prompt."""
    view = build_view("prosody",
                      {"prosody_analysis": {"profiles": _profiles()}})
    serialised = json.dumps(view)
    assert "word_prosody" not in serialised.replace(
        "word_prosody_formula", "")
    assert "121.7" in serialised  # ...while the summary still does.


# ── The wiring: every anchor consumer declares it ────────────────────

def test_anchor_consumers_declare_the_view_and_its_input():
    """A view is not routing: the step still declares prosody_analysis."""
    for step_dir in sorted(ANCHOR_STEPS):
        m = manifest(step_dir)
        assert "view:emphasis" in m["context_fields"], (
            f"{step_dir} plans anchored placements but never sees emphasis")
        names = {i["name"]: i.get("required", True)
                 for i in m["interface"]["inputs"]}
        assert names.get("prosody_analysis") is False, (
            f"{step_dir} reads view:emphasis without declaring the "
            f"optional prosody_analysis input it is built from")
