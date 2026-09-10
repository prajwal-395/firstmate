"""A capture that did not happen raises by name - never a usable value.

On 2026-09-10 a lane reported the captain's `.drx` grade moving 599,583
pixels (28.9% of the frame); the real number was 486 pixels (0.023%).
The cause: the `GrabStill` + `ExportStills` route returned False and
wrote no file on Resolve Studio 21.0.0b, and the caller measured anyway -
the "measurements" clustered around 600,000 px because they were the
picture area, not a picture.  See `marker_capture`'s "WHEN THE ROUTE
FAILS".

These tests pin the rule with fakes (no Resolve): all three failure
shapes - `GrabStill` declining, `ExportStills` returning False, and no
or empty file on disk - raise `StillCaptureError` (a `CaptureError`, so
existing handlers still catch it).  The ffmpeg capture helpers pin the
same rule in their own idiom: a zero-byte file is failure, not a still.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.marker_capture import (  # noqa: E402
    CaptureError,
    StillCaptureError,
    grab_still,
)


# ── Fakes: Resolve, gallery side only ─────────────────────────────


class _Album:
    """A still album whose export behaviour the test dictates."""

    def __init__(self, exported=True, write_png=True, png_bytes=b"\x89PNG\r\n\x1a\n" + b"1" * 64):
        self._exported = exported
        self._write_png = write_png
        self._png_bytes = png_bytes

    def GetStills(self):
        return []

    def ExportStills(self, stills, directory, prefix, fmt):
        if self._write_png:
            Path(directory, f"{prefix}_1.1.1.{fmt}").write_bytes(self._png_bytes)
        return self._exported

    def DeleteStills(self, stills):
        return True


class _Gallery:
    def __init__(self, album):
        self._album = album

    def GetCurrentStillAlbum(self):
        return self._album

    def GetAlbumName(self, album):
        return "test-album"

    def GetGalleryStillAlbums(self):
        return [self._album]


class _Project:
    def __init__(self, album):
        self._gallery = _Gallery(album)

    def GetGallery(self):
        return self._gallery


class _Timeline:
    def __init__(self, still):
        self._still = still

    def GrabStill(self):
        return self._still


_SOME_STILL = object()
"""A truthy stand-in for the opaque GalleryStill Resolve returns."""


def _grab(exported=True, write_png=True, png_bytes=b"\x89PNG\r\n\x1a\n" + b"1" * 64,
          still=_SOME_STILL, tmp_path=None):
    album = _Album(exported=exported, write_png=write_png, png_bytes=png_bytes)
    destination = Path(str(tmp_path)) / "still.png"
    return grab_still(_Timeline(still), _Project(album), destination)


# ── The three failure shapes ──────────────────────────────────────


def test_grabstill_false_raises_by_name(tmp_path):
    """`GrabStill` returning False is a capture that did not happen."""
    with pytest.raises(StillCaptureError, match="declined to grab"):
        _grab(still=False, tmp_path=tmp_path)


def test_grabstill_none_raises_by_name(tmp_path):
    with pytest.raises(StillCaptureError, match="declined to grab"):
        _grab(still=None, tmp_path=tmp_path)


def test_exportstills_false_raises_by_name(tmp_path):
    """The 2026-09-10 shape: False return, nothing on disk."""
    with pytest.raises(StillCaptureError, match="ExportStills returned False"):
        _grab(exported=False, write_png=False, tmp_path=tmp_path)


def test_exportstills_false_raises_even_when_a_file_is_there(tmp_path):
    """A False return is authoritative: a file beside it is not this
    call's file, so it must not reach a caller that would average it."""
    with pytest.raises(StillCaptureError, match="ExportStills returned False"):
        _grab(exported=False, write_png=True, tmp_path=tmp_path)


def test_exportstills_true_with_no_file_raises_by_name(tmp_path):
    with pytest.raises(StillCaptureError, match="wrote no png"):
        _grab(exported=True, write_png=False, tmp_path=tmp_path)


