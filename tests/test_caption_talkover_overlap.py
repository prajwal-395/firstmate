"""Reel 15 (2026-09-08): a talk-over captioned twice on one track.

The rebuild refused reel 15 with two findings that are one defect seen
from both sides: caption segment 5 (block 4, 'google rewards') was
PLANNED at 14.93s and NEVER PLACED (F14), and its neighbours overlap by
43 frames / 1.79s (F6).

The reel spine keeps a real talk-over on both mics by design -
`reel_spine._drop_bleed` drops only same-word bleed, and different words
at the same instant are two people talking over each other.  Step 4.01
planned each block independently and only WARNED about the resulting
cross-block overlap, so the plan asked two cards to cover the same
seconds on one V3 track.  Resolve trims the later one's head, shifting
it 43 frames off its planned start - outside the 2-frame mechanical
pairing tolerance, which is why the segment reads as "never placed".

Verdict: the PLANNER was wrong, the placer faithful, the gate correct.
`generate_subtitles` now resolves cross-card overlaps (the earlier card
yields to the next card's first word, or joins it where the trim would
flash), and F6/F14 are unchanged - they must keep refusing a plan that
still carries this shape.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.steps.step_4_01_plan_subtitles.step import (
    generate_subtitles,
    resolve_caption_overlaps,
)
from library.tools.reel_conformance_verifier import (
    FindingClass,
    PlannedCaption,
    TimelineItem,
    check_caption_duration,
    check_caption_overlaps,
)

FPS = 24000 / 1001


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
    assert fix == {"trimmed": 0, "merged": 1}, fix
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
    assert fix == {"trimmed": 0, "merged": 1}, fix
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
    assert fix == {"trimmed": 0, "merged": 1}, fix
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


def test_gate_still_reports_a_segment_trimmed_off_its_planned_start():
    """F14 fires when a planned segment has no placed item within the
    2-frame mechanical pairing tolerance - the shape Resolve's trim of
    an overlapping card produces.  Unaffected by the planner fix, which
    stops the trim happening rather than widening the tolerance."""
    planned = (
        _planned(10.0, 12.0, "for small businesses", block=3),
        _planned(14.93, 16.5, "google rewards", block=4),
    )
    seg2_start_frame = round(14.93 * FPS)
    placed = (
        _placed(round(10.0 * FPS), round(12.0 * FPS) - round(10.0 * FPS)),
        # the later head trimmed 43 frames, as Resolve does to the
        # second of two overlapping items on one track
        _placed(seg2_start_frame + 43, 30),
    )
    findings = check_caption_duration("reel 15", planned, placed, FPS)
    f14 = [f for f in findings if f.finding_class == FindingClass.F14]
    assert len(f14) == 1, [f.as_dict() for f in findings]
    assert f14[0].detail["planned_start_frame"] == seg2_start_frame
