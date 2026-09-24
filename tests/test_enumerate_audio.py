"""Voiceover and music audio enumerate separately from video.

The defect: `footage_identity` accepted video extensions only, so a
voiceover take or music bed in `raw/` never entered the pipeline at
all. Audio enumerates in its own `audio_001` space - admitting it
into the video numbering would renumber every video clip after it
and orphan every cached per-clip analysis.
"""

from __future__ import annotations

import pytest

from library.tools.footage_identity import (
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    enumerate_audio,
    enumerate_footage,
    fingerprints_for,
)


def _project(tmp_path, files):
    project = tmp_path / "project"
    raw = project / "raw"
    raw.mkdir(parents=True)
    for name, body in files.items():
        (raw / name).write_bytes(body)
    return str(project)


def test_audio_files_enumerate_in_their_own_space(tmp_path):
    folder = _project(tmp_path, {
        "b.mp4": b"x" * 64, "a.mp4": b"y" * 64,
        "voiceover.wav": b"z" * 64, "bed.mp3": b"w" * 64,
        "notes.txt": b"not media",
    })
    audio, skipped = enumerate_audio(folder)
    assert skipped == []
    assert [(e["audio_id"], e["filename"]) for e in audio] == [
        ("audio_001", "bed.mp3"), ("audio_002", "voiceover.wav")]
    assert all("clip_id" not in e for e in audio)


def test_video_numbering_is_untouched_by_audio(tmp_path):
    """The renumbering this separation exists to prevent: adding a
    voiceover must not move clip_001."""
    folder = _project(tmp_path, {"a.mp4": b"x" * 64})
    before, _ = enumerate_footage(folder)
    assert [e["clip_id"] for e in before] == ["clip_001"]
    open(folder + "/raw/voiceover.wav", "wb").write(b"z" * 64)
    after, _ = enumerate_footage(folder)
    assert [e["clip_id"] for e in after] == ["clip_001"]
    audio, _ = enumerate_audio(folder)
    assert [e["audio_id"] for e in audio] == ["audio_001"]


def test_audio_and_video_share_one_fingerprint_record(tmp_path):
    """The identity check holds both spaces in one record without a
    collision: `audio_001` is not `clip_001`."""
    folder = _project(tmp_path, {
        "a.mp4": b"x" * 64, "voiceover.wav": b"z" * 64})
    video, _ = enumerate_footage(folder)
    audio, _ = enumerate_audio(folder)
    record = fingerprints_for(video + audio)
    assert sorted(record) == ["audio_001", "clip_001"]
    assert (record["audio_001"]["path"]
            != record["clip_001"]["path"])


def test_audio_extensions_cover_voiceover_sources():
    assert {".mp3", ".wav", ".m4a", ".flac"} <= SUPPORTED_AUDIO_EXTENSIONS
    assert not (SUPPORTED_AUDIO_EXTENSIONS & SUPPORTED_VIDEO_EXTENSIONS)


def test_zero_byte_audio_is_skipped_not_cataloged(tmp_path):
    folder = _project(tmp_path, {"empty.wav": b"",
                                 "bed.mp3": b"w" * 64})
    audio, skipped = enumerate_audio(folder)
    assert [e["audio_id"] for e in audio] == ["audio_001"]
    assert len(skipped) == 1 and "zero-byte" in skipped[0]["reason"]


def test_missing_raw_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        enumerate_audio(str(tmp_path / "nope"))


def test_no_audio_is_an_empty_list_not_an_error(tmp_path):
    """Video-only projects - the normal case - enumerate to []."""
    folder = _project(tmp_path, {"a.mp4": b"x" * 64})
    audio, skipped = enumerate_audio(folder)
    assert audio == [] and skipped == []
