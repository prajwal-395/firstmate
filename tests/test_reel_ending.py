"""Where a reel ends, what draws over its tail, and what refuses.

Reel 13, 2026-09-11: an ending expressed as a keep-range EXTENSION
crossed the master's own cut, admitted twelve frames of the next
speaker, and took the 18-frame switch-off with it - the tail element
no longer fitted the shot it landed on, so `treatment_verify` undid
it. One cause, both of the captain's complaints. Every test below is
synthetic under `tmp_path` (AGENTS.md 8); none reaches Resolve.
"""

import json
import os
from types import SimpleNamespace

import pytest

from library.tools import reel_build
from library.tools import reel_ending


def _words(*tokens, start=10.0):
    words, cursor = [], start
    for token in tokens:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return words


def _transcript():
    return {"segments": [{
        "text": "alpha beta gamma delta epsilon",
        "words": _words("alpha", "beta", "gamma", "delta", "epsilon")}]}


def _shots():
    """Two back-to-back master shots, speaker A then speaker B."""
    return [
        SimpleNamespace(timeline_start=10.0, timeline_end=11.5,
                        source_in=50.0, track_index=1, speaker="A"),
        SimpleNamespace(timeline_start=11.5, timeline_end=13.0,
                        source_in=80.0, track_index=2, speaker="B"),
    ]


def _ending(**over):
    base = {"reel": "Reel 13 - x", "ends_on": {"anchor_phrase": "beta gamma"},
            "tail_element": "tv_power_tail", "reason": "captain marker"}
    base.update(over)
    return base


def _declare(root, endings):
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    with open(os.path.join(external, "reel_ending.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"version": 1, "endings": endings}, handle)


# ── The defect, reproduced and then closed ─────────────────────────

def test_an_extended_range_admits_the_next_shot():
    """The mechanism, in one assertion: a keep range reaching past the
    master's cut does not lengthen the shot that is playing - it adds
    the next one."""
    probe = reel_build.placements([(10.0, 12.1)], _shots(), 24.0)
    assert len(probe) == 2
    assert probe[-1]["clip"].speaker == "B"
    # Fourteen frames of the next speaker, from asking for 0.6s of room.
    assert round(
        (probe[-1]["source_out"] - probe[-1]["source_in"]) * 24.0) == 14


def test_declared_ending_truncates_to_its_shot():
    transcript = _transcript()
    ranges = [(10.0, 12.1)]
    probe = reel_build.placements(ranges, _shots(), 24.0)
    out, record = reel_ending.apply_ending(
        ranges, probe, transcript, _ending(), 24.0)
    assert out == [(10.0, 11.5)]
    assert len(record["applied"]) == 1 and not record["stale"]
    assert record["applied"][0]["now"] == [10.0, 11.5]
    # And the reel now places ONE shot: the next speaker is gone.
    after = reel_build.placements(out, _shots(), 24.0)
    assert [p["clip"].speaker for p in after] == ["A"]


def test_an_ending_never_extends():
    """The rule the defect teaches. A declaration that would reach past
    the plan's own end holds instead - an ending removes seconds or it
    does nothing."""
    transcript = _transcript()
    ranges = [(10.0, 11.5)]
    probe = reel_build.placements(ranges, _shots(), 24.0)
    out, record = reel_ending.apply_ending(
        ranges, probe, transcript, _ending(), 24.0)
    assert out == [(10.0, 11.5)]
    assert not record["applied"] and len(record["held"]) == 1


def test_no_declaration_passes_the_ranges_through():
    ranges = [(10.0, 12.1)]
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, _shots(), 24.0),
        _transcript(), None, 24.0)
    assert out == ranges
    assert record == {"applied": [], "held": [], "stale": []}


def test_words_the_reel_no_longer_plays_report_stale():
    transcript = _transcript()
    ranges = [(10.0, 12.1)]
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, _shots(), 24.0),
        transcript, _ending(ends_on={"anchor_phrase": "words never said"}),
        24.0)
    assert out == ranges and not record["applied"]
    assert len(record["stale"]) == 1
    assert "STALE" in record["stale"][0]["reason"]


# ── The tail element ───────────────────────────────────────────────

def test_tail_element_room_comes_from_its_own_module():
    from library.tools.fusion.played_window import frames_for_ramp
    from library.tools.tv_power import switch_off_frames

    timing = switch_off_frames()
    ramp = (timing["collapse_frames"] + timing["dot_frames"]
            + timing["decay_frames"])
    # Frames of CLIP, which is one more than frames of RAMP - the
    # arithmetic belongs to the check that enforces it, and a hold of
    # exactly the ramp's length is undone as `never_settles`.
    expected = frames_for_ramp(ramp)
    assert expected == ramp + 1
    assert reel_ending.tail_room_frames(_ending()) == expected
    # A project that redeclares the animation redeclares its room. The
    # declaration names the SHAPE, not a half: one shape, two directions
    # (captain, 2026-09-11).
    look = {"power": {"decay_frames": 30}}
    assert reel_ending.tail_room_frames(_ending(), look) == frames_for_ramp(
        ramp - timing["decay_frames"] + 30)
    # `none` is the absence of decoration and needs nothing.
    assert reel_ending.tail_room_frames(_ending(tail_element="none")) == 0
    assert reel_ending.tail_effects(_ending(tail_element="none")) == {}


