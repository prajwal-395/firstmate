"""A pin may overrule the computation. It may not do it in silence.

The defect, 2026-09-11: nineteen caption pins recorded during one
rebuild encoded a position a later fix superseded. They outranked the
computation by design and said nothing, so the only way to find out a
pin had gone stale was to look at the picture and disbelieve it.

Measured on the field test they turned out to be INERT as well - not one
of them matched a caption id on any current reel - which is the other
half of the same silence: a pin that matches nothing is as quiet as one
that matches wrongly, and a re-render can make it match again tomorrow.

Neither half changes who wins. A pin still beats the computation; that
is what a pin is for.
"""

import pytest

from library.tools import overlay_intent

COMPUTED = {"scaling": 1, "pan": 0.0, "tilt": -435.0}
FRAME = (1080, 1920)
CANVAS = (840, 480)


def _pin(tilt):
    """A version-2 pin naming the place that `tilt` reaches on CANVAS.

    Spelled as a place, because that is what a pin is now; written from
    a tilt so the fixtures below stay the numbers the incident was
    reported in.
    """
    # Positive Tilt moves the clip UP, so the centre goes the other way.
    return {"canvas_centre": [540.0, 960.0 - tilt * (CANVAS[1] / FRAME[1])],
            "scaling": 1}


def _resolve(kind, segment_id, computed, intent):
    return overlay_intent.resolve(kind, segment_id, computed, intent,
                                  canvas=CANVAS, frame=FRAME)


def test_a_pin_still_wins():
    intent = {"seg-1": _pin(-870.0)}
    placement, provenance = _resolve(
        "caption", "seg-1", COMPUTED, intent)
    assert provenance == "declared"
    assert placement["tilt"] == -870.0


def test_a_material_disagreement_is_reported(capsys):
    intent = {"seg-1": _pin(-870.0)}
    _resolve("caption", "seg-1", COMPUTED, intent)
    err = capsys.readouterr().err
    assert "OVERRULES" in err
    assert "-870" in err and "-435" in err
    # It says which way and by how much, so a reader can judge it.
    assert "-435" in err


def test_agreement_says_nothing(capsys):
    """Below the threshold the two answers are the same place, and
    saying so on every overlay would teach a reader to skip the line."""
    intent = {"seg-1": _pin(-437.0)}
    _resolve("caption", "seg-1", COMPUTED, intent)
    assert capsys.readouterr().err == ""


def test_an_unpinned_overlay_reports_nothing(capsys):
    placement, provenance = _resolve(
        "caption", "seg-2", COMPUTED, {"seg-1": _pin(-435.0)})
    assert provenance == "computed" and placement == COMPUTED
    assert capsys.readouterr().err == ""


def test_a_pin_with_nothing_to_compare_against_is_not_an_alarm(capsys):
    """A fresh placement with no computed value is not a disagreement -
    there is no second answer for the pin to overrule."""
    intent = {"seg-1": _pin(-870.0)}
    _resolve("caption", "seg-1", None, intent)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("field,value", [
    ("pan", 900.0), ("tilt", -870.0), ("scaling", 42),
])
def test_every_placement_field_is_compared(field, value):
    pinned = dict(COMPUTED)
    pinned[field] = value
    note = overlay_intent.disagreement(pinned, COMPUTED, "seg-1")
    assert field in note and "OVERRULES" in note


def test_the_threshold_is_stated_in_stored_units():
    """A number, not a formula, and small enough that a real stale pin
    cannot hide under it: the caption defect missed by 435 units."""
    assert overlay_intent.INTENT_DISAGREEMENT_UNITS < 435


# ── The inert half ─────────────────────────────────────────────────

def test_pins_that_matched_nothing_are_named():
    intent = {
        "sub_akshita_old-id_1_aaaa": {"scaling": 1, "pan": 0.0, "tilt": -870.0},
        "sub_akshita_old-id_2_bbbb": {"scaling": 1, "pan": 0.0, "tilt": -870.0},
        "mg_live_one": {"scaling": 1, "pan": 0.0, "tilt": 895.0},
    }
    stale = overlay_intent.unmatched(intent, ["mg_live_one"])
    assert stale == ["sub_akshita_old-id_1_aaaa", "sub_akshita_old-id_2_bbbb"]


def test_the_caption_kind_default_is_never_called_stale():
    """A kind default that placed no caption on one reel is not stale -
    that reel just had no caption for it."""
    intent = {overlay_intent.CAPTION_KIND: dict(COMPUTED)}
    assert overlay_intent.unmatched(intent, []) == []


def test_nothing_declared_is_nothing_stale():
    assert overlay_intent.unmatched(None, ["a"]) == []
    assert overlay_intent.unmatched({}, []) == []
