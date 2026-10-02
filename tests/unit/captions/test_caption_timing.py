"""A caption-only timing change, expressed and surviving a rebuild.

The captain moved Reel 13's five closing cards on 2026-09-11 and left
the picture frame-identical. `span_retime` holds picture spans, so
nothing could carry the change and a rebuild destroyed it twice. The
fixtures below are his real measured numbers - live against rebuild -
so a regression here is the same defect, not a synthetic one.

    live     1607-1685  1688-1721  1731-1807  1822-1891  1900-1903
    rebuild  1600-1678  1681-1714  1724-1800  1815-1884  1887-1903
"""
from __future__ import annotations
import os
import pytest
from library.tools import caption_timing
import sys
from pathlib import Path
from library.tools import operations
from library.tools import reel_spine
from library.tools.reel_build import reel_subtitle_segments
from library.tools.reel_conformance_verifier import (
    FindingClass,
    PlannedCaption,
    TimelineItem,
    check_caption_duration,
)
from library.steps.step_4_05_render_subtitles import generate_remotion_props
from library.tools import reel_build, subtitle_coverage, transcript_corrections
from library.tools import reel_conformance_verifier as verifier
from library.tools.reel_proposal import ReelMoment


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


# --------------------------------------------------------------------------
# From test_caption_talkover_overlap.py
#
# Reel 15 (2026-09-08): a talk-over captioned twice on one track.
#
# The rebuild refused reel 15 with two findings that are one defect seen
# from both sides: caption segment 5 (block 4, 'google rewards') was
# PLANNED at 14.93s and NEVER PLACED (F14), and its neighbours overlap by
# 43 frames / 1.79s (F6).
#
# The reel spine keeps a real talk-over on both mics by design -
# `reel_spine._drop_bleed` drops only same-word bleed, and different words
# at the same instant are two people talking over each other.  Step 4.01
# planned each block independently and only WARNED about the resulting
# cross-block overlap, so the plan asked two cards to cover the same
# seconds on one V3 track.  Resolve trims the later one's head, shifting
# it 43 frames off its planned start - outside the 2-frame mechanical
# pairing tolerance, which is why the segment reads as "never placed".
#
# Verdict: the PLANNER was wrong, the placer faithful, the gate correct.
# `generate_subtitles` now resolves cross-card overlaps (the earlier card
# yields to the next card's first word, or joins it where the trim would
# flash). A measured word fully inside the later card moves with its timing
# and text, including before a sentence-final backward merge. F6/F14 are
# unchanged - they must keep refusing a plan that still carries this shape.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.steps.step_4_01_plan_subtitles.step import (
    generate_subtitles,
    resolve_caption_overlaps,
)
from library.tools.reel_conformance_verifier import (
    check_caption_overlaps,
)


# ── Fixture: two talk-over blocks, reel 15's shape ──

def _words(text, start, dur=0.35, gap=0.05):
    out, t = [], start
    for word in text.split():
        out.append({"word": word, "source_start": round(t, 3),
                    "source_end": round(t + dur, 3)})
        t += dur + gap
    return out, t


def _block(position, text, tl_start, speaker):
    words, end = _words(text, tl_start)
    tl_end = round(end - 0.05, 3)
    return {
        "block_type": "speech",
        "position": position,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "source_start": tl_start,
        "source_end": tl_end,
        "clip_id": f"clip_{position:03d}",
        "alignment_method": "timeline_transcript",
        "word_timestamps": words,
        "speaker": speaker,
        "content": {"text": text},
    }


def _talkover_spine():
    """Block 4 starts 1.8s before block 3 ends: a genuine talk-over with
    different words on each mic, the shape `reel_spine` keeps by design."""
    return {"structure": [
        _block(3, "for small businesses the chains just cannot keep up",
               10.0, "akshita"),
        _block(4, "google rewards the shop that answers every question",
               12.6, "craig"),
    ]}


def _entries(spine):
    entries = generate_subtitles(
        spine, caption_case="lowercase")["subtitle_plan"]["subtitle_entries"]
    return sorted(entries, key=lambda e: e["timeline_start"])


