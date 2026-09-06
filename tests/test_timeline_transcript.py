"""What the timeline says, rebuilt from source spans rather than rendered.

The design claim worth testing is the CACHE KEY. The captain asked to be
able to re-index one portion of the cut - a clip that was unused becoming
used, or an earlier index being wrong - and that is only possible if an
extracted span is addressed by the span itself and not by where it sits.
`test_the_cache_key_ignores_timeline_position` is that claim.

ffmpeg is real here; the fixtures are tiny generated tones, so the tests
measure the assembly rather than mocking it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import timeline_transcript as tt
from library.tools.timeline_ingest import TimelineClip

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required; CI installs them (AGENTS.md 9)")


def _tone(path: Path, seconds: float, freq: int = 440) -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True)
    return path


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, encoding="utf-8", check=True)
    return float(out.stdout.strip())


FPS = 24000 / 1001


def _clip(source, src_in, src_out, tl_start, tl_end, uid="uid", speaker="A",
          source_frames=None):
    return TimelineClip(
        resolve_item_id=uid, track_type="video", track_index=1,
        track_name=speaker, speaker=speaker, source_file=str(source),
        source_in=src_in, source_out=src_out,
        source_in_frame=round(src_in * FPS),
        source_out_frame=round(src_out * FPS),
        source_frames=source_frames,
        timeline_start=tl_start, timeline_end=tl_end, name="clip")


# ── The cache key, which is what makes re-indexing possible ──────────

def test_the_cache_key_ignores_timeline_position(tmp_path):
    """The captain's ask: re-index one portion. A key that included the
    cut position would re-extract a clip that merely MOVED, and would
    miss a span already extracted for a different clip."""
    a = tt.span_cache_key("/m/LC4930.MXF", 131.4, 151.7)
    b = tt.span_cache_key("/m/LC4930.MXF", 131.4, 151.7)
    assert a == b


def test_a_different_span_gets_a_different_key():
    a = tt.span_cache_key("/m/LC4930.MXF", 131.4, 151.7)
    assert a != tt.span_cache_key("/m/LC4930.MXF", 131.4, 151.8)
    assert a != tt.span_cache_key("/m/LC4931.MXF", 131.4, 151.7)


def test_the_key_is_stable_across_float_noise():
    assert (tt.span_cache_key("/m/x.MXF", 10.0000001, 20.0)
            == tt.span_cache_key("/m/x.MXF", 10.0, 20.0))


def test_the_cache_key_enumeration_is_complete():
    assert tt.AUDIO_CACHE_KEYS == ("source_file", "source_in", "source_out",
                                   "sample_rate", "channels")


# ── Rebuilding the audio ─────────────────────────────────────────────

def test_a_span_is_extracted_at_the_right_length(tmp_path):
    source = _tone(tmp_path / "src.wav", 10.0)
    out = tt.extract_span(str(source), 2.0, 5.0, tmp_path / "cache")
    assert out.exists()
    assert _duration(out) == pytest.approx(3.0, abs=0.05)


def test_an_extracted_span_is_reused_rather_than_re_extracted(tmp_path):
    source = _tone(tmp_path / "src.wav", 10.0)
    first = tt.extract_span(str(source), 2.0, 5.0, tmp_path / "cache")
    stamp = first.stat().st_mtime_ns
    again = tt.extract_span(str(source), 2.0, 5.0, tmp_path / "cache")
    assert again == first
    assert again.stat().st_mtime_ns == stamp, "it re-extracted a cached span"


def test_an_empty_span_is_refused(tmp_path):
    source = _tone(tmp_path / "src.wav", 10.0)
    with pytest.raises(tt.TimelineTranscriptError):
        tt.extract_span(str(source), 5.0, 5.0, tmp_path / "cache")


def test_the_rebuilt_track_puts_the_gaps_back(tmp_path):
    """Two 2s clips with a 6s hole between them, starting 4s in, is a
    14s track - and that is what makes the transcript's timings already
    be timeline timings."""
    source = _tone(tmp_path / "src.wav", 30.0)
    clips = [
        _clip(source, 1.0, 3.0, 4.0, 6.0, uid="a"),
        _clip(source, 10.0, 12.0, 12.0, 14.0, uid="b"),
    ]
    out = tt.build_speaker_audio(clips, tmp_path / "spk.wav",
                                 tmp_path / "cache")
    assert _duration(out) == pytest.approx(14.0, abs=0.1)


def test_a_track_starting_at_zero_gets_no_head_silence(tmp_path):
    source = _tone(tmp_path / "src.wav", 30.0)
    clips = [_clip(source, 1.0, 3.0, 0.0, 2.0)]
    out = tt.build_speaker_audio(clips, tmp_path / "spk.wav",
                                 tmp_path / "cache")
    assert _duration(out) == pytest.approx(2.0, abs=0.05)


def test_the_track_ends_at_the_last_clip_not_the_timeline(tmp_path):
    """Trailing silence would be padding nobody measured."""
    source = _tone(tmp_path / "src.wav", 30.0)
    clips = [_clip(source, 1.0, 3.0, 0.0, 2.0)]
    out = tt.build_speaker_audio(clips, tmp_path / "spk.wav",
                                 tmp_path / "cache")
    assert _duration(out) < 3.0


def test_building_with_no_clips_is_refused(tmp_path):
    with pytest.raises(tt.TimelineTranscriptError):
        tt.build_speaker_audio([], tmp_path / "x.wav", tmp_path / "cache")


# ── Binding speech back to the footage ───────────────────────────────

def test_speech_inside_one_clip_is_attributed_to_it(tmp_path):
    clips = [_clip("/m/a.MXF", 100.0, 120.0, 10.0, 30.0, uid="a")]
    assert tt.attribute_to_clip(12.0, 18.0, clips).resolve_item_id == "a"


def test_speech_straddling_a_cut_is_attributed_to_nothing(tmp_path):
    """Two clips means two places in the raw footage. Naming either would
    be a false ground truth, so it says it cannot tell."""
    clips = [_clip("/m/a.MXF", 100.0, 120.0, 10.0, 30.0, uid="a"),
             _clip("/m/b.MXF", 5.0, 25.0, 30.0, 50.0, uid="b")]
    assert tt.attribute_to_clip(28.0, 34.0, clips) is None


def test_speech_in_a_gap_is_attributed_to_nothing():
    clips = [_clip("/m/a.MXF", 100.0, 120.0, 10.0, 30.0, uid="a")]
    assert tt.attribute_to_clip(40.0, 45.0, clips) is None


def test_a_timeline_second_maps_back_into_the_source():
    clip = _clip("/m/a.MXF", 100.0, 120.0, 10.0, 30.0)
    assert tt.to_source_time(clip, 10.0) == 100.0
    assert tt.to_source_time(clip, 25.0) == 115.0


# ── Untimed words are interpolated, never dropped ────────────────────

def test_an_untimed_word_is_interpolated_not_dropped():
    """The one-off generate_podcast_subtitles.py dropped these silently."""
    words = [{"word": "hello", "start": 1.0, "end": 1.4},
             {"word": "there"},
             {"word": "friend", "start": 2.0, "end": 2.5}]
    out = tt.interpolate_untimed_words(words)
    assert [w["word"] for w in out] == ["hello", "there", "friend"]
    assert out[1]["timed"] is False
    assert 1.4 <= out[1]["start"] < out[1]["end"] <= 2.0


def test_a_timed_word_is_marked_as_measured():
    out = tt.interpolate_untimed_words([{"word": "hi", "start": 0.0, "end": 0.3}])
    assert out[0]["timed"] is True


def test_an_untimed_word_with_no_neighbours_is_dropped():
    """Nothing to interpolate FROM. Dropping is honest; inventing is not."""
    assert tt.interpolate_untimed_words([{"word": "alone"}]) == []


# ── The document ─────────────────────────────────────────────────────

def test_the_document_reports_what_could_not_be_bound():
    class Snap:
        project_name, timeline_name = "P", "T"
        fps, duration = 23.976, 100.0
        clips = []
        def speakers(self): return ["Craig", None]

    merged = [
        tt.SpokenSegment("Craig", "bound", 1.0, 2.0, "/m/a.MXF", 10.0, 11.0, "a"),
        tt.SpokenSegment("Craig", "straddles", 3.0, 4.0, None, None, None, None),
    ]
    doc = tt.transcript_document(Snap(), merged)
    assert doc["segment_count"] == 2
    assert doc["segments_straddling_a_cut"] == 1
    # From the SEGMENTS, not the snapshot's track roster - Snap() lists
    # a second entry nothing speaks under, exactly as the real one lists
    # `Akshita CH1` beside `Akshita`.
    assert doc["speakers"] == ["Craig"]
    assert "Resolve was not opened" in doc["measurement"]


# ── A row that crosses a cut is re-read from its own words ───────────
#
# The numbers here are the field test's, not invented: Craig's clips at
# 606.796-614.013 and 614.765-616.560 and 630.909-636.583, and rows 204
# and 206 of `transcript.json` as WhisperX left them. Reel 05 plays this
# stretch and had no caption over about nine seconds of it.

CLIP_A = {"src_in": 1273.898, "tl_start": 606.796, "tl_end": 614.013,
          "uid": "A"}
CLIP_B = {"src_in": 1289.830, "tl_start": 614.765, "tl_end": 616.560,
          "uid": "B"}
CLIP_C = {"src_in": 1421.295, "tl_start": 630.909, "tl_end": 636.583,
          "uid": "C"}


def _craig_clips():
    return [_clip("/m/LCATL0013.MXF", c["src_in"],
                  c["src_in"] + (c["tl_end"] - c["tl_start"]),
                  c["tl_start"], c["tl_end"], uid=c["uid"], speaker="Craig")
            for c in (CLIP_A, CLIP_B, CLIP_C)]


def _word(text, start, end):
    return {"word": text, "start": start, "end": end}


ROW_204 = {
    "start": 609.380, "end": 615.340,
    "text": "on here yeah so ranking",
    "words": [_word("on", 613.574, 613.654),
              _word("here", 613.715, 613.875),
              _word("yeah", 614.818, 615.059),
              _word("so", 615.079, 615.139),
              _word("ranking", 615.159, 615.340)],
}

ROW_206 = {
    "start": 615.540, "end": 636.200,
    "text": "tells google when it comes",
    "words": [_word("tells", 615.540, 615.860),
              _word("google", 615.921, 616.441),
              _word("when", 631.115, 631.255),
              _word("it", 631.295, 631.355),
              _word("comes", 631.415, 631.716)],
}


def test_a_row_crossing_a_cut_is_split_at_its_own_words():
    """Row 204. No WORD crosses the cut at 614.0/614.8 - eighteen end
    before it and three begin after - so the row has two bindings, not
    none."""
    out = tt.segments_for_speaker({"segments": [ROW_204]}, "Craig",
                                  _craig_clips())
    assert [s.resolve_item_id for s in out] == ["A", "B"]
    assert [s.text for s in out] == ["on here", "yeah so ranking"]
    assert out[0].timeline_start == pytest.approx(613.574)
    assert out[0].timeline_end == pytest.approx(613.875)
    assert out[1].timeline_start == pytest.approx(614.818)
    assert out[1].timeline_end == pytest.approx(615.340)
    # SOURCE seconds, mapped through the clip each part actually sits on.
    assert out[0].source_start == pytest.approx(
        1273.898 + (613.574 - 606.796))
    assert out[1].source_start == pytest.approx(
        1289.830 + (614.818 - 614.765))
    assert all(s.read_from_words for s in out)


def test_a_row_whose_middle_is_silence_binds_both_ends():
    """Row 206 spans 615.5-636.2, and Craig has no clip for 14.4s of
    that. The row carries NO WORD in the middle: the envelope is Whisper
    joining two utterances, and both ends are on real clips."""
    out = tt.segments_for_speaker({"segments": [ROW_206]}, "Craig",
                                  _craig_clips())
    assert [s.resolve_item_id for s in out] == ["B", "C"]
    assert [s.text for s in out] == ["tells google", "when it comes"]
    # Nothing is emitted for the silence itself.
    assert not any(616.5 < s.timeline_start < 631.0 for s in out)


def test_a_row_inside_one_clip_is_left_exactly_as_it_was():
    """The 814 rows that already bind must not move. `attribute_to_clip`
    answering means the question is answered."""
    row = {"start": 607.5, "end": 610.0, "text": "Whisper's own text.",
           "words": [_word("Whisper's", 607.5, 608.0),
                     _word("own", 608.2, 609.0),
                     _word("text.", 609.2, 610.0)]}
    out = tt.segments_for_speaker({"segments": [row]}, "Craig",
                                  _craig_clips())
    assert len(out) == 1
    assert out[0].resolve_item_id == "A"
    assert out[0].text == "Whisper's own text."
    assert out[0].timeline_start == 607.5 and out[0].timeline_end == 610.0
    assert out[0].read_from_words is False


def test_a_row_wholly_in_a_gap_stays_unbound():
    """The honest outcome, unchanged. Nine of the field test's nineteen
    unbindable words are single words the aligner stretched across a
    silence; nothing here invents a clip for them."""
    row = {"start": 620.0, "end": 628.0, "text": "well",
           "words": [_word("well", 620.0, 628.0)]}
    out = tt.segments_for_speaker({"segments": [row]}, "Craig",
                                  _craig_clips())
    assert len(out) == 1
    assert out[0].resolve_item_id is None
    assert out[0].source_start is None
    assert out[0].text == "well"


def test_a_row_that_reaches_a_clip_only_at_its_edges_keeps_the_gap_out():
    """Head on a clip, middle in the gap, tail on the next: three parts,
    and the middle one is still refused."""
    row = {"start": 613.0, "end": 632.0, "text": "here well when",
           "words": [_word("here", 613.7, 613.9),
                     _word("well", 620.0, 628.0),
                     _word("when", 631.1, 631.3)]}
    out = tt.segments_for_speaker({"segments": [row]}, "Craig",
                                  _craig_clips())
    assert [s.resolve_item_id for s in out] == ["A", None, "C"]
    assert [s.text for s in out] == ["here", "well", "when"]


def test_a_word_is_placed_by_the_same_containment_rule_with_no_tolerance():
    """`clip_of_word` is `attribute_to_clip`'s own test, asked of the
    atom. A word one millisecond outside a clip is outside it - the
    rebuilt track plays silence there, so there is nothing to be inside
    of."""
    clips = _craig_clips()
    assert tt.clip_of_word(_word("x", 613.0, 613.2), clips).resolve_item_id == "A"
    just_out = _word("x", 614.014, 614.016)
    assert tt.clip_of_word(just_out, clips) is None


def test_the_runs_are_maximal():
    """Consecutive words on one clip are ONE run, not one run each: a
    caption card per word is not what a split is for."""
    runs = tt.clip_runs(ROW_204["words"], _craig_clips())
    assert [(c.resolve_item_id if c else None, len(w)) for c, w in runs] \
        == [("A", 2), ("B", 3)]


def test_the_document_says_how_much_was_re_read():
    class Snap:
        project_name, timeline_name = "P", "T"
        fps, duration = 23.976, 100.0
        clips = ()

        def speakers(self):
            return ["Craig"]

    merged = tt.segments_for_speaker({"segments": [ROW_204]}, "Craig",
                                     _craig_clips())
    doc = tt.transcript_document(Snap(), merged)
    assert doc["segments_read_from_words"] == 2
    assert doc["segments_rebound_from_words"] == 2
    assert doc["segments_straddling_a_cut"] == 0
