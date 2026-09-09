"""The whole-span element: one declaration covering the reel's whole body.

The animated-reel lane (``docs/ANIMATED_REEL_CEILING.md``) covered a
44.5s reel with SEVEN back-to-back head cards, every boundary on the
reel's own edit points - "the vocabulary has no word for what was
built".  ``full_frame_span`` is that word: one declaration whose
segments cover the keep ranges one by one.

Every gate here is proved in BOTH directions, like
``tests/test_full_frame_element.py``: a declaration that should be
refused is asserted to raise AND the neighbouring valid one is asserted
to pass.  And the drawing half is proved by a RENDER, not by asserting
a node exists: ``test_each_segment_draws_its_own_copy`` renders a real
two-segment span and reads per-segment ink back off the frames.  A
source parse would pass against a build with the segment copy
deliberately crossed; per-segment colours do not.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess

import pytest

from library.tools import full_frame_element as ffe
from library.tools.reel_conformance_verifier import (
    CARD_NAME_SHAPE,
    FindingClass,
    PlannedCard,
    TimelineItem,
    card_items,
    check_full_frame_cards,
    check_item_count,
    check_picture_holes,
)

FPS = 24000 / 1001

VALID = {
    "element": "full_frame_span",
    "placement": "span",
    "background": "#101014",
    "font_family": "Montserrat",
    "segments": [
        {"runs": [
            {"text": "First kept sentence",
             "type_role": "display", "colour": "#FFFFFF"}]},
        {"runs": [
            {"bind": "range_line",
             "type_role": "micro", "colour": "#FFAA4D"}]},
    ],
}


def declare(**overrides):
    entry = copy.deepcopy(VALID)
    entry.update(overrides)
    return {"full_frame_elements": [entry]}


def _transcript():
    return {"segments": [
        {"speaker": "Akshita", "timeline_start": 10.0, "timeline_end": 16.0,
         "text": "So ranking number one on Google but invisible to AI",
         "words": [
             {"word": w, "start": 10.0 + i * 0.4, "end": 10.35 + i * 0.4,
              "timed": True}
             for i, w in enumerate(
                 "So ranking number one on Google but invisible to AI".split())
         ]},
        {"speaker": "Craig", "timeline_start": 20.0, "timeline_end": 24.0,
         "text": "And the second kept sentence lands here",
         "words": [
             {"word": w, "start": 20.0 + i * 0.4, "end": 20.35 + i * 0.4,
              "timed": True}
             for i, w in enumerate(
                 "And the second kept sentence lands here".split())
         ]},
    ]}


RANGES = [(10.0, 16.0), (20.0, 24.0)]


class _Moment:
    number = 3
    speakers = ("Akshita", "Craig")


def _facts():
    return ffe.ReelFacts.from_moment(
        _Moment(), RANGES, _transcript(), opening_seconds=3.0)


def _plan(declarations=None, ranges=RANGES, transcript=None,
          resolve_asset=None):
    declarations = (ffe.declared_elements(declare())
                    if declarations is None else declarations)
    body = sum(int(round(e * FPS)) - int(round(s * FPS))
               for s, e in ranges)
    return ffe.plan_reel_cards(
        declarations, _facts(), body, FPS,
        ranges=list(ranges),
        transcript=_transcript() if transcript is None else transcript,
        resolve_asset=resolve_asset)


# ── The declaration ──────────────────────────────────────────────


def test_the_valid_span_declaration_is_accepted():
    """The other half of every refusal below."""
    normalised = ffe.declared_elements(declare())
    assert normalised[0]["element"] == "full_frame_span"
    assert normalised[0]["placement"] == "span"
    assert normalised[0]["duration_seconds"] is None
    assert len(normalised[0]["segments"]) == 2


@pytest.mark.parametrize("overrides,expect", [
    ({"placement": "head"}, "its placement is 'span'"),
    ({"placement": "tail"}, "its placement is 'span'"),
    ({"placement": "middle"}, "its placement is 'span'"),
    ({"placement": None}, "its placement is 'span'"),
    ({"duration_seconds": 44.5}, "a second source of truth"),
    ({"runs": [{"text": "x", "type_role": "display",
                "colour": "#FFF"}]}, "one copy block cannot say"),
    ({"segments": []}, "no `segments`"),
    ({"segments": None}, "no `segments`"),
    ({"background": ""}, "no `background`"),
    ({"font_family": ""}, "no `font_family`"),
    ({"image": "logo.png"}, "names its own image"),
    ({"image_width": 200}, "names its own image"),
])
def test_a_malformed_span_is_refused_by_name(overrides, expect):
    with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
        ffe.declared_elements(declare(**overrides))


def test_a_card_may_not_take_the_span_placement():
    """The new placement is a different declaration, not a longer card.

    A whole-span single card stays refused exactly as the ceiling lane
    met it (past 30s "it is not a card in front of a reel") - and
    `span` on a card refuses by name rather than re-timimg it.
    """
    card = {
        "element": "full_frame_card",
        "placement": "span",
        "duration_seconds": 2.0,
        "background": "#101014",
        "font_family": "Montserrat",
        "runs": [{"text": "x", "type_role": "display",
                  "colour": "#FFF"}],
    }
    with pytest.raises(ffe.FullFrameDeclarationError, match="not a long card"):
        ffe.declared_elements({"full_frame_elements": [card]})


def test_a_mid_reel_card_is_still_refused():
    """The load-bearing refusal this change must not weaken."""
    card = {
        "element": "full_frame_card",
        "placement": "middle",
        "duration_seconds": 2.0,
        "background": "#101014",
        "font_family": "Montserrat",
        "runs": [{"text": "x", "type_role": "display",
                  "colour": "#FFF"}],
    }
    with pytest.raises(ffe.FullFrameDeclarationError, match="no mid-reel"):
        ffe.declared_elements({"full_frame_elements": [card]})


def test_a_segment_with_no_runs_is_refused():
    entry = copy.deepcopy(VALID)
    entry["segments"] = [{"runs": []}, entry["segments"][1]]
    with pytest.raises(ffe.FullFrameDeclarationError, match="no `runs`"):
        ffe.declared_elements({"full_frame_elements": [entry]})


def test_a_segment_run_is_checked_like_a_card_run():
    """Segments carry the card run shape, including its refusals."""
    entry = copy.deepcopy(VALID)
    entry["segments"] = [{"runs": [{"text": "x", "colour": "#FFF"}]},
                         entry["segments"][1]]
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="typographic weights"):
        ffe.declared_elements({"full_frame_elements": [entry]})


# ── A segment may name the project's own mark ─────────────────────
#
# The card's image slot (tests/test_fullframe_card_image.py), per
# segment: each segment is the unit that renders, so each names its
# own image through the same channel_bug shape - staged lazily,
# refused by name when it resolves to nothing.


def _imaged_span(image="mark.png", image_width=120):
    entry = copy.deepcopy(VALID)
    first = {"runs": [dict(entry["segments"][0]["runs"][0])],
             "image": image}
    if image_width is not None:
        first["image_width"] = image_width
    entry["segments"] = [first, entry["segments"][1]]
    return {"full_frame_elements": [entry]}


def test_a_segment_may_name_a_project_image():
    """The other half of every refusal below."""
    normalised = ffe.declared_elements(_imaged_span())
    segment = normalised[0]["segments"][0]
    assert segment["image"] == "mark.png"
    assert segment["image_width"] == 120.0
    assert normalised[0]["segments"][1]["image"] is None


@pytest.mark.parametrize("image,image_width,expect", [
    ("", None, "brand_assets"),
    ("   ", None, "brand_assets"),
    (42, None, "brand_assets"),
    ("mark.png", 0, "positive number"),
    ("mark.png", -4, "positive number"),
    (None, 120, "does not name"),
])
def test_a_malformed_segment_image_is_refused_by_name(
        image, image_width, expect):
    with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
        ffe.declared_elements(_imaged_span(image, image_width))


# ── Planning ─────────────────────────────────────────────────────


def test_segments_abut_over_the_body_exactly():
    body = sum(int(round(e * FPS)) - int(round(s * FPS))
               for s, e in RANGES)
    planned = _plan()
    assert [c.placement for c in planned] == ["span", "span"]
    assert planned[0].reel_start_frame == 0
    assert planned[1].reel_start_frame == planned[0].duration_frames
    assert sum(c.duration_frames for c in planned) == body
    assert planned[0].reel_end_frame == planned[1].reel_start_frame


def test_span_segments_are_named_spans_not_cards():
    planned = _plan()
    assert [c.render_name for c in planned] == [
        "reel_03_span_01", "reel_03_span_02"]
    assert all(c.element == "full_frame_span" for c in planned)
    for name in ("reel_03_span_01", "reel_03_span_02"):
        assert CARD_NAME_SHAPE.match(name), (
            f"{name} must read as a full-frame element to the verifier")


def test_a_span_contributes_no_lead():
    """It replaces the footage rather than preceding it."""
    from library.tools.reel_build import lead_frames
    assert lead_frames(_plan(), FPS) == 0


# The count check this replaced lives on as two window refusals:
# `test_a_windowless_segment_past_the_last_range_refuses` (more
# segments than ranges) and `test_a_range_no_segment_covers_refuses`
# (fewer) in the windows section below.


def test_a_span_with_no_ranges_to_anchor_to_refuses():
    declarations = ffe.declared_elements(declare())
    with pytest.raises(ffe.FullFrameDeclarationError, match="no keep ranges"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS)


def test_a_span_beside_a_card_refuses():
    """Two pictures on the same seconds is an overlap, not a composite."""
    card = {
        "element": "full_frame_card",
        "placement": "head",
        "duration_seconds": 1.0,
        "background": "#000000",
        "font_family": "Montserrat",
        "runs": [{"text": "x", "type_role": "micro",
                  "colour": "#FFF"}],
    }
    declarations = ffe.declared_elements(
        {"full_frame_elements": [copy.deepcopy(VALID), card]})
    with pytest.raises(ffe.FullFrameDeclarationError, match="whole body"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS,
                            ranges=RANGES, transcript=_transcript())


def test_two_spans_on_one_reel_refuse():
    declarations = ffe.declared_elements(
        {"full_frame_elements": [copy.deepcopy(VALID),
                                 copy.deepcopy(VALID)]})
    with pytest.raises(ffe.FullFrameDeclarationError, match="one span alone"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS,
                            ranges=RANGES, transcript=_transcript())


def test_range_line_quotes_each_range_own_words():
    planned = _plan()
    assert planned[0].props["runs"][0]["text"] == "First kept sentence"
    assert planned[1].props["runs"][0]["text"] == (
        "And the second kept sentence lands here")


def test_range_line_with_nothing_behind_it_refuses_the_span():
    """An empty run is never drawn and never substituted - as cards do."""
    silent = {"segments": [
        {"speaker": "Nobody", "timeline_start": 10.0, "timeline_end": 16.0,
         "text": "no timings here", "words": []},
        {"speaker": "Nobody", "timeline_start": 20.0, "timeline_end": 24.0,
         "text": "nor here", "words": []},
    ]}
    declarations = ffe.declared_elements(declare())
    with pytest.raises(ffe.FullFrameDeclarationError, match="nothing there"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS,
                            ranges=RANGES, transcript=silent)


def test_range_line_outside_a_span_segment_refuses():
    """On a card 'that range' names nothing."""
    card = {
        "element": "full_frame_card",
        "placement": "head",
        "duration_seconds": 1.0,
        "background": "#000000",
        "font_family": "Montserrat",
        "runs": [{"bind": "range_line", "type_role": "micro",
                  "colour": "#FFF"}],
    }
    declarations = ffe.declared_elements(
        {"full_frame_elements": [card]})
    facts = ffe.ReelFacts(reel_number=1, speakers=("A",), opening=(),
                          opening_window=3.0)
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="outside a span segment"):
        ffe.plan_reel_cards(declarations, facts, 100, FPS)


def test_the_facts_refuse_range_line_without_a_segment():
    facts = ffe.ReelFacts(reel_number=1, speakers=("A",), opening=(),
                          opening_window=3.0)
    with pytest.raises(ffe.FullFrameDeclarationError, match="per SEGMENT"):
        facts.binding("range_line")


def test_a_named_segment_image_resolves_to_the_staged_path():
    planned = _plan(ffe.declared_elements(_imaged_span()),
                    resolve_asset=lambda name: f"brand/{name}")
    assert planned[0].props["image"] == "brand/mark.png"
    assert planned[0].props["imageWidth"] == 120.0
    assert "image" not in planned[1].props


def test_an_unstated_segment_width_leaves_no_width_in_props():
    planned = _plan(ffe.declared_elements(_imaged_span(image_width=None)),
                    resolve_asset=lambda name: f"brand/{name}")
    assert planned[0].props["image"] == "brand/mark.png"
    assert "imageWidth" not in planned[0].props


def test_a_span_naming_no_image_carries_no_image_key():
    """Props written before the slot existed render byte-identically."""
    planned = _plan()
    assert all("image" not in c.props and "imageWidth" not in c.props
               for c in planned)


def test_an_unresolvable_segment_image_refuses_the_span():
    """The engine ships no artwork, so there is no substitute to draw."""
    declarations = ffe.declared_elements(_imaged_span(image="missing.png"))
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="not in the project's brand_assets"):
        _plan(declarations, resolve_asset=lambda name: "")


def test_a_segment_image_with_no_lookup_refuses_the_span():
    declarations = ffe.declared_elements(_imaged_span())
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="no way to look a project asset up"):
        _plan(declarations)


# ── Subdividing a range: windows ───────────────────────────────────
#
# Blocker 3 of docs/SPAN_RENDERER_CAPABILITY.md: segments coincided with
# keep ranges, and only those, so a one-range reel got a one-segment
# span - a card, not an animated reel. A segment may now declare a
# `window` - [start, end] in the reel's own master seconds - naming the
# speech beat inside its range that it paces its picture to. The count
# check is REPLACED, not removed: every window must fall inside exactly
# one keep range, windows must not overlap, and they must tile every
# range unless the span declares `allow_gaps`.

ONE_RANGE = [(10.0, 16.0)]

ONE_RANGE_WORDS = (
    "So ranking number one on Google but invisible to AI systems now here"
).split()


def _one_range_transcript():
    return {"segments": [
        {"speaker": "Akshita", "timeline_start": 10.0, "timeline_end": 16.0,
         "text": " ".join(ONE_RANGE_WORDS),
         "words": [
             {"word": w, "start": 10.0 + i * 0.4, "end": 10.35 + i * 0.4,
              "timed": True}
             for i, w in enumerate(ONE_RANGE_WORDS)
         ]},
    ]}


def _one_range_facts():
    return ffe.ReelFacts.from_moment(
        _Moment(), ONE_RANGE, _one_range_transcript(), opening_seconds=3.0)


def _windowed_runs(text=None):
    if text is None:
        return [{"bind": "range_line", "type_role": "display",
                 "colour": "#FFFFFF"}]
    return [{"text": text, "type_role": "display", "colour": "#FFFFFF"}]


def _subdivided(background="#101014", extra=None, segments=None):
    entry = {
        "element": "full_frame_span",
        "placement": "span",
        "background": background,
        "font_family": "Montserrat",
        "segments": segments if segments is not None else [
            {"window": [10.0, 12.0], "runs": _windowed_runs()},
            {"window": [12.0, 14.0], "runs": _windowed_runs()},
            {"window": [14.0, 16.0], "runs": _windowed_runs()},
        ],
    }
    if extra:
        entry.update(extra)
    return {"full_frame_elements": [entry]}


def _plan_one(declarations=None, ranges=ONE_RANGE, transcript=None):
    declarations = (ffe.declared_elements(_subdivided())
                    if declarations is None else declarations)
    body = sum(int(round(e * FPS)) - int(round(s * FPS))
               for s, e in ranges)
    return ffe.plan_reel_cards(
        declarations, _one_range_facts(), body, FPS,
        ranges=list(ranges),
        transcript=(_one_range_transcript()
                    if transcript is None else transcript))


def test_three_windows_tile_one_range():
    """The capability blocker 3 refused: one range, three beats."""
    planned = _plan_one()
    body = int(round(16.0 * FPS)) - int(round(10.0 * FPS))
    assert len(planned) == 3
    assert planned[0].reel_start_frame == 0
    for first, second in zip(planned, planned[1:]):
        assert second.reel_start_frame == first.reel_end_frame
    assert sum(c.duration_frames for c in planned) == body
    assert [c.render_name for c in planned] == [
        "reel_03_span_01", "reel_03_span_02", "reel_03_span_03"]
    assert [c.duration_frames for c in planned] == [
        int(round(e * FPS)) - int(round(s * FPS))
        for s, e in ((10.0, 12.0), (12.0, 14.0), (14.0, 16.0))]


def test_each_window_quotes_its_own_words():
    """`range_line` is the window's line, not the range's."""
    planned = _plan_one()
    assert planned[0].props["runs"][0]["text"] == "So ranking number one on"
    assert planned[1].props["runs"][0]["text"] == "Google but invisible to AI"
    assert planned[2].props["runs"][0]["text"] == "systems now here"


def test_a_windowless_segment_past_the_last_range_refuses():
    """The old count check's first half: more segments than ranges."""
    declarations = ffe.declared_elements(declare())
    with pytest.raises(ffe.FullFrameDeclarationError, match="by position"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS,
                            ranges=[RANGES[0]], transcript=_transcript())


