"""A caption-only timing change, expressed and surviving a rebuild.

The captain moved Reel 13's five closing cards on 2026-09-11 and left
the picture frame-identical. `span_retime` holds picture spans, so
nothing could carry the change and a rebuild destroyed it twice. The
fixtures below are his real measured numbers - live against rebuild -
so a regression here is the same defect, not a synthetic one.

    live     1607-1685  1688-1721  1731-1807  1822-1891  1900-1903
    rebuild  1600-1678  1681-1714  1724-1800  1815-1884  1887-1903
"""

import json
import os

import pytest

from library.tools import caption_timing

FPS = 24000 / 1001

#: Reel 13's five closing cards as a REBUILD renders them: the source
#: spans are off the segment names Resolve reports, in seconds.
CLOSERS = [
    ("sub_akshita_5ae8f521_500780-504042", 500.780, 504.042, 1600, 1678),
    ("sub_akshita_5ae8f521_504162-505548", 504.162, 505.548, 1681, 1714),
    ("sub_akshita_5ae8f521_505970-509122", 505.970, 509.122, 1724, 1800),
    ("sub_akshita_5ae8f521_509764-512638", 509.764, 512.638, 1815, 1884),
    ("sub_akshita_5ae8f521_512759-513442", 512.759, 513.442, 1887, 1903),
]

#: And where the captain's own hand put them.
LIVE = [(1607, 1685), (1688, 1721), (1731, 1807), (1822, 1891),
        (1900, 1903)]


def _segments():
    return [{"segment_id": sid,
             "binding": {"speaker": "akshita",
                         "source_clip_id": "5ae8f521-647e-49c6-bf3d",
                         "source_start": start, "source_end": end},
             "timeline_start": a / FPS, "timeline_end": b / FPS}
            for sid, start, end, a, b in CLOSERS]


def _spans(segments):
    from library.tools.frame_utils import span_frames

    return [span_frames(s["timeline_start"], s["timeline_end"], FPS)
            for s in segments]


def _captains_pins():
    """His edit, declared: four cards moved seven frames later, and the
    fifth's head pulled in with its tail held."""
    return [
        {"scope": {"speaker": "akshita",
                   "source_start_at_or_after": 500.780,
                   "source_start_before": 512.759},
         "offset_frames": 7,
         "reason": "captain 2026-09-11: the closer cards run 7f early"},
        {"scope": {"speaker": "akshita", "source_start": 512.759},
         "head_frames": 13,
         "reason": "captain 2026-09-11: the last card comes in late"},
    ]


# ── The captain's own edit, reproduced ─────────────────────────────

def test_the_pins_reproduce_the_captains_timeline_exactly():
    moved, applied, short, stale = caption_timing.apply_pins(
        _segments(), _captains_pins(), FPS)
    assert not stale
    assert _spans(moved) == LIVE
    assert len(applied) == 5
    # His last card is three frames - under the craft floor, reported
    # and placed rather than refused. He is allowed a flash.
    assert len(short) == 1
    assert short[0]["segment_id"].endswith("512759-513442")
    assert short[0]["seconds"] == pytest.approx(3 / FPS, abs=1e-3)


def test_applying_twice_lands_the_same_place():
    """A rebuild re-renders the cards at the plan's own seconds and the
    pins move them again - that is what surviving means here."""
    first, _, _, _ = caption_timing.apply_pins(
        _segments(), _captains_pins(), FPS)
    second, _, _, _ = caption_timing.apply_pins(
        _segments(), _captains_pins(), FPS)
    assert _spans(first) == _spans(second) == LIVE


def test_the_input_segments_are_not_mutated():
    segments = _segments()
    before = _spans(segments)
    caption_timing.apply_pins(segments, _captains_pins(), FPS)
    assert _spans(segments) == before


def test_no_pins_passes_the_segments_through():
    segments = _segments()
    out, applied, short, stale = caption_timing.apply_pins(segments, [], FPS)
    assert out is segments
    assert not (applied or short or stale)


# ── Scope ──────────────────────────────────────────────────────────

def test_scope_addresses_the_source_audio_not_the_timeline():
    segments = _segments()
    window = {"speaker": "akshita", "source_start_at_or_after": 505.970,
              "source_start_before": 509.764}
    assert [s["segment_id"] for s in segments
            if caption_timing.matches(s, window)] == [
        "sub_akshita_5ae8f521_505970-509122"]
    # A clip id read off a rendered FILENAME is slugged and truncated;
    # it must still address the binding it was copied from.
    assert caption_timing.matches(
        segments[0], {"source_clip_id": "5ae8f521-647e-49c6-bf3d"})
    assert not caption_timing.matches(segments[0],
                                      {"speaker": "craig"})
    assert not caption_timing.matches(segments[0],
                                      {"source_start": 999.0})


