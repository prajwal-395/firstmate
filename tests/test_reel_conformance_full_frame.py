"""What the conformance checks do when picture is a GRAPHIC, not footage.

A full-frame element replaces picture on V1 for its own stretch of reel
time (``library/tools/full_frame_element.py``).  Three checks in
``reel_conformance_verifier`` were written when every V1 item was a
frame of the master, and all three would report a FALSE ERROR on a
correct build:

- ``check_item_count`` (F4) counts V1/V2 items against the plan's
  placements, so the card reads as an extra item and its seconds land on
  whichever speaker owns V1;
- ``check_delivered_framing`` (F12) looks each item's source up in the
  catalog, and a rendered card is not in it;
- ``check_plan_describes_timeline`` is frame-exact, and the card's frames
  come from no keep range.

This file plants each of those and asserts the check now reads it right -
and, in the same breath, that the NEW gate (F13) can still fail in both
directions.  A check taught to ignore a card would be a check that cannot
see one, which is the trade this file exists to refuse (AGENTS.md 10.4).
"""
from __future__ import annotations

import pytest

from library.tools.reel_conformance_verifier import (
    FindingClass,
    PlannedCard,
    PlannedPlacement,
    TimelineItem,
    card_items,
    check_delivered_framing,
    check_full_frame_cards,
    check_item_count,
    check_picture_holes,
    check_plan_describes_timeline,
)

FPS = 24000 / 1001
CARD_FRAMES = 53          # 2.2s at 23.976, as the planner rounds it
CARD_NAME = "reel_07_card_01"
CARD_FILE = f"/p/pipeline_output/scratch/reel_cards/{CARD_NAME}.mov"
FOOTAGE = "/m/LCATL0013.MXF"


def _item(track_index: int, start: int, frames: int,
          source_file: str = FOOTAGE, speaker="Akshita",
          transform=None) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=track_index,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=frames,
        source_file=source_file, speaker=speaker,
        name=source_file.rsplit("/", 1)[-1],
        transform=dict(transform or {}))


def _card_item(start: int = 0, frames: int = CARD_FRAMES,
               track_index: int = 1, transform=None) -> TimelineItem:
    return _item(track_index, start, frames, source_file=CARD_FILE,
                 speaker=None, transform=transform)


def _card(start_frame: int = 0,
          frames: int = CARD_FRAMES) -> PlannedCard:
    return PlannedCard(render_name=CARD_NAME, placement="head",
                       reel_start_frame=start_frame, duration_frames=frames)


IDENTITY = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
            "CropLeft": 0.0, "CropRight": 0.0,
            "CropTop": 0.0, "CropBottom": 0.0}


# ── Identifying a card ───────────────────────────────────────────────


def test_a_card_is_identified_by_its_render_name_not_by_a_catalog_miss():
    """Positive identification. "Not in the catalog" would make every
    relink gap look like a full-frame element."""
    items = [_card_item(), _item(1, CARD_FRAMES, 100),
             _item(1, CARD_FRAMES + 100, 100, source_file="/m/unknown.MXF")]
    found = card_items(items, [_card()])
    assert list(found) == [CARD_NAME]
    assert found[CARD_NAME].source_file == CARD_FILE


# ── F13, both directions ─────────────────────────────────────────────


def test_a_declared_card_that_is_not_on_the_timeline_fails():
    findings = check_full_frame_cards(
        "Reel 07", [_card()], [_item(1, 0, 400)], 1080, 1920, FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F13]
    assert "no item on the timeline is it" in findings[0].message


def test_a_card_nobody_declared_fails():
    """The out-of-band append `bookends` refuses on the master, caught on
    the reels path."""
    findings = check_full_frame_cards(
        "Reel 07", [], [_card_item(), _item(1, CARD_FRAMES, 400)],
        1080, 1920, FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F13]
    assert "no declaration accounts for it" in findings[0].message


def test_a_card_placed_above_the_picture_tracks_fails():
    """The replace-versus-overlay ruling, as a gate."""
    findings = check_full_frame_cards(
        "Reel 07", [_card()], [_card_item(track_index=4)], 1080, 1920, FPS)
    assert any("belongs on V1" in f.message for f in findings)
    assert any("cannot turn down" in f.message for f in findings)


# ── F4: the card is not an unplanned picture item ────────────────────


