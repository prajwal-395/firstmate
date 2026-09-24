"""One clamp for both transcript paths, and the start it leaves alone.

`library/tools/word_boundaries.py` serves `step_1_04_temporal_index`
and `timeline_transcript.segments_for_speaker`. These pin that the two
call sites are the SAME function, that a stretched word is cut back to
its start plus the median while the start never moves, and that the
existing unfitted-text counting reads identically before and after -
the two must agree, not double-report the same rows.

No ffmpeg, no models, no audio: every case is fixtures. This file
carries no ffmpeg skip mark on purpose, so it runs in environments
where `test_timeline_transcript.py` skips.
"""

from __future__ import annotations

from library.tools import timeline_transcript as tt
from library.tools import transcript_fit
from library.tools.timeline_ingest import TimelineClip
from library.tools.word_boundaries import sanitize_word_boundaries


FPS = 24000 / 1001


def _clip(source, src_in, src_out, tl_start, tl_end, uid="uid",
          speaker="Craig"):
    return TimelineClip(
        resolve_item_id=uid, track_type="video", track_index=1,
        track_name=speaker, speaker=speaker, source_file=str(source),
        source_in=src_in, source_out=src_out,
        source_in_frame=round(src_in * FPS),
        source_out_frame=round(src_out * FPS),
        source_frames=None,
        timeline_start=tl_start, timeline_end=tl_end, name="clip")


def _word(text, start, end, **extra):
    return {"word": text, "start": start, "end": end, **extra}


# ── The shared implementation ──────────────────────────────────────



def test_a_stretched_word_is_cut_back_to_start_plus_median():
    words = [_word("and", 100.00, 100.28),
             _word("then", 100.34, 100.61),
             _word("audits", 100.67, 134.80),
             _word("resume", 141.02, 141.40)]
    out = sanitize_word_boundaries(words)
    assert out is words
    # Median of the ordinary spans (0.28, 0.27, 0.38) is 0.28.
    assert out[2]["start"] == 100.67
    assert out[2]["end"] == 100.95
    # Everything else is untouched.
    assert (out[0]["start"], out[0]["end"]) == (100.00, 100.28)
    assert (out[3]["start"], out[3]["end"]) == (141.02, 141.40)


def test_a_lone_stretched_word_falls_back_to_three_tenths():
    """A single-word segment has no median to read; the 0.3s fallback
    is step 1.04's historical answer, kept so both paths agree."""
    out = sanitize_word_boundaries([_word("well", 620.0, 628.0)])
    assert (out[0]["start"], out[0]["end"]) == (620.0, 620.3)


def test_degenerate_and_overlapping_boundaries_are_fixed():
    out = sanitize_word_boundaries([
        _word("a", 1.0, 1.0),
        _word("b", 1.1, 1.5),
        _word("c", 1.4, 1.8),
    ])
    assert out[0]["end"] == 1.02
    assert out[1]["end"] == 1.4


def test_sanitising_preserves_every_key_and_every_word():
    """`timed` and the aligner score travel on the words; the clamp
    moves ends, never membership - which is what the unfitted-text
    counting below rests on."""
    words = [_word("hi", 0.0, 9.0, timed=True, alignment_score=0.9),
             _word("there", 9.1, 9.4, timed=True, alignment_score=0.8)]
    out = sanitize_word_boundaries(words)
    assert [w["word"] for w in out] == ["hi", "there"]
    assert out[0]["timed"] is True
    assert out[0]["alignment_score"] == 0.9
    assert out[1]["end"] == 9.4




# ── The reels path, on field-test-shaped fixtures ──────────────────
#
# "audits": one word spanning 34.13s, its start 60ms after the previous
# word's end and on a real clip, its end 30s out in a clip gap. The
# start is trustworthy WITHOUT reading the word's own end - the gap
# before it (0.06s, an independent measurement off the previous word)
# sits in rhythm with the row's other gaps, and the clip list is
# independent of the aligner. Had the speech been at the tail, the gap
# before it would be the whole silence. So the end is cut and the start
# stays, exactly as the preflight does.

CLIPS = [_clip("/m/a.MXF", 1000.0, 1015.0, 90.0, 105.0, uid="A"),
         _clip("/m/b.MXF", 2000.0, 2010.0, 140.0, 150.0, uid="B")]

AUDITS_ROW = {
    "start": 99.0, "end": 141.0,
    "text": "and then audits resume",
    "words": [_word("and", 100.00, 100.28),
              _word("then", 100.34, 100.61),
              _word("audits", 100.67, 134.80),
              _word("resume", 141.02, 141.40)],
}


def test_a_stretched_word_no_longer_unbinds_its_row():
    """Before the fix this row split into three runs - A, unbound, B -
    because the stretched midpoint sat in the gap. Now the clamp puts
    the midpoint back on the clip the speech came from."""
    out = tt.segments_for_speaker({"segments": [AUDITS_ROW]}, "Craig",
                                  CLIPS)
    assert [s.resolve_item_id for s in out] == ["A", "B"]
    assert [s.text for s in out] == ["and then audits", "resume"]
    assert all(s.read_from_words for s in out)




def test_a_word_in_a_gap_stays_unbound_after_the_clamp():
    """Clamping must not invent a binding. "well" sits in the gap at
    120s; cut back to 0.3s it still sits in the gap, and the honest
    answer is still None."""
    row = {"start": 119.0, "end": 129.0, "text": "well",
           "words": [_word("well", 120.0, 128.0)]}
    out = tt.segments_for_speaker({"segments": [row]}, "Craig", CLIPS)
    assert len(out) == 1
    assert out[0].resolve_item_id is None
    assert out[0].words[0]["start"] == 120.0
    assert out[0].words[0]["end"] == 120.3


# ── Agreement with the unfitted-text counting ──────────────────────
#
# `transcript_document` counts rows whose text outruns their timings
# (`transcript_fit.row_fit`: timed words against text words). The clamp
# moves ends and never membership, so those counts must read
# identically on the stretched and the clamped document - agreement,
# not a second report on the same rows.


class _Snap:
    project_name, timeline_name = "P", "T"
    fps, duration = 23.976, 700.0
    clips = ()

    def speakers(self):
        return ["Craig"]


def _document(words_per_row):
    """One document per word list, same texts, same spans."""
    segments = []
    for words in words_per_row:
        segments.append(tt.SpokenSegment(
            speaker="Craig", text=" ".join(w["word"] for w in words),
            timeline_start=100.0, timeline_end=142.0,
            source_file="/m/a.MXF", source_start=1000.0,
            source_end=1042.0, resolve_item_id=None,
            words=tuple(words)))
    return tt.transcript_document(_Snap(), segments)


def test_clamping_changes_no_unfitted_text_count():
    stretched = [[_word("and", 100.00, 100.28),
                  _word("audits", 100.67, 134.80)],
                 [_word("well", 120.0, 128.0)]]
    clamped = [[dict(w) for w in row] for row in stretched]
    for row in clamped:
        sanitize_word_boundaries(row)
    before = transcript_fit.scan(_document(stretched))
    after = transcript_fit.scan(_document(clamped))
    assert before["unfitted_rows"] == after["unfitted_rows"] == 0
    assert before["words_with_no_timing"] == after["words_with_no_timing"] == 0
    assert before["rows"] == after["rows"] == 2