def test_a_pin_matching_nothing_reports_stale_rather_than_vanishing():
    out, applied, _, stale = caption_timing.apply_pins(
        _segments(), [{"scope": {"speaker": "craig"}, "offset_frames": 3,
                       "reason": "words that moved"}], FPS)
    assert not applied and len(stale) == 1
    assert "STALE" in stale[0]["reason"] and "craig" in str(stale[0]["scope"])
    assert _spans(out) == [(a, b) for _, _, _, a, b in CLOSERS]


def test_the_list_subclass_and_its_plan_entries_survive():
    """`reel_subtitle_segments` returns a list SUBCLASS carrying the
    plan entries the caption hash digests; rebuilding a plain list
    would drop them three functions from the cause."""
    from library.tools.reel_build import _SegmentsWithEntries

    segments = _SegmentsWithEntries()
    segments.extend(_segments())
    segments.caption_entries = [{"text": "the link's in our bio"}]
    segments.spine = {"structure": []}
    out, _, _, _ = caption_timing.apply_pins(
        segments, _captains_pins(), FPS)
    assert isinstance(out, _SegmentsWithEntries)
    assert out.caption_entries == segments.caption_entries
    assert out.spine == segments.spine


# ── Refusals ───────────────────────────────────────────────────────

def test_a_card_trimmed_out_of_existence_refuses():
    with pytest.raises(caption_timing.CaptionTimingError,
                       match="draws nothing"):
        caption_timing.apply_pins(
            _segments(), [{"scope": {"source_start": 512.759},
                           "head_frames": 99, "reason": "too far"}], FPS)


def test_a_card_moved_off_the_head_of_the_reel_refuses():
    with pytest.raises(caption_timing.CaptionTimingError,
                       match="before the reel begins"):
        caption_timing.apply_pins(
            _segments(), [{"scope": {"source_start": 500.780},
                           "offset_frames": -9999, "reason": "too far"}],
            FPS)


@pytest.mark.parametrize("bad,match", [
    ([{"offset_frames": 1, "reason": "r"}], "no scope"),
    ([{"scope": {}, "offset_frames": 1, "reason": "r"}], "no scope"),
    ([{"scope": {"nonsense": 1}, "offset_frames": 1, "reason": "r"}],
     "nothing reads"),
    ([{"scope": {"speaker": "a"}, "reason": "r"}], "adjusts nothing"),
    ([{"scope": {"speaker": "a"}, "offset_frames": 0.5, "reason": "r"}],
     "whole number of FRAMES"),
    ([{"scope": {"speaker": "a"}, "head_frames": -3, "reason": "r"}],
     "a trim removes frames"),
    ([{"scope": {"speaker": "a"}, "offset_frames": 1}], "no reason"),
    ([], "non-empty"),
])
def test_malformed_pins_refuse(bad, match):
    with pytest.raises(caption_timing.CaptionTimingError, match=match):
        caption_timing.validate_pins(bad)


def test_an_unreadable_declaration_refuses(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "external"))
    with open(os.path.join(root, "external", "caption_timing.json"), "w",
              encoding="utf-8") as handle:
        handle.write("{not json")
    with pytest.raises(caption_timing.CaptionTimingError,
                       match="cannot be read"):
        caption_timing.load_pins(root)
    # A project that declares none gets none.
    assert caption_timing.load_pins(str(tmp_path / "other")) == []


def test_declaration_round_trips(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "external"))
    with open(os.path.join(root, "external", "caption_timing.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"version": 1, "pins": _captains_pins()}, handle)
    assert caption_timing.load_pins(root) == _captains_pins()


def test_span_retime_cannot_express_this():
    """The reason this owner sits beside `span_retime` rather than
    inside it: that vocabulary is a keep-range EDGE, it refuses an
    extension, and four of the five edits above are equal-duration
    shifts."""
    from library.tools import captain_edits

    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits(
            [{"kind": "span_retime", "anchor_phrase": "our bio",
              "edge": "offset", "reason": "move the card 7f later"}])
    # Its own vocabulary is head/tail - picture edges - and it carries
    # no word for WHICH ROW a change applies to.
    assert captain_edits.validate_edits(
        [{"kind": "span_retime", "anchor_phrase": "our bio",
          "edge": "head", "reason": "trim the picture"}])