def _placements(n: int, frames: int) -> list:
    return [PlannedPlacement(track_index=1, speaker="Akshita",
                             record_seconds=i * frames / FPS,
                             source_in=0.0, source_out=frames / FPS,
                             source_file=FOOTAGE)
            for i in range(n)]


def test_f4_is_clean_once_the_plan_carries_the_card():
    items = [_card_item()] + [_item(1, CARD_FRAMES + i * 400, 400)
                              for i in range(2)]
    findings = check_item_count("Reel 07", _placements(2, 400), items, FPS,
                                cards=[_card()])
    assert findings == [], [f.message for f in findings]


def test_f4_still_catches_a_genuinely_dropped_clip_with_a_card_present():
    """Taught to ignore the card, not taught to ignore everything."""
    items = [_card_item(), _item(1, CARD_FRAMES, 400)]
    findings = check_item_count("Reel 07", _placements(2, 400), items, FPS,
                                cards=[_card()])
    assert any("planned 2 picture items, found 1" in f.message
               for f in findings)


# ── F1: a card abutting the first clip is not a hole ─────────────────


def test_a_one_frame_gap_after_the_card_is_still_a_hole():
    findings = check_picture_holes(
        "Reel 07", [_card_item(), _item(1, CARD_FRAMES + 1, 400)])
    assert [f.finding_class for f in findings] == [FindingClass.F1]


# ── F12: the card is graded against the whole frame ──────────────────


LETTERBOX_SIZES = {FOOTAGE: {"width": 3840, "height": 2160, "rotation": 0}}


def test_f12_grades_a_full_frame_card_against_the_whole_frame_and_passes():
    """A card drawn at the delivery size with an identity transform fills
    the frame, whatever the project declares about its FOOTAGE."""
    findings = check_delivered_framing(
        "Reel 07", [_card_item(transform=IDENTITY),
                    _item(1, CARD_FRAMES, 400, transform=IDENTITY)],
        1080, 1920, source_sizes=LETTERBOX_SIZES, declared_intent=0.0,
        cards=[_card()])
    assert findings == [], [f.message for f in findings]


def test_f12_fails_a_card_that_does_not_fill_the_frame():
    """The other direction: the gate is not an exemption."""
    shrunk = dict(IDENTITY, ZoomX=0.5, ZoomY=0.5)
    findings = check_delivered_framing(
        "Reel 07", [_card_item(transform=shrunk)],
        1080, 1920, source_sizes={}, declared_intent=0.0, cards=[_card()])
    assert [f.finding_class for f in findings] == [FindingClass.F12]
    assert findings[0].severity == "error"


# ── PLAN-MISMATCH: the card's frames come from no keep range ─────────


def test_the_plan_mismatch_gate_still_fires_on_a_real_difference():
    keep = [(100.0, 120.0)]
    body = round(120.0 * FPS) - round(100.0 * FPS)
    findings = check_plan_describes_timeline(
        "Reel 07", keep, body + CARD_FRAMES - 9, FPS,
        card_frames=CARD_FRAMES)
    assert [f.finding_class for f in findings] == [FindingClass.PLAN_MISMATCH]
    assert "-9 frames" in findings[0].message


# ── The lead: picture and captions move together, or not at all ──────


def test_placements_shift_by_the_lead_in_whole_frames():
    from library.tools.reel_build import placements

    class _Clip:
        track_type = "video"
        track_index = 1
        speaker = "Akshita"
        source_file = FOOTAGE
        timeline_start = 10.0
        timeline_end = 20.0
        source_in = 100.0

    plain = placements([(10.0, 20.0)], [_Clip()], FPS)
    shifted = placements([(10.0, 20.0)], [_Clip()], FPS,
                         lead_frames=CARD_FRAMES)
    assert plain[0]["snapped_record"] == 0
    assert shifted[0]["snapped_record"] == CARD_FRAMES
    # Which frames PLAY is unchanged - a card in front moves where a clip
    # lands, never what is in it.
    assert plain[0]["source_in"] == shifted[0]["source_in"]
    assert plain[0]["source_out"] == shifted[0]["source_out"]