def test_a_range_no_segment_covers_refuses():
    """The old count check's second half: fewer segments than ranges."""
    declarations = ffe.declared_elements(declare())
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="no segment covers"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS,
                            ranges=RANGES + [(30.0, 32.0)],
                            transcript=_transcript())


def test_a_window_outside_every_range_refuses():
    segments = [
        {"window": [10.0, 12.0], "runs": _windowed_runs()},
        {"window": [30.0, 32.0], "runs": _windowed_runs()},
    ]
    declarations = ffe.declared_elements(_subdivided(segments=segments))
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="exactly one keep range"):
        _plan_one(declarations)


def test_a_window_spanning_the_cut_between_ranges_refuses():
    """A window across two ranges sits inside neither one."""
    entry = copy.deepcopy(VALID)
    entry["segments"] = [
        {"window": [10.0, 16.0],
         "runs": [dict(VALID["segments"][0]["runs"][0])]},
        {"window": [15.0, 21.0],
         "runs": [dict(VALID["segments"][1]["runs"][0])]},
    ]
    declarations = ffe.declared_elements({"full_frame_elements": [entry]})
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="exactly one keep range"):
        _plan(declarations)


def test_a_window_inside_two_overlapping_ranges_refuses():
    """Exactly one means one: a window inside two ranges names neither."""
    declarations = ffe.declared_elements(_subdivided())
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="exactly one keep range"):
        _plan_one(declarations,
                  ranges=[(10.0, 16.0), (12.0, 20.0)],
                  transcript=_one_range_transcript())