# ── The regression: the planner must not emit overlapping cards ──

def test_talkover_blocks_produce_no_overlapping_cards():
    """Fails before the fix (block 3's last card overlaps block 4's first
    by ~1.8s and the step only warns); passes after (the earlier card
    ends at the next card's first word)."""
    entries = _entries(_talkover_spine())
    assert len(entries) > 1, "the fixture must produce more than one card"
    overlaps = [
        (a["id"], a["timeline_end"], b["id"], b["timeline_start"])
        for a, b in zip(entries, entries[1:])
        if a["timeline_end"] > b["timeline_start"] + 0.01
    ]
    assert not overlaps, f"overlapping caption cards: {overlaps}"


def test_resolving_the_overlap_keeps_every_word_on_screen():
    """Trimming or merging must never drop speech - a word with no card
    is an F5 coverage gap, trading one gate for another."""
    spine = _talkover_spine()
    spoken = sorted(
        w["word"].lower()
        for block in spine["structure"]
        for w in block["word_timestamps"])
    captioned = sorted(
        w["word"].lower()
        for entry in _entries(spine)
        for w in entry.get("words", []))
    assert captioned == spoken, (
        f"words lost resolving the overlap: "
        f"{sorted(set(spoken) - set(captioned))}")


def test_a_trim_that_would_flash_merges_into_the_next_card():
    """An earlier card trimmed under the 0.5s flash floor (the number P6
    fails a build on) joins the next card instead of flashing - with the
    next card's timing untouched, so no new overlap opens behind it."""
    entries = [
        {"id": "sub_3_001", "timeline_start": 14.0, "timeline_end": 14.4,
         "text": "short tail",
         "words": [{"word": "short", "start": 14.0, "end": 14.2},
                   {"word": "tail", "start": 14.2, "end": 14.4}],
         "word_count": 2, "spine_block_position": 3},
        {"id": "sub_4_001", "timeline_start": 14.2, "timeline_end": 16.0,
         "text": "google rewards",
         "words": [{"word": "google", "start": 14.2, "end": 14.6}],
         "word_count": 2, "spine_block_position": 4},
    ]
    fix = resolve_caption_overlaps(entries)
    assert fix == {"trimmed": 0, "merged": 1, "merged_backward": 0,
                   "reassigned": 0}, fix
    assert len(entries) == 1
    survivor = entries[0]
    assert survivor["id"] == "sub_4_001"
    assert (survivor["timeline_start"], survivor["timeline_end"]) == (14.2, 16.0)
    assert survivor["text"] == "short tail google rewards"
    assert [w["word"] for w in survivor["words"]] == [
        "short", "tail", "google"]


def _merge_entries(earlier_text, earlier_words, later_text, later_words,
                   earlier_start, earlier_end, later_start, later_end,
                   speaker):
    def _w(text, starts):
        return [{"word": w, "start": s, "end": s + 0.2}
                for w, s in zip(text.split(), starts)]
    return [
        {"id": "sub_1_001", "timeline_start": earlier_start,
         "timeline_end": earlier_end, "text": earlier_text,
         "words": _w(earlier_text, earlier_words),
         "word_count": len(earlier_text.split()),
         "spine_block_position": 1, "speaker": speaker[0]},
        {"id": "sub_2_001", "timeline_start": later_start,
         "timeline_end": later_end, "text": later_text,
         "words": _w(later_text, later_words),
         "word_count": len(later_text.split()),
         "spine_block_position": 2, "speaker": speaker[1]},
    ]