def test_a_shot_too_short_for_its_tail_element_refuses_by_name():
    """The check that was missing. Twelve frames of the next speaker
    cannot carry an 18-frame switch-off, and the build says so instead
    of shipping the reel without it."""
    short = [{"source_in": 0.0, "source_out": 12 / 24.0}]
    with pytest.raises(reel_ending.TailElementHasNoRoom) as excinfo:
        reel_ending.assert_tail_fits(short, _ending(), 24.0)
    message = str(excinfo.value)
    assert "19 frames" in message and "plays 12" in message
    # And a shot with room passes, reporting what it measured.
    roomy = [{"source_in": 0.0, "source_out": 310 / 24.0}]
    measured = reel_ending.assert_tail_fits(roomy, _ending(), 24.0)
    assert measured == {"element": "tv_power_tail", "tail_frames": 19,
                        "shot_frames": 310, "hold": "none", "fits": True}


# ── The declaration ────────────────────────────────────────────────

def test_declaration_round_trips_and_matches_a_staging_name(tmp_path):
    root = str(tmp_path)
    _declare(root, [_ending()])
    assert reel_ending.resolve_ending(root, "Reel 13 - x") == _ending()
    # A staging container resolves to the same declaration, which is
    # what makes an ending survive the rebuild that promotes it.
    assert reel_ending.resolve_ending(
        root, "Reel 13 - x (scratch fm) (rebuild staging)") is not None
    assert reel_ending.resolve_ending(root, "Reel 28 - y") is None
    # A project that declares nothing gets nothing.
    assert reel_ending.resolve_ending(str(tmp_path / "other"), "R") is None


@pytest.mark.parametrize("bad,match", [
    ([{"ends_on": {"anchor_phrase": "x"}, "reason": "r"}], "names no reel"),
    ([{"reel": "R", "reason": "r"}], "no ends_on"),
    ([{"reel": "R", "ends_on": {"anchor_phrase": "x"},
       "tail_element": "explosion", "reason": "r"}], "tail_element"),
])
def test_malformed_declarations_refuse(bad, match):
    with pytest.raises(reel_ending.ReelEndingError, match=match):
        reel_ending.validate_endings(bad)


def test_an_unreadable_declaration_refuses_rather_than_defaulting(tmp_path):
    root = str(tmp_path)
    external = os.path.join(root, "external")
    os.makedirs(external)
    with open(os.path.join(external, "reel_ending.json"), "w",
              encoding="utf-8") as handle:
        handle.write("{not json")
    with pytest.raises(reel_ending.ReelEndingError, match="cannot be read"):
        reel_ending.load_endings(root)


def test_an_ending_that_would_empty_the_reel_refuses():
    transcript = {"segments": [{
        "text": "alpha beta", "words": _words("alpha", "beta", start=10.0)}]}
    shots = [SimpleNamespace(timeline_start=10.0, timeline_end=10.6,
                             source_in=50.0, track_index=1, speaker="A"),
             SimpleNamespace(timeline_start=10.6, timeline_end=13.0,
                             source_in=80.0, track_index=2, speaker="B")]
    ranges = [(10.0, 10.6), (11.0, 12.0)]
    probe = reel_build.placements(ranges, shots, 24.0)
    with pytest.raises(reel_ending.ReelEndingError, match="to nothing"):
        reel_ending.apply_ending(
            ranges, probe, transcript,
            _ending(ends_on={"anchor_phrase": "alpha beta"}), 24.0)


# ── The freeze: hold the last frame, play the element over it ──────

def _freeze_ending(**over):
    base = _ending(tail_hold="freeze")
    base.update(over)
    return base


def test_a_freeze_holds_the_last_frame_for_the_elements_own_length():
    """The captain chose the freeze on 2026-09-11 after the switch-off
    played across his whole closing line. The hold's length is the
    ELEMENT's, so "it plays entirely after the words" is true by
    construction rather than by a tuned number."""
    placements = [{"clip": _shots()[0], "source_in": 50.0,
                   "source_out": 50.0 + 310 / 24.0,
                   "snapped_record": 1599, "record": 1599 / 24.0}]
    freeze = reel_ending.plan_freeze(placements, _freeze_ending(), 24.0)
    assert freeze is not None
    assert freeze.duration_frames == reel_ending.tail_room_frames(
        _freeze_ending()) == 19
    # It begins on the frame AFTER the live tail, and holds the last
    # frame that actually plays - never one the reel does not show.
    assert freeze.reel_start_frame == 1599 + 310
    assert freeze.end_frame == 1599 + 310 + 19
    assert freeze.held_source_seconds == pytest.approx(
        50.0 + 310 / 24.0 - 1 / 24.0)
    assert freeze.element == "tv_power_tail"


def test_the_freeze_placement_is_a_picture_clip_that_speaks_nothing():
    placements = [{"clip": _shots()[0], "source_in": 50.0,
                   "source_out": 50.0 + 310 / 24.0,
                   "snapped_record": 1599, "record": 1599 / 24.0}]
    freeze = reel_ending.plan_freeze(placements, _freeze_ending(), 24.0)
    place = reel_ending.freeze_placement(freeze, 24.0)
    assert place["snapped_record"] == 1909
    assert place["track_index"] == _shots()[0].track_index
    assert place["clip"].track_type == "video"
    assert place["source_out"] == pytest.approx(19 / 24.0)
    # An empty master span: a held frame speaks no words, so no
    # word-anchored pass may claim it as a shot - it inherits the
    # treatment of the shot it holds instead.
    assert place["master"] == (0.0, 0.0)
    assert place["freeze"] is True