def test_overlapping_windows_refuse():
    segments = [
        {"window": [10.0, 13.0], "runs": _windowed_runs("alpha")},
        {"window": [12.0, 14.0], "runs": _windowed_runs("beta")},
        {"window": [14.0, 16.0], "runs": _windowed_runs("gamma")},
    ]
    declarations = ffe.declared_elements(_subdivided(segments=segments))
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="may not claim one second"):
        _plan_one(declarations)


def test_an_undeclared_gap_refuses():
    segments = [
        {"window": [10.0, 12.0], "runs": _windowed_runs("alpha")},
        {"window": [14.0, 16.0], "runs": _windowed_runs("beta")},
    ]
    declarations = ffe.declared_elements(_subdivided(segments=segments))
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="no segment covers"):
        _plan_one(declarations)


def test_a_declared_gap_lays_as_a_hole_in_reel_positions():
    """`allow_gaps` is the explicit declaration a gap needs. The plan
    lays what was declared - the hole stays a hole in reel positions,
    so the placer and the verifier grade what the span chose rather
    than a stretch the engine invented."""
    segments = [
        {"window": [10.0, 12.0], "runs": _windowed_runs("alpha")},
        {"window": [14.0, 16.0], "runs": _windowed_runs("beta")},
    ]
    declarations = ffe.declared_elements(
        _subdivided(extra={"allow_gaps": True}, segments=segments))
    planned = _plan_one(declarations)
    assert len(planned) == 2
    assert planned[1].reel_start_frame == (
        int(round(14.0 * FPS)) - int(round(10.0 * FPS)))
    assert planned[1].reel_start_frame > planned[0].reel_end_frame


