"""The colourist decides the grade, and the two halves compose exactly.

Project 001 measured nine clips across a 2.7x luma spread and wrote the
IDENTITY CDL on all nine, because normalisation was reachable only
through an `exposure_reference` that only a brand template declares and
001 names no template.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import color_correction as cc  # noqa: E402
from library.tools.series_look import NEUTRAL_CDL, resolve_look  # noqa: E402

DECLARED = {
    "name": "test_declaration",
    "cdl": {
        "slope": [1.04, 1.0, 0.95],
        "offset": [0.012, 0.01, 0.004],
        "power": [0.985, 0.995, 1.012],
        "saturation": 0.95,
    },
}

CLIPS = ["clip_011", "clip_013", "clip_017"]


def _read(entries):
    return cc.read_corrections(entries, CLIPS)


# ── The composition is exact, in a stated order ──────────────────────

def test_a_project_with_no_look_gets_the_correction_as_the_whole_grade():
    corrections, dropped = _read([
        {"clip_id": "clip_017", "exposure_stops": 1.0,
         "why": "car interior at dusk, brought up under the plaza"},
    ])
    assert not dropped
    cdl = cc.compose_cdl(None, corrections[0], NEUTRAL_CDL)
    # 2 ** 1.0 == 2.0, moving all three channels equally so the hue
    # balance is untouched.
    assert cdl["slope_r"] == cdl["slope_g"] == cdl["slope_b"] == 2.0
    assert cdl["offset_r"] == 0.0 and cdl["power_r"] == 1.0




def test_every_term_composes_the_way_the_legend_says():
    """The arithmetic this module exists for. Slope multiplies, offset
    adds, power multiplies, saturations multiply - exact for the serial
    order the docstring fixes."""
    look = resolve_look(DECLARED)
    corrections, _ = _read([{
        "clip_id": "clip_013",
        "exposure_stops": -0.5,
        "slope": [1.1, 1.0, 0.9],
        "offset": [0.01, 0.0, -0.005],
        "power": [1.05, 1.0, 0.95],
        "saturation": 1.2,
        "why": "warming a flat midday shot",
    }])
    cdl = cc.compose_cdl(look, corrections[0], NEUTRAL_CDL)
    gain = 2.0 ** -0.5
    assert cdl["slope_r"] == round(gain * 1.1 * 1.04, 4)
    assert cdl["offset_r"] == round(0.012 + 0.01, 4)
    assert cdl["power_r"] == round(0.985 * 1.05, 4)
    assert cdl["saturation"] == round(0.95 * 1.2, 4)


# ── Refused reaches the model; under-specified is dropped ────────────

def test_a_value_the_cdl_cannot_take_is_refused_by_name():
    """Refusing is how a violation reaches the model that wrote it
    (post_bridge_retry); dropping is how it goes quiet."""
    with pytest.raises(cc.ColorCorrectionRefused, match="slope"):
        _read([{"clip_id": "clip_011", "slope": [1.0, 1.0], "why": "two"}])
    # A term nothing reads is refused too, and says where it lives.
    with pytest.raises(cc.ColorCorrectionRefused, match="temperature"):
        _read([{"clip_id": "clip_011", "temperature": 200, "why": "warmer"}])


def test_an_all_neutral_entry_is_dropped_rather_than_written_as_a_no_op():
    """A no-op CDL is what the last run wrote on all nine clips, and
    Resolve drew no node for any of them."""
    corrections, dropped = _read([
        {"clip_id": "clip_011", "exposure_stops": 0.0,
         "slope": [1.0, 1.0, 1.0], "why": "leave it"}])
    assert not corrections
    assert [d.reason for d in dropped] == ["no_correction_terms"]




# ── The four absences are four, not one ──────────────────────────────

def test_a_judged_no_correction_is_not_an_absent_decision():
    """The whole point. `[]` from a colourist who looked and `[]` because
    no colourist ran are different facts; the old output could not tell
    them apart."""
    assert cc.planning_basis(True, [], []) == cc.JUDGED_NO_CORRECTION_NEEDED
    assert cc.planning_basis(False, [], []) == cc.NO_CORRECTION_DECISION
    # And a plan whose every entry was discarded is a third thing again.
    assert cc.planning_basis(
        True, [], [cc.Dropped("no_reason_given", "x", {})]
    ) == cc.EVERY_ENTRY_DROPPED