def test_zero_byte_still_raises_by_name(tmp_path):
    with pytest.raises(StillCaptureError, match="is empty"):
        _grab(exported=True, write_png=True, png_bytes=b"", tmp_path=tmp_path)


def test_the_named_error_is_still_a_capture_error(tmp_path):
    """Existing `except CaptureError` handlers keep catching it."""
    assert issubclass(StillCaptureError, CaptureError)
    with pytest.raises(CaptureError):
        _grab(still=False, tmp_path=tmp_path)


def test_a_real_export_still_returns(tmp_path):
    result = _grab(tmp_path=tmp_path)
    assert result.path.is_file()
    assert result.path.stat().st_size > 0


# ── The ffmpeg helpers: zero bytes is failure, not a still ────────


def _run_writing_zero_bytes(cmd, **kwargs):
    Path(cmd[-1]).write_bytes(b"")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_ask_the_footage_rejects_a_zero_byte_still(tmp_path):
    from library.skills.ask_the_footage.skill import capture_still

    out = str(tmp_path / "still_00.png")
    with patch("subprocess.run", side_effect=_run_writing_zero_bytes):
        assert capture_still(str(tmp_path), 1.0, out) is False


def test_verify_treatment_skips_zero_byte_stills(tmp_path):
    from library.skills.verify_treatment.skill import capture_window_stills

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake-video")
    with patch("subprocess.run", side_effect=_run_writing_zero_bytes):
        assert capture_window_stills(
            str(source), 30.0, [0, 90], str(tmp_path)) == []


def test_thumbnail_extractor_rejects_a_zero_byte_thumbnail(tmp_path):
    from library.tools.thumbnail_extractor import extract_thumbnail

    out = str(tmp_path / "thumb.jpg")
    with patch("subprocess.run", side_effect=_run_writing_zero_bytes):
        assert extract_thumbnail(str(tmp_path), out) is False


def test_grade_extract_frame_rejects_a_zero_byte_frame(tmp_path):
    from library.steps.step_5_01_color_grade.grade import _extract_frame

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake-video")
    with patch("subprocess.run", side_effect=_run_writing_zero_bytes):
        assert _extract_frame(str(source)) == ""


def test_validate_output_extract_frame_rejects_a_zero_byte_frame(tmp_path):
    from library.steps.step_6_02_validate_output.bridge import _extract_frame

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake-video")
    with patch("subprocess.run", side_effect=_run_writing_zero_bytes):
        assert _extract_frame(
            str(source), 30, str(tmp_path / "frame.png")) is False


def test_vision_frame_cache_skips_a_zero_byte_frame(tmp_path):
    """`extract_frames` never hands an empty file to the vision pass,
    and does not leave it cached for the next run either."""
    from library.tools.analysis.vision_pipeline_v3 import extract_frames

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-video")
    with patch("subprocess.run", side_effect=_run_writing_zero_bytes):
        assert extract_frames(clip, 10.0, tmp_path, interval_s=5.0) == []
    leftovers = [p for p in (tmp_path / "clip" / "frames").iterdir()]
    assert leftovers == []


def _run_writing_a_frame(cmd, **kwargs):
    Path(cmd[-1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 64)
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_vision_frame_cache_returns_a_real_frame(tmp_path):
    from library.tools.analysis.vision_pipeline_v3 import extract_frames

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-video")
    with patch("subprocess.run", side_effect=_run_writing_a_frame):
        frames = extract_frames(clip, 10.0, tmp_path, interval_s=5.0)
    assert len(frames) == 3
    assert all(Path(f["path"]).stat().st_size > 0 for f in frames)


def test_thumbnail_cache_serves_no_zero_byte_file(tmp_path):
    """A poisoned dashboard cache entry reads as a miss, and is gone."""
    from library.tools.thumbnail_extractor import _cached_url

    (tmp_path / "a.jpg").write_bytes(b"")
    assert _cached_url(tmp_path, "a.jpg") == ""
    assert not (tmp_path / "a.jpg").exists()

    (tmp_path / "b.jpg").write_bytes(b"1" * 64)
    assert _cached_url(tmp_path, "b.jpg") == "/thumbnails/b.jpg"