def test_segments_out_of_play_order_refuse():
    entry = copy.deepcopy(VALID)
    entry["segments"] = [
        {"window": [20.0, 24.0],
         "runs": [dict(VALID["segments"][0]["runs"][0])]},
        {"window": [10.0, 16.0],
         "runs": [dict(VALID["segments"][1]["runs"][0])]},
    ]
    declarations = ffe.declared_elements({"full_frame_elements": [entry]})
    with pytest.raises(ffe.FullFrameDeclarationError, match="in play order"):
        _plan(declarations)


def test_a_windowless_segment_keeps_its_positional_range():
    """No window means the old reading: segment i covers range i. A
    span may mix that with windows subdividing a later range."""
    entry = copy.deepcopy(VALID)
    entry["segments"] = [
        {"runs": [{"bind": "range_line", "type_role": "display",
                   "colour": "#FFFFFF"}]},
        {"window": [20.0, 22.0],
         "runs": _windowed_runs("second beat")},
        {"window": [22.0, 24.0],
         "runs": _windowed_runs("third beat")},
    ]
    declarations = ffe.declared_elements({"full_frame_elements": [entry]})
    planned = _plan(declarations)
    body = sum(int(round(e * FPS)) - int(round(s * FPS)) for s, e in RANGES)
    assert len(planned) == 3
    assert sum(c.duration_frames for c in planned) == body
    assert planned[0].props["runs"][0]["text"] == (
        "So ranking number one on Google but invisible to AI")
    assert planned[1].props["runs"][0]["text"] == "second beat"


