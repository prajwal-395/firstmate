"""A caption-only timing change, expressed and surviving a rebuild.

The captain moved Reel 13's five closing cards on 2026-09-11 and left
the picture frame-identical. `span_retime` holds picture spans, so
nothing could carry the change and a rebuild destroyed it twice. The
fixtures below are his real measured numbers - live against rebuild -
so a regression here is the same defect, not a synthetic one.

    live     1607-1685  1688-1721  1731-1807  1822-1891  1900-1903
    rebuild  1600-1678  1681-1714  1724-1800  1815-1884  1887-1903
"""

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
    # A timeline-scoped pin gone stale on its own reel still reports.
    pins = _scoped_pins()
    pins[0]["scope"] = {**pins[0]["scope"],
                        "source_start_at_or_after": 9999.0}
    _, _, _, stale = caption_timing.apply_pins(
        _segments_on(REEL_13), pins, FPS)
    assert len(stale) == 1
    assert "STALE" in stale[0]["reason"]


# ── Timeline scope: one reel's hand edit, not every reel's ──────────
#
# Measured 2026-09-12: the two pins above are Reel 13's closing cards,
# but the source words they address are the shared call to action that
# closes Reels 01, 13, 23, 28 and 31. Unscoped, the pins moved five of
# Reel 23's cards seven frames late and trimmed its last card from
# 2138-2155 to 2151-2155 - the captain's marked drift, at exactly the
# marked frame. A `timeline` scope keeps the pin on its own reel.

REEL_13 = "Reel 13 - the-accounting-firm-ai-called-healthcare"
REEL_23 = "Reel 23 - why-small-business-wins-on-ai"


def _scoped_pins():
    pins = _captains_pins()
    for pin in pins:
        pin["scope"] = {**pin["scope"], "timeline": REEL_13}
    return pins


def _segments_on(timeline):
    segments = _segments()
    for segment in segments:
        segment["binding"] = {**segment["binding"], "timeline": timeline}
    return segments


def test_a_timeline_scoped_pin_moves_only_its_own_reel():
    pins = _scoped_pins()
    moved13, applied13, _, stale13 = caption_timing.apply_pins(
        _segments_on(REEL_13), pins, FPS)
    assert not stale13
    assert _spans(moved13) == LIVE
    assert len(applied13) == 5
    # The same source words on Reel 23: untouched, and silent - out of
    # scope is not stale.
    moved23, applied23, _, stale23 = caption_timing.apply_pins(
        _segments_on(REEL_23), pins, FPS)
    assert not applied23 and not stale23
    assert _spans(moved23) == [(a, b) for _, _, _, a, b in CLOSERS]






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

def test_a_pin_that_would_draw_nothing_or_leave_the_reel_refuses():
    with pytest.raises(caption_timing.CaptionTimingError,
                       match="draws nothing"):
        caption_timing.apply_pins(
            _segments(), [{"scope": {"source_start": 512.759},
                           "head_frames": 99, "reason": "too far"}], FPS)
    with pytest.raises(caption_timing.CaptionTimingError,
                       match="before the reel begins"):
        caption_timing.apply_pins(
            _segments(), [{"scope": {"source_start": 500.780},
                           "offset_frames": -9999, "reason": "too far"}],
            FPS)


def test_malformed_pins_refuse():
    for bad, match in [
        ([{"offset_frames": 1, "reason": "r"}], "no scope"),
        ([{"scope": {"nonsense": 1}, "offset_frames": 1, "reason": "r"}],
         "nothing reads"),
        ([{"scope": {"speaker": "a"}, "offset_frames": 0.5, "reason": "r"}],
         "whole number of FRAMES"),
        ([{"scope": {"speaker": "a"}, "offset_frames": 1}], "no reason"),
        ([], "non-empty"),
    ]:
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


# ── Rebase: pins follow the words across an ingest retiming ─────────
#
# When ingest moves instruments, words sit where the new one heard
# them and a pin scoped to the old positions detaches - matching a
# different set of cards, or none, and the no-match case reports
# STALE for a trim that is still wanted. The migration the MFA
# adoption's landing condition names is a rebase by the measured
# per-file delta, never leaving the pins to report stale.

def test_a_rebase_moves_source_scopes_by_the_measured_delta():
    moved = caption_timing.rebase_pins(
        _captains_pins(), -0.047,
        reason="ingest retimed wav2vec2 to MFA")
    first, last = moved
    assert first["scope"]["source_start_at_or_after"] == pytest.approx(
        500.733, abs=1e-9)
    assert first["scope"]["source_start_before"] == pytest.approx(
        512.712, abs=1e-9)
    assert last["scope"]["source_start"] == pytest.approx(
        512.712, abs=1e-9)
    # Frame adjustments are relative moves and travel unchanged.
    assert first["offset_frames"] == 7
    assert last["head_frames"] == 13
    # The hand edit's own account stays attached to the numbers.
    assert "captain 2026-09-11" in last["reason"]
    assert "rebased -0.047s" in last["reason"]
    # A pin with no source scope passes through unchanged.
    pins = [{"scope": {"speaker": "akshita", "timeline": "Reel 13"},
             "offset_frames": 7,
             "reason": "a placement pin names no source seconds"}]
    assert caption_timing.rebase_pins(
        pins, -0.047, reason="ingest retimed") == pins


def test_a_rebase_follows_the_words_to_the_same_cards():
    """The property the migration exists for: pins rebased by the
    delta match cards shifted by the delta exactly where the
    unrebased pins matched the unshifted cards."""
    delta = -0.047
    pins = _captains_pins()
    moved, applied, _, stale = caption_timing.apply_pins(
        _segments(), pins, FPS)
    assert not stale

    shifted = []
    for segment in _segments():
        row = dict(segment)
        binding = dict(row["binding"])
        binding["source_start"] = round(
            binding["source_start"] + delta, 3)
        binding["source_end"] = round(binding["source_end"] + delta, 3)
        row["binding"] = binding
        shifted.append(row)
    moved_shifted, applied_shifted, _, stale_shifted = (
        caption_timing.apply_pins(
            shifted, caption_timing.rebase_pins(
                pins, delta, reason="ingest retimed wav2vec2 to MFA"),
            FPS))
    assert not stale_shifted
    assert [r["segment_id"] for r in applied_shifted] == [
        r["segment_id"] for r in applied]
    assert [s["timeline_start"] for s in moved_shifted] == [
        s["timeline_start"] for s in moved]


def test_a_rebase_off_the_source_head_or_without_a_reason_refuses():
    pins = [{"scope": {"source_start": 0.010}, "offset_frames": 7,
             "reason": "a pin on the first word"}]
    with pytest.raises(caption_timing.CaptionRebaseRefused,
                       match="before the source starts") as refused:
        caption_timing.rebase_pins(
            pins, -0.047, reason="ingest retimed")
    # The one refusal shape: what, why, fix - and still the
    # module's own error for every existing catcher.
    assert refused.value.what and refused.value.why and refused.value.fix
    assert isinstance(refused.value, caption_timing.CaptionTimingError)
    assert refused.value.render().startswith("ren: refused - ")
    with pytest.raises(caption_timing.CaptionTimingError,
                       match="no reason"):
        caption_timing.rebase_pins(_captains_pins(), -0.047, reason=" ")