def test_same_speaker_merge_covers_its_first_word():
    """Reel 06, 2026-09-20: "Not at all." joined the next card, which
    kept the later start - "Not" played 2.79-2.93s with no caption
    over it (F25). One voice mistimed across two blocks is alignment
    slop, so the merged card opens where its first word does."""
    entries = _merge_entries(
        "not at all.", [2.82, 2.97, 3.04], "i have seen", [2.98, 3.28, 3.42],
        2.82, 3.24, 2.98, 4.41, ("akshita", "akshita"))
    fix = resolve_caption_overlaps(entries)
    assert fix == {"trimmed": 0, "merged": 1, "merged_backward": 0,
                   "reassigned": 0}, fix
    assert len(entries) == 1
    survivor = entries[0]
    assert survivor["timeline_start"] == 2.82
    first = survivor["words"][0]
    assert (first["word"], first["start"]) == ("not", 2.82)
    assert survivor["timeline_start"] <= first["start"]


def test_mixed_speaker_merge_keeps_the_later_start():
    """A genuine talk-over is a collision one track cannot serialize:
    the merged card keeps the later timing and the gates refuse what
    it cannot cover, exactly as before."""
    entries = _merge_entries(
        "short tail", [14.0, 14.2], "google rewards", [14.2, 14.6],
        14.0, 14.4, 14.2, 16.0, ("akshita", "craig"))
    fix = resolve_caption_overlaps(entries)
    assert fix == {"trimmed": 0, "merged": 1, "merged_backward": 0,
                   "reassigned": 0}, fix
    assert entries[0]["timeline_start"] == 14.2


# ── The gate: unchanged, and still refuses the genuinely bad case ──

def _planned(start, end, text, block):
    return PlannedCaption(
        start_seconds=start, end_seconds=end, text=text, speaker="craig",
        frames=max(int(round((end - start) * FPS)), 1),
        block_position=str(block), block_end_seconds=end)


def _placed(start_frame, duration_frames):
    return TimelineItem(
        track_type="video", track_index=3,
        start_frame=start_frame, end_frame=start_frame + duration_frames,
        duration_frames=duration_frames,
        source_start_frame=0, source_end_frame=duration_frames,
        source_file="/m/caption.mov", speaker="craig", name="caption")


def test_gate_still_refuses_overlapping_plan_cards():
    """F6 fires on two plan cards covering the same seconds, whatever
    produced them.  This pins the gate the fix deliberately left able
    to refuse."""
    cards = [
        {"reel_start": 13.0, "reel_end": 14.93 + 1.79,
         "text": "for small businesses", "speaker": "akshita"},
        {"reel_start": 14.93, "reel_end": 16.5,
         "text": "google rewards", "speaker": "craig"},
    ]
    findings = check_caption_overlaps("reel 15", cards, FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F6]
    assert findings[0].detail["overlap_frames"] == 43, findings[0].detail


# --------------------------------------------------------------------------
# From test_caption_segment_frame_convention.py
#
# The caption segment frame convention: media fps == timeline fps.
#
# vep-caption-segment-off-by-one-frame. The 2026-09-08 rebuild placed content
# CLEAN and still drew 15 x F2 with delta -1, every segment exactly one frame
# short in the same direction. That is a convention mismatch, not noise: the
# reel builder rendered caption media at ``int(round(fps))`` = 24fps for a
# 24000/1001 timeline, and Resolve's time-mapping of the 24fps source onto
# 23.976 drops the fractional frame on every segment this size (a 135-frame
# 24fps source reads back as 134 timeline frames). The verifier counts
# ``round(span * 24000/1001)`` - what the timeline should carry - and is right.
#
# The fix renders caption media at the exact timeline fps, which maps 1:1
# under any snap direction. These tests pin that contract from both ends:
# the builder counts what the verifier expects, and the verifier still
# catches a genuine one-frame shortfall.

# A span where counting at 24fps and at 24000/1001 disagree by a frame:
# round(2.5625 * 24) = 62, round(2.5625 * 24000/1001) = 61.
SPAN_START = 20.0
SPAN = 2.5625
SPAN_END = SPAN_START + SPAN
EXPECTED_FRAMES = max(int(round(SPAN * FPS)), 1)
assert EXPECTED_FRAMES == 61


def _entries_2():
    return [
        {
            "timeline_start": SPAN_START,
            "timeline_end": SPAN_END,
            "text": "search did not change",
            "spine_block_position": "body_7",
            "speaker": "Craig",
            "words": [],
        }
    ]