@pytest.mark.parametrize("window,expect", [
    ("nope", "a window is"),
    ([10.0], "a window is"),
    ([10.0, 12.0, 14.0], "a window is"),
    (["a", "b"], "a window is"),
    ([True, 12.0], "a window is"),
    ([12.0, 10.0], "starts before it ends"),
    ([12.0, 12.0], "starts before it ends"),
])
def test_a_malformed_window_is_refused_by_name(window, expect):
    segments = [
        {"window": window, "runs": _windowed_runs("alpha")},
        {"window": [14.0, 16.0], "runs": _windowed_runs("beta")},
    ]
    with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
        ffe.declared_elements(_subdivided(segments=segments))


def test_allow_gaps_must_be_a_bool():
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="either on or off"):
        ffe.declared_elements(_subdivided(extra={"allow_gaps": "yes"}))


# ── A segment may declare its own ground ───────────────────────────
#
# Blocker 2 of docs/SPAN_RENDERER_CAPABILITY.md: one `background` for
# the whole span, so the reference's mid-piece value inversion had no
# declaration. A segment may now declare its own `background`; absent
# means the span's, which stays required and stays the default.

def _grounded_segments():
    return [
        {"window": [10.0, 12.0], "runs": _windowed_runs("alpha")},
        {"window": [12.0, 14.0], "background": "#000000",
         "runs": _windowed_runs("beta")},
        {"window": [14.0, 16.0], "runs": _windowed_runs("gamma")},
    ]


def test_a_segment_may_declare_its_own_background():
    """The other half of the refusal below."""
    normalised = ffe.declared_elements(
        _subdivided(segments=_grounded_segments()))
    assert normalised[0]["segments"][1]["background"] == "#000000"
    assert normalised[0]["segments"][0]["background"] is None
    planned = _plan_one(ffe.declared_elements(
        _subdivided(segments=_grounded_segments())))
    assert [c.props["background"] for c in planned] == [
        "#101014", "#000000", "#101014"]


@pytest.mark.parametrize("background", ["", "   "])
def test_an_empty_segment_background_is_refused(background):
    segments = [
        {"window": [10.0, 12.0], "background": background,
         "runs": _windowed_runs("alpha")},
        {"window": [12.0, 16.0], "runs": _windowed_runs("beta")},
    ]
    with pytest.raises(ffe.FullFrameDeclarationError, match="the span's"):
        ffe.declared_elements(_subdivided(segments=segments))