def test_the_spine_moves_the_reel_clock_and_leaves_the_source_clock_alone():
    """A word's place in the raw clip does not move when something is
    placed in front of it (AGENTS.md 6)."""
    from library.tools.reel_spine import spine_for_reel

    transcript = {"segments": [{
        "speaker": "Akshita", "timeline_start": 10.0, "timeline_end": 14.0,
        "text": "ranking number one on Google but invisible to AI entirely",
        "clip_id": "clip_001", "source_file": FOOTAGE,
        "source_start": 100.0, "source_end": 104.0,
        "words": [{"word": w, "start": 10.0 + i * 0.4,
                   "end": 10.35 + i * 0.4, "timed": True}
                  for i, w in enumerate(
                      "ranking number one on Google but invisible to AI "
                      "entirely".split())],
    }]}

    class _Moment:
        number = 7
        timeline_start = 10.0
        timeline_end = 14.0
        speakers = ("Akshita",)

    plain = spine_for_reel(_Moment(), transcript, [(10.0, 14.0)])
    shifted = spine_for_reel(_Moment(), transcript, [(10.0, 14.0)],
                             lead_seconds=2.2)
    assert plain["structure"] and shifted["structure"]
    for before, after in zip(plain["structure"], shifted["structure"]):
        assert after["timeline_start"] == pytest.approx(
            before["timeline_start"] + 2.2)
        assert after["timeline_end"] == pytest.approx(
            before["timeline_end"] + 2.2)
        assert after["source_start"] == before["source_start"]
        assert after["source_end"] == before["source_end"]
        assert after["word_timestamps"] == before["word_timestamps"]


def test_a_tail_card_abuts_the_last_clip_on_a_range_that_does_not_round_evenly():
    """The head card's one-frame bug, checked at the OTHER end.

    A tail card placed from `round(seconds * fps)` lands a frame away
    from the last clip on ranges whose edges do not round evenly - a
    black hole if it is late, an overlap if it is early. Both sides are
    integer frame arithmetic instead, so the ranges below still abut
    exactly.

    Routed through `reel_build.plan_cards` rather than
    `plan_reel_cards` directly, because the number that can regress is
    the one the CALLER computes: summing the ranges' seconds and
    rounding once is the mistake, and a test that passes the correct
    frame count in cannot see it (AGENTS.md 10.4).
    """
    from library.tools.reel_build import placements, plan_cards
    from library.tools import full_frame_element as ffe

    # CHOSEN so the two arithmetics disagree by a frame: summing the
    # ranges' SECONDS and rounding once gives 562 frames, while rounding
    # each edge - which is what `placements` does - gives 563. Without
    # that disagreement this test could not fail.
    ranges = [(29.881, 42.397), (134.695, 145.611)]
    per_edge = sum(int(round(b * FPS)) - int(round(a * FPS))
                   for a, b in ranges)
    summed_seconds = int(round(sum(b - a for a, b in ranges) * FPS))
    assert per_edge == 563 and summed_seconds == 562

    class _Clip:
        track_type = "video"
        track_index = 1
        speaker = "Akshita"
        source_file = FOOTAGE
        timeline_start = 0.0
        # Spans BOTH ranges: a clip that covered only the first would
        # place 301 frames of picture and let the assertion below pass
        # for the wrong reason.
        timeline_end = 200.0
        source_in = 0.0

    class _Moment:
        number = 7
        speakers = ("Akshita",)

    declarations = ffe.declared_elements({"full_frame_elements": [{
        "element": "full_frame_card", "placement": "tail",
        "duration_seconds": 1.37, "background": "#000000",
        "font_family": "Montserrat",
        "runs": [{"text": "the end", "type_role": "micro",
                  "colour": "#FFFFFF"}]}]})

    cards = plan_cards(_Moment(), {"segments": []}, ranges, "", FPS,
                       declarations=declarations, width=1080, height=1920)
    assert [c.placement for c in cards] == ["tail"]

    placed = placements(ranges, [_Clip()], FPS, lead_frames=0)
    last_end = max(p["snapped_record"]
                   + int(round((p["source_out"] - p["source_in"]) * FPS))
                   for p in placed)
    assert last_end == per_edge, (
        "the picture itself must end on the per-edge frame count, or "
        "this test is measuring the wrong thing")
    assert cards[0].reel_start_frame == last_end, (
        "the tail card must start on the frame the picture ends, not a "
        "rounding away from it")

    items = [_item(1, p["snapped_record"],
                   int(round((p["source_out"] - p["source_in"]) * FPS)))
             for p in placed]
    items.append(_card_item(start=cards[0].reel_start_frame,
                            frames=cards[0].duration_frames))
    assert check_picture_holes("Reel 07", items) == []