def _captured_props(monkeypatch, tmp_path):
    """Drive the real reel caption path with stubbed plan/render operations.

    The spine producer and both registry operations are replaced, but
    `generate_subtitle_props_per_block` - the frame counting under test -
    runs unmodified, called exactly as `reel_subtitle_segments` calls it.
    Returns the props dicts the (stubbed) renderer was handed.
    """
    captured = []

    def fake_spine(moment, transcript, keep_ranges, lead_seconds=0.0,
                   project_folder=""):
        return {}

    class FakePlan:
        def run(self, spine, **kwargs):
            return {"subtitle_plan": {
                "subtitle_entries": _entries_2(),
                "style": {"fontFamily": "Montserrat"},
            }}

    class FakeRender:
        def run(self, props, out_dir, name, progress="", reuse=False,
                overlay_geometry=None, overlay_container=None,
                project_folder="", draw_gain=None):
            captured.append(props)
            return {"overlay_path": "/s/seg.mov"}

    def fake_get(name):
        if name == "subtitles.plan":
            return FakePlan()
        if name == "subtitles.render_segment":
            return FakeRender()
        raise AssertionError(f"unexpected operation {name!r}")

    monkeypatch.setattr(reel_spine, "spine_for_reel", fake_spine)
    monkeypatch.setattr(operations, "get", fake_get)

    import unittest.mock as mock

    reel_subtitle_segments(
        mock.MagicMock(), {"segments": []}, [(0.0, 60.0)],
        str(tmp_path), FPS, 1080, 1920, timeline_name="Reel 01")
    return captured


def _item(start_frame: int, duration_frames: int) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=3,
        start_frame=start_frame,
        end_frame=start_frame + duration_frames,
        duration_frames=duration_frames,
        source_start_frame=0, source_end_frame=duration_frames,
        source_file="/s/seg.mov", speaker="Craig", name="seg",
    )


def test_reel_caption_media_is_counted_in_timeline_frames(monkeypatch, tmp_path):
    """The builder's segment length equals the verifier's expectation.

    Fails while the reel path renders at int(round(fps)): the props count
    62 media frames for a span the verifier grades as 61, which is the
    mismatch that read back as F2 delta -1 on every rebuilt segment.
    """
    (props,) = _captured_props(monkeypatch, tmp_path)
    assert props["fps"] == pytest.approx(FPS)
    trimmed = props["_source_out_frame"] - props["_source_in_frame"]
    assert trimmed == EXPECTED_FRAMES, (
        f"segment counted {trimmed} frames for a {SPAN}s span the verifier "
        f"grades as {EXPECTED_FRAMES} - the placer and the verifier count "
        f"at different frame rates")


def test_placed_segment_passes_the_duration_gate(monkeypatch, tmp_path):
    """The contract end to end: what the builder counts draws no F2.

    The placed item is built from the props the fixed call site produces,
    at the record frame the builder places. Before the fix this draws F2
    (placed 62 against expected 61); after, it is clean.
    """
    (props,) = _captured_props(monkeypatch, tmp_path)
    trimmed = props["_source_out_frame"] - props["_source_in_frame"]
    record = int(round(SPAN_START * FPS))
    planned = (PlannedCaption(
        start_seconds=SPAN_START, end_seconds=SPAN_END,
        text="search did not change", speaker="Craig",
        frames=EXPECTED_FRAMES, block_position="body_7"),)
    findings = check_caption_duration(
        "Reel 01", planned, (_item(record, trimmed),), FPS)
    assert findings == [], (
        f"a faithfully placed segment must draw no finding; got "
        f"{[(f.finding_class, f.message) for f in findings]}")