def test_word_sync_clocks_each_window_not_its_range():
    """The word clock paces the segment's own beat: the second window's
    first cue reads frame 0, where a range clock would read 2.0s."""
    entry = {
        "element": "full_frame_span",
        "placement": "span",
        "background": "#101014",
        "font_family": "Montserrat",
        "entrance": "typewriter",
        "word_sync": True,
        "segments": [
            {"window": [10.0, 12.0], "runs": _windowed_runs()},
            {"window": [12.0, 14.0], "runs": _windowed_runs()},
            {"window": [14.0, 16.0], "runs": _windowed_runs()},
        ],
    }
    planned = _plan_one(
        ffe.declared_elements({"full_frame_elements": [entry]}))
    first, second, third = (c.props["wordCues"] for c in planned)
    assert [len(first), len(second), len(third)] == [5, 5, 3]
    assert first[0]["start"] == 0.0
    assert second[0]["start"] == 0.0
    assert third[0]["start"] == 0.0


def test_word_sync_beside_full_range_copy_refuses_on_a_subdivided_segment():
    """A subdivided clock paces the window's words, so quoting the
    whole range beside it lands near words instead of on them."""
    entry = {
        "element": "full_frame_span",
        "placement": "span",
        "background": "#101014",
        "font_family": "Montserrat",
        "entrance": "typewriter",
        "word_sync": True,
        "segments": [
            {"window": [10.0, 12.0], "runs": _windowed_runs(
                "So ranking number one on Google but invisible to AI "
                "systems now here")},
            {"window": [12.0, 16.0], "runs": _windowed_runs("beta")},
        ],
    }
    declarations = ffe.declared_elements({"full_frame_elements": [entry]})
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="do not read exactly"):
        _plan_one(declarations)


# ── The verifier reads spans ─────────────────────────────────────


SPAN_FRAMES = [int(round(e * FPS)) - int(round(s * FPS)) for s, e in RANGES]


def _span_cards():
    return [PlannedCard(render_name=f"reel_03_span_0{i}", placement="span",
                        reel_start_frame=sum(SPAN_FRAMES[:i - 1]),
                        duration_frames=SPAN_FRAMES[i - 1],
                        element="full_frame_span")
            for i in (1, 2)]


def _span_items():
    return [TimelineItem(
        track_type="video", track_index=1,
        start_frame=sum(SPAN_FRAMES[:i]), end_frame=sum(SPAN_FRAMES[:i + 1]),
        duration_frames=SPAN_FRAMES[i],
        source_start_frame=0, source_end_frame=SPAN_FRAMES[i],
        source_file=(f"/p/scratch/reel_cards/reel_03_span_0{i + 1}.mov"),
        speaker=None, name=f"reel_03_span_0{i + 1}.mov")
        for i in (0, 1)]


def test_f13_passes_a_span_that_is_on_the_timeline_whole():
    findings = check_full_frame_cards(
        "Reel 03", _span_cards(), _span_items(), 1080, 1920, FPS)
    assert findings == []


def test_f13_fails_a_span_segment_that_is_missing_or_misplaced():
    missing = check_full_frame_cards(
        "Reel 03", _span_cards(), _span_items()[:1], 1080, 1920, FPS)
    assert [f.finding_class for f in missing] == [FindingClass.F13]
    assert "span segment" in missing[0].message

    moved = list(_span_items())
    moved[1] = TimelineItem(
        track_type="video", track_index=1,
        start_frame=moved[1].start_frame + 1,
        end_frame=moved[1].end_frame + 1,
        duration_frames=moved[1].duration_frames,
        source_start_frame=0, source_end_frame=moved[1].duration_frames,
        source_file=moved[1].source_file, speaker=None, name=moved[1].name)
    wrong = check_full_frame_cards(
        "Reel 03", _span_cards(), moved, 1080, 1920, FPS)
    assert [f.finding_class for f in wrong] == [FindingClass.F13]


def test_f13_fails_a_span_segment_nobody_declared():
    findings = check_full_frame_cards(
        "Reel 03", [], _span_items(), 1080, 1920, FPS)
    assert {f.finding_class for f in findings} == {FindingClass.F13}


def test_f4_counts_no_footage_where_a_span_replaced_it():
    """The build suppresses the footage video under a span, so the plan
    the verifier re-derives is empty and the span items are excluded as
    elements - zero expected, zero found, and still sensitive: drop one
    span item and F13 (not F4) names it."""
    findings = check_item_count(
        "Reel 03", [], _span_items(), FPS, cards=_span_cards())
    assert findings == []


def test_span_segments_leave_no_picture_hole():
    assert check_picture_holes("Reel 03", _span_items()) == []


def test_f12_grades_a_span_segment_against_the_whole_frame():
    from library.tools.reel_conformance_verifier import check_delivered_framing
    identity = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
                "CropLeft": 0.0, "CropRight": 0.0,
                "CropTop": 0.0, "CropBottom": 0.0}
    items = [TimelineItem(
        track_type="video", track_index=1,
        start_frame=it.start_frame, end_frame=it.end_frame,
        duration_frames=it.duration_frames,
        source_start_frame=0, source_end_frame=it.duration_frames,
        source_file=it.source_file, speaker=None, name=it.name,
        transform=dict(identity)) for it in _span_items()]
    findings = check_delivered_framing(
        "Reel 03", items, 1080, 1920,
        source_sizes={}, declared_intent=0.0, cards=_span_cards())
    assert findings == [], [str(f) for f in findings]


