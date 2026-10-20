"""A video-only clip on the timeline must not fail the transcription.

The timeline transcript REBUILDS each speaker's audio from the source
media, span by span. A freeze frame, a still and a title graphic carry
no audio stream, and the timeline plays NOTHING where they sit - but
ffmpeg exits 234 on such a file ("Output file does not contain any
stream"), which failed the whole transcription of a reel that carries a
freeze frame. Measured 2026-10-20 on Reel 13 of the Ren Scratch -
breaktest project.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from library.tools import timeline_transcript


def test_a_file_with_no_audio_stream_is_recognised(tmp_path):
    """Catches: the probe mistaking a silent file for an unreadable one,
    so the span is extracted anyway and the extraction fails."""
    silent = tmp_path / "silent.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=64x64:d=1", "-pix_fmt", "yuv420p", str(silent)],
        check=True, capture_output=True)
    assert timeline_transcript._has_audio_stream(str(silent)) is False


def test_a_file_with_audio_is_recognised(tmp_path):
    """Catches: the probe calling a file with sound silent, which would
    drop the speech."""
    loud = tmp_path / "loud.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=1", "-pix_fmt", "yuv420p", str(loud)],
        check=True, capture_output=True)
    assert timeline_transcript._has_audio_stream(str(loud)) is True


def test_an_unreadable_probe_is_not_an_answer(tmp_path):
    """Catches: a probe that fails being read as "no audio", which would
    silence a file that does carry sound."""
    assert timeline_transcript._has_audio_stream(
        str(tmp_path / "not-there.mov")) is True


def test_a_silent_clip_is_silence_not_a_failure(tmp_path, monkeypatch):
    """Catches: the audio rebuild failing on a clip that plays nothing."""
    calls = []

    class _Clip:
        source_file = "/does/not/exist.mov"
        source_in = 0.0
        source_out = 1.0
        timeline_start = 0.0
        timeline_end = 1.0

    monkeypatch.setattr(
        timeline_transcript, "_has_audio_stream", lambda path: False)
    monkeypatch.setattr(
        timeline_transcript, "extract_span",
        lambda *args, **kwargs: calls.append(args) or Path("never"))

    out = tmp_path / "out.wav"
    timeline_transcript.build_speaker_audio([_Clip()], out, tmp_path)

    assert calls == [], "a clip with no audio stream was extracted"
    assert out.is_file()
    assert out.stat().st_size > 44, "the rebuilt track is empty"