def test_a_genuinely_one_frame_short_segment_still_fails():
    """The gate stays exact: one frame short is still F2 with delta -1.

    This is the anti-tolerance test. The fix moves the builder onto the
    verifier's counting; it must not teach the verifier to stop noticing
    single-frame errors. Unaffected by the fix - passes before and after.
    """
    planned = (PlannedCaption(
        start_seconds=SPAN_START, end_seconds=SPAN_END,
        text="search did not change", speaker="Craig",
        frames=EXPECTED_FRAMES, block_position="body_7"),)
    record = int(round(SPAN_START * FPS))
    short = check_caption_duration(
        "Reel 01", planned, (_item(record, EXPECTED_FRAMES - 1),), FPS)
    assert len(short) == 1
    assert short[0].finding_class == FindingClass.F2
    assert short[0].detail["delta"] == -1
    assert short[0].detail["planned_frames"] == EXPECTED_FRAMES
    assert short[0].detail["actual_frames"] == EXPECTED_FRAMES - 1

    exact = check_caption_duration(
        "Reel 01", planned, (_item(record, EXPECTED_FRAMES),), FPS)
    assert exact == []


# --------------------------------------------------------------------------
# From test_reel24_card_end_derivation.py
#
# Reel 24's 'concise,' card: the end derives from the WORD (pinning tests).
#
# Measured 2026-09-08 through the REAL verifier path (`spine_for_reel` on the
# captain's transcript, moment 24 `why-ai-trusts-youtube` 2009.529-2079.919,
# then `generate_subtitles`, then `check_caption_hangs`):
#
# - word 'concise,': master 2053.648-2055.676 (span 2.028s), reel 44.119-46.147.
#   The timed end IS the VAD segment end (segment 2051.5-2055.68): ~0.4s of
#   utterance (local neighbour rate 0.2-0.5s/word) with ~1.6s of trailing
#   silence padded on. At 2.028s it sits UNDER `MAX_WORD_SECONDS` (3.0s) -
#   and under PR 692's measured genuine ceiling (2.65s) - so
#   `_clamp_stretched_words` is correctly silent here.
# - card 'very strong, concise,': reel 42.671-46.147 (3.48s, 3 words).
# - card end (46.147) == last-word end (46.147) == block-12 end (46.147);
#   the next card starts at 48.273. The block end coincides because block 12
#   is cut from the same VAD segment - it is not an independent derivation.
# - F15 refuses the card: 3.48s for 3 words against the 3.0s limit.
#
# So the trichotomy from the filing - word, next card's start, or block
# boundary - answers WORD on current main: no next-start or block-boundary
# derivation exists in `generate_subtitles` (a card's end is its last word's
# end, plus at most the 0.7s legibility floor; overlaps only ever shrink an
# end or carry the words along). These tests PIN that derivation and the
# gate's refusal of this exact shape. They pass before AND after this diff -
# there is deliberately no behaviour change here: the measurement refuted
# the wrong-derivation premise, and the residual defect (a sub-bound
# trailing-silence stretch) admits no fix that leaves `_clamp_stretched_words`,
# `MAX_WORD_SECONDS` and the F15 limit untouched. See the needs-decision
# record on the task for the options.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


from library.tools.reel_conformance_verifier import (
    check_caption_hangs,
)


# Reel 24's real relative word timings (master seconds, rebased to a
# 100.0 source origin exactly as the spine carries them): 'very'
# 2052.183-2052.544, 'strong,' 2053.086-2053.628 (gap 0.542s), 'concise,'
# 2053.648-2055.676 (span 2.028s, gap 0.020s), then 'there's' 2.990s later.
# The block ends AFTER the word (14.0 > 13.493) so a block-boundary
# derivation would be visible as a different number.
WORDS = [
    {"word": "very", "source_start": 100.000, "source_end": 100.361},
    {"word": "strong,", "source_start": 100.903, "source_end": 101.445},
    {"word": "concise,", "source_start": 101.465, "source_end": 103.493},
    {"word": "there's", "source_start": 106.483, "source_end": 106.684},
]