def test_the_render_is_opaque_and_names_spans(monkeypatch, tmp_path):
    """Span segments render through the card composition, opaque."""
    seen = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        seen.setdefault("commands", []).append(command)
        out = command[4]
        with open(out, "wb") as handle:
            handle.write(b"not empty")
        return _Result()

    monkeypatch.setattr(ffe.subprocess, "run", fake_run)
    planned = _plan()
    rendered = ffe.render_reel_cards(planned, str(tmp_path), str(tmp_path))
    for command in seen["commands"]:
        assert "--transparent" not in command
        assert ffe.FULL_FRAME_COMPOSITION in command
    assert [c.rendered_path for c in rendered] != ["", ""]
    props = json.loads(
        (tmp_path / "reel_03_span_01_props.json").read_text())
    assert props["background"] == "#101014"
    assert props["runs"][0]["text"] == "First kept sentence"


# ── The render proves the drawing ────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

renders_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None
    or shutil.which("ffmpeg") is None,
    reason="needs remotion-subtitles/node_modules, npx and ffmpeg",
)

# Small frame, short hold: this proves per-segment drawing, not the
# delivery format.  The colours are the assertion mechanism - a build
# with the segment copy crossed or unified would swap or merge them.
RENDER_WIDTH, RENDER_HEIGHT, RENDER_FPS = 320, 568, 30
SEGMENT_SECONDS = 0.5
GROUND = "#101014"
GROUND_RGB = (0x10, 0x10, 0x14)


def _render_transcript():
    return {"segments": [
        {"speaker": "Akshita", "timeline_start": 10.0, "timeline_end": 10.5,
         "text": "alpha",
         "words": [{"word": "alpha", "start": 10.1, "end": 10.4,
                    "timed": True}]},
        {"speaker": "Craig", "timeline_start": 20.0, "timeline_end": 20.5,
         "text": "beta",
         "words": [{"word": "beta", "start": 20.1, "end": 20.4,
                    "timed": True}]},
    ]}


def _render_ranges():
    return [(10.0, 10.5), (20.0, 20.5)]


@renders_available
def test_each_segment_draws_its_own_copy(tmp_path):
    """One declaration, two renders, each drawing its segment's copy.

    Segment 1 draws white copy, segment 2 red.  The mid-hold frame of
    each must carry its own colour's ink and none of the other's: a
    render that unified the span into one static card - or crossed the
    two - fails here, while a source parse of the roster would pass.
    """
    declarations = ffe.declared_elements({"full_frame_elements": [{
        "element": "full_frame_span",
        "placement": "span",
        "background": GROUND,
        "font_family": "Montserrat",
        "segments": [
            {"runs": [{"text": "ALPHA", "type_role": "display",
                       "colour": "#FFFFFF"}]},
            {"runs": [{"bind": "range_line", "type_role": "display",
                       "colour": "#FF0000"}]},
        ]} ]})
    ranges = _render_ranges()
    transcript = _render_transcript()
    facts = ffe.ReelFacts.from_moment(
        _Moment(), ranges, transcript, opening_seconds=3.0)
    body = sum(int(round(e * RENDER_FPS)) - int(round(s * RENDER_FPS))
               for s, e in ranges)
    planned = ffe.plan_reel_cards(
        declarations, facts, body, RENDER_FPS,
        width=RENDER_WIDTH, height=RENDER_HEIGHT,
        ranges=list(ranges), transcript=transcript)
    assert len(planned) == 2
    assert planned[1].props["runs"][0]["text"] == "beta"

    rendered = ffe.render_reel_cards(
        planned, REMOTION_DIR, str(tmp_path / "span"))
    assert all(os.path.getsize(c.rendered_path) > 0 for c in rendered)

    frames = []
    for card in rendered:
        out_dir = str(tmp_path / f"frames_{card.render_name}")
        os.makedirs(out_dir, exist_ok=True)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", card.rendered_path, "-pix_fmt", "rgba",
             os.path.join(out_dir, "frame_%03d.png")],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False)
        assert result.returncode == 0, result.stderr[-800:]
        own = sorted(os.path.join(out_dir, n) for n in os.listdir(out_dir)
                     if n.endswith(".png"))
        assert len(own) == card.duration_frames, (
            f"{card.render_name}: {len(own)} frames for "
            f"{card.duration_frames} planned")
        frames.append(own)

    from PIL import Image

    def ink(png_path, colour, tolerance=30, min_alpha=200):
        image = Image.open(png_path).convert("RGBA")
        pixels = image.load()
        width, height = image.size
        found = []
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                if a < min_alpha:
                    continue
                if all(abs(c - t) <= tolerance
                       for c, t in zip((r, g, b), colour)):
                    found.append((x, y))
        return found

    mid_first = frames[0][len(frames[0]) // 2]
    mid_second = frames[1][len(frames[1]) // 2]
    white_in_first = ink(mid_first, (255, 255, 255))
    red_in_first = ink(mid_first, (255, 0, 0))
    white_in_second = ink(mid_second, (255, 255, 255))
    red_in_second = ink(mid_second, (255, 0, 0))

    assert len(white_in_first) >= 500, (
        f"segment 1 draws no white 'ALPHA' in {mid_first}")
    assert not red_in_first, (
        f"segment 1 carries segment 2's red in {mid_first}")
    assert len(red_in_second) >= 500, (
        f"segment 2 draws no red range_line 'beta' in {mid_second}")
    assert not white_in_second, (
        f"segment 2 carries segment 1's white in {mid_second}")

    # Opaque on its declared ground: a corner pixel is the ground
    # itself, at full alpha - no transparency showing through nothing.
    corner = Image.open(mid_first).convert("RGBA").load()[4, 4]
    assert corner[3] == 255, f"corner alpha {corner[3]}; the render leaks"
    assert all(abs(c - t) <= 30 for c, t in zip(corner[:3], GROUND_RGB)), (
        f"corner {corner[:3]} is not the declared ground {GROUND_RGB}")


stills_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None,
    reason="needs remotion-subtitles/node_modules and npx",
)


def _span_mark_stand_in(path: str, width: int = 400,
                        height: int = 160) -> None:
    """A stand-in for a project's own wordmark, drawn, not shipped.

    Ice blue on transparent - a colour neither the span's ground nor
    its runs carry (one segment's copy is Sun Orange, which is why
    this fixture is not), so its presence proves the SEGMENT's image
    drew. The same fixture shape
    tests/test_fullframe_card_image.py draws.
    """
    from PIL import Image, ImageDraw
    mark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mark)
    draw.rectangle([0, 0, width - 1, height - 1],
                   fill=(80, 160, 255, 255))
    draw.rectangle([10, 10, width - 11, height - 11],
                   outline=(20, 60, 140, 255), width=6)
    mark.save(path, "PNG")


