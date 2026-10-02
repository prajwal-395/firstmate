"""A capture that did not happen raises by name - never a usable value.

Every failure shape of the `GrabStill` + `ExportStills` route raises
`StillCaptureError` (a `CaptureError`, so existing handlers still catch
it), and the ffmpeg capture helpers treat a zero-byte file as failure,
not a still. Incident (the 599,583-pixel grade that was really 486):
docs/evidence/marker_capture.md and `marker_capture`'s "WHEN THE ROUTE
FAILS".
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


# ── The failure shapes ────────────────────────────────────────────


def test_every_capture_that_did_not_happen_raises_by_name(tmp_path):
    """`GrabStill` declining; `ExportStills` returning False (the
    2026-09-10 shape) - authoritative even when a file sits beside it,
    because that file is not this call's; True with no file; and a
    zero-byte file."""
    assert issubclass(StillCaptureError, CaptureError)
    rows = [
        (dict(still=False), "declined to grab"),
        (dict(exported=False, write_png=False), "ExportStills returned False"),
        (dict(exported=False, write_png=True), "ExportStills returned False"),
        (dict(exported=True, write_png=False), "wrote no png"),
        (dict(exported=True, write_png=True, png_bytes=b""), "is empty"),
    ]
    for n, (shape, fragment) in enumerate(rows):
        folder = tmp_path / str(n)
        folder.mkdir()
        with pytest.raises(StillCaptureError, match=fragment):
            _grab(tmp_path=folder, **shape)


# ── The ffmpeg helpers: zero bytes is failure, not a still ────────


def _run_writing_zero_bytes(cmd, **kwargs):
    Path(cmd[-1]).write_bytes(b"")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_every_ffmpeg_capture_helper_rejects_a_zero_byte_still(tmp_path):
    from library.skills.ask_the_footage.skill import capture_still
    from library.skills.verify_treatment.skill import capture_window_stills
    from library.steps.step_5_01_color_grade.grade import (
        _extract_frame as grade_extract_frame,
    )
    from library.steps.step_6_02_validate_output.bridge import (
        _extract_frame as validate_extract_frame,
    )
    from library.tools.analysis.vision_pipeline_v3 import extract_frames

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake-video")
    with patch("subprocess.run", side_effect=_run_writing_zero_bytes):
        assert capture_still(
            str(tmp_path), 1.0, str(tmp_path / "still_00.png")) is False
        assert capture_window_stills(
            str(source), 30.0, [0, 90], str(tmp_path)) == []
        assert grade_extract_frame(str(source)) == ""
        assert validate_extract_frame(
            str(source), 30, str(tmp_path / "frame.png")) is False
        # The vision pass is never handed an empty file, and does not
        # leave it cached for the next run either.
        assert extract_frames(source, 10.0, tmp_path, interval_s=5.0) == []
    assert list((tmp_path / "clip" / "frames").iterdir()) == []