def _spine():
    return {"structure": [{
        "block_type": "speech",
        "position": 12,
        "timeline_start": 10.0,
        "timeline_end": 17.0,
        "source_start": 100.0,
        "source_end": 107.0,
        "clip_id": "clip_001",
        "alignment_method": "timeline_transcript",
        "word_timestamps": WORDS,
        "content": {"text": " ".join(w["word"] for w in WORDS)},
    }]}


def _entries_3():
    return generate_subtitles(
        _spine(), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]


def _concise_card(entries):
    cards = [e for e in entries if "concise," in e["text"].split()]
    assert len(cards) == 1
    return cards[0]


def test_card_end_is_the_words_end_not_next_start_or_block_end(capsys):
    """The filing's trichotomy, answered in code: WORD.

    The 'concise,' card ends at the word's own timed end (13.493) - not at
    the next card's start (16.483) and not at the block end (17.0). The
    2.028s word is under `MAX_WORD_SECONDS`, so the clamp stays silent;
    assert that too, because a future bound change must update this test
    deliberately rather than drift past it.
    """
    entries = _entries_3()
    card = _concise_card(entries)

    concise = next(w for w in card["words"] if w["word"] == "concise,")
    assert card["timeline_end"] == pytest.approx(concise["end"], abs=0.002)
    assert card["timeline_end"] == pytest.approx(13.493, abs=0.002)

    later_starts = sorted(
        e["timeline_start"] for e in entries
        if e["timeline_start"] > card["timeline_start"])
    assert later_starts, "the next card must exist to separate the candidates"
    assert card["timeline_end"] != pytest.approx(later_starts[0], abs=0.01)
    assert card["timeline_end"] != pytest.approx(17.0, abs=0.01)

    assert "span longer than" not in capsys.readouterr().err


def test_f15_still_refuses_the_reel24_card_shape():
    """The gate keeps its teeth on the real failure shape.

    A ~3.5s three-word card whose last word carries a sub-bound stretch is
    refused, exactly as reel 24 was. The planner emits this shape faithfully
    (see above); the fix for the underlying stretch is NOT here (it would
    be a bound by another name), so the gate must keep refusing it.
    """
    entries = _entries_3()
    card = _concise_card(entries)
    derived = {"reel_start": card["timeline_start"],
               "reel_end": card["timeline_end"], "text": card["text"]}
    findings = check_caption_hangs("Reel 24", [derived], FPS)
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.F15


# --------------------------------------------------------------------------
# From test_reel_subtitle_suppressed_duplicate_alignment.py
#
# A hidden duplicate cannot steal a visible word's caption timing.
#
# Reel 10 (2026-09-29): the cleaned sentence says ``I kind ...`` while the
# transcript timing array carries ``I I kind ...``. The second ``I`` is an
# anchored display suppression. Matching the cleaned sentence against every
# timing token let SequenceMatcher choose that later hidden ``I`` because it
# had the longer matching suffix, leaving the first played ``I`` at 4.62s
# between caption cards. Exercise the same entry point the reel build uses and
# F25's own played-vs-captioned comparison.

PROJECT_RANGE = [(718.59, 775.52)]
SOURCE_FILE = "LCATL0013.MXF"
SEGMENT = {
    "speaker": "Craig",
    "text": "I kind of explain it as what you kind of mentioned.",
    "timeline_start": 723.08,
    "timeline_end": 726.2,
    "source_file": SOURCE_FILE,
    "source_start": 1587.818875,
    "source_end": 1590.938875,
    "resolve_item_id": "c170b6f6-14bc-49c8-95a0-fbd68bfb4efd",
    "words": [
        {"word": "Uh", "start": 723.08, "end": 723.13, "timed": True},
        {"word": "I", "start": 723.21, "end": 723.77, "timed": True},
        {"word": "I", "start": 723.96, "end": 724.07, "timed": True,
         "display": False},
        {"word": "kind", "start": 724.07, "end": 724.32, "timed": True},
        {"word": "of", "start": 724.32, "end": 724.39, "timed": True},
        {"word": "explain", "start": 724.39, "end": 724.81, "timed": True},
        {"word": "it", "start": 724.81, "end": 724.91, "timed": True},
        {"word": "as", "start": 724.91, "end": 725.33, "timed": True},
        {"word": "what", "start": 725.37, "end": 725.6, "timed": True},
        {"word": "you", "start": 725.6, "end": 725.66, "timed": True},
        {"word": "kind", "start": 725.66, "end": 725.83, "timed": True},
        {"word": "of", "start": 725.83, "end": 725.89, "timed": True},
        {"word": "mentioned.", "start": 725.89, "end": 726.2,
         "timed": True},
    ],
}