def _is_span_mark(pixel) -> bool:
    r, g, b = pixel[0], pixel[1], pixel[2]
    return r < 120 and 100 < g < 220 and b > 200


@stills_available
def test_a_segment_image_reaches_pixels(tmp_path):
    """A span segment's own mark reaches the frame; without it, none.

    Two stills of the SAME planned segment's props, the only difference
    the `image` key the span plan resolved. The props come out of
    `_plan_span`, not out of a hand-built dict - so this pins the span's
    own image path through the shared drawing node, and fails on a plan
    that drops the segment's mark or a composition that ignores it.
    """
    pytest.importorskip("PIL", reason="needs Pillow to draw the fixture")
    from PIL import Image

    brand_dir = os.path.join(REMOTION_DIR, "public", "brand")
    os.makedirs(brand_dir, exist_ok=True)
    staged = os.path.join(brand_dir, "fullframe_span_mark_fixture.png")
    fixture = tmp_path / "span_mark.png"
    _span_mark_stand_in(str(fixture))
    shutil.copy2(str(fixture), staged)
    try:
        declarations = ffe.declared_elements(_imaged_span(
            image="fullframe_span_mark_fixture.png", image_width=200))
        planned = ffe.plan_reel_cards(
            declarations, _facts(),
            sum(SPAN_FRAMES), FPS, width=540, height=960,
            ranges=list(RANGES), transcript=_transcript(),
            resolve_asset=lambda name: f"brand/{name}")
        assert planned[0].props["image"] == (
            "brand/fullframe_span_mark_fixture.png")

        def still(props: dict, name: str):
            props_path = tmp_path / f"{name}.json"
            props_path.write_text(json.dumps(props), encoding="utf-8")
            out_path = tmp_path / f"{name}.png"
            result = subprocess.run(
                ["npx", "remotion", "still", "FullFrameCard",
                 str(out_path), "--frame=7", f"--props={props_path}",
                 "--image-format=png"],
                cwd=REMOTION_DIR, capture_output=True, text=True,
                encoding="utf-8", check=False)
            assert result.returncode == 0, result.stderr[-2000:]
            return Image.open(out_path).convert("RGB")

        plain_props = dict(planned[1].props)
        assert "image" not in plain_props
        plain = still(plain_props, "span_segment_plain")
        branded = still(dict(planned[0].props), "span_segment_branded")

        assert plain.size == branded.size == (540, 960)
        plain_px, branded_px = plain.load(), branded.load()
        mark_in_plain = sum(
            1 for y in range(960) for x in range(540)
            if _is_span_mark(plain_px[x, y]))
        assert mark_in_plain == 0, (
            f"the segment with no image drew {mark_in_plain} mark "
            f"pixels - the control frame is contaminated")
        mark_in_branded = sum(
            1 for y in range(960) for x in range(540)
            if _is_span_mark(branded_px[x, y]))
        assert mark_in_branded > 4000, (
            f"the segment's image drew only {mark_in_branded} mark pixels")
    finally:
        if os.path.exists(staged):
            os.remove(staged)
