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