def _planner_input():
    moment = ReelMoment(
        number=10,
        slug="the-website-wasnt-broken-everything-else",
        reason="regression fixture",
        timeline_start=PROJECT_RANGE[0][0],
        timeline_end=PROJECT_RANGE[0][1],
        source_spans=({
            "source_file": SOURCE_FILE,
            "source_start": 1583.348875,
            "source_end": 1649.299875,
        },),
    )
    transcript = {"segments": [SEGMENT]}
    return moment, transcript


def _f25_inputs(plan, transcript, project_folder):
    segment = transcript["segments"][0]
    audio_spans = [{
        "source_file": SOURCE_FILE,
        "source_start": segment["source_start"],
        "source_end": segment["source_end"],
        "reel_start": reel_build.reel_time(
            segment["timeline_start"], PROJECT_RANGE),
    }]
    played_result = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], audio_spans)
    played = subtitle_coverage.read_words_for_comparison(
        played_result["words"],
        transcript_corrections.spelling_corrections(project_folder))
    suppressed = verifier._suppressed_played_words(played, project_folder)

    cards = []
    captioned = []
    for entry in plan.caption_entries:
        card = entry["id"]
        cards.append({
            "card": card,
            "reel_start": entry["timeline_start"],
            "reel_end": entry["timeline_end"],
            "text_norms": sorted({
                subtitle_coverage.normalize_word(token)
                for token in entry["text"].split()
                if subtitle_coverage.normalize_word(token)
            }),
        })
        for word in entry.get("words") or []:
            norm = subtitle_coverage.normalize_word(word["word"])
            if norm:
                captioned.append({
                    "word": word["word"],
                    "norm": norm,
                    "card": card,
                    "reel_start": word["start"],
                    "reel_end": word["end"],
                })
    return played, captioned, cards, suppressed


def test_build_planner_covers_played_i_and_f25_passes(tmp_path, monkeypatch):
    project = str(tmp_path)
    transcript_corrections.record_display_suppression(
        project, "uh", "the filler before the sentence is not captioned")
    transcript_corrections.record_display_suppression(
        project, "I", "the second I is a false start",
        scope={"speaker": "Craig", "surface": "I",
               "prev": "I", "next": "kind"})
    moment, transcript = _planner_input()
    monkeypatch.setattr(
        generate_remotion_props, "generate_subtitle_props_per_block",
        lambda *args, **kwargs: [],
    )

    # This is the exact caption-planning entry point called from the reel
    # builder. Empty props skip rendering only; the pipeline plan is real.
    plan = reel_build.reel_subtitle_segments(
        moment, transcript, PROJECT_RANGE, project,
        fps=FPS, width=1080, height=1920,
    )
    played, captioned, cards, suppressed = _f25_inputs(
        plan, transcript, project)
    coverage = subtitle_coverage.check_word_coverage(
        played, captioned, cards, suppressed=suppressed)

    spoken_i = next(word for word in played
                    if word["norm"] == "i" and not word.get("suppression"))
    covering = [card for card in cards
                if card["reel_start"] <= spoken_i["reel_start"]
                and card["reel_end"] >= spoken_i["reel_end"]]
    assert covering, (
        "the build's planned cards leave the spoken I between cards")
    card = covering[0]
    assert any(word["card"] == card["card"] and word["norm"] == "i"
               and abs(word["reel_start"] - spoken_i["reel_start"])
               < 0.001
               for word in captioned)
    assert [finding for finding in coverage["findings"]
            if finding["severity"] == "error"] == []
