"""The product-format lane's replacement media: provisioned, not borrowed.

The lane used to fall back to a media file inside an operator's home
directory when the operator named none, which put a machine-specific
path in the checkout - and the fallback was the defect, not the path:
the checkout depended on a file it could not carry.

These tests hold the replacement where it belongs: the run PROVISIONS
the file it owns when the operator names none, and it COPIES the file
an operator names into the same owned place.  Neither path reads
anything the checkout does not carry.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.live import product_format_qualification as lane

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are the tools the provisioned "
           "replacement is rendered and measured with")


def _probe(path: Path) -> dict:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=width,height,r_frame_rate,codec_type",
         "-of", "json", str(path)],
        capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout or "{}")


def _own_in(tmp_path: Path, monkeypatch) -> Path:
    """The lane's owned place, pointed at this test's directory."""
    owned_dir = tmp_path / "owned"
    monkeypatch.setattr(lane, "OWNED_DIR", owned_dir)
    monkeypatch.setattr(lane, "OWNED_REPLACEMENT",
                        owned_dir / "vep-qual-owned-replacement.mov")
    return owned_dir


def test_the_replacement_is_provisioned_when_no_source_is_named(
        tmp_path, monkeypatch):
    """No operator file, no outside file: the run renders its own."""
    _own_in(tmp_path, monkeypatch)
    owned = Path(lane._owned_media(""))
    assert owned.is_file(), owned
    assert owned.stat().st_size > 0
    streams = _probe(owned).get("streams") or []
    video = next(row for row in streams if row.get("codec_type") == "video")
    audio = [row for row in streams if row.get("codec_type") == "audio"]
    # The PRODUCT format: the reel this lane measures is portrait, and
    # a replacement in another shape would measure the re-point, not
    # the product.
    assert (video.get("width"), video.get("height")) == (
        lane.REPLACEMENT_WIDTH, lane.REPLACEMENT_HEIGHT)
    assert video.get("r_frame_rate") == lane.REPLACEMENT_FPS
    # A replacement the reel cannot hear is a mute, so the clip carries
    # an audio stream.
    assert audio


def test_a_named_source_is_copied_into_the_owned_place(tmp_path,
                                                       monkeypatch):
    """The operator's file is copied, and the operator's file is left."""
    _own_in(tmp_path, monkeypatch)
    source = tmp_path / "operator-source.mov"
    source.write_bytes(b"the operator's media")
    owned = Path(lane._owned_media(str(source)))
    assert owned.read_bytes() == b"the operator's media"
    assert source.is_file(), "the operator's own file is never moved"
    assert owned != source


def test_the_lane_names_no_path_outside_the_checkout():
    """The defect was the DEPENDENCY, so no module constant is one."""
    for name, value in vars(lane).items():
        if not name.isupper() or not isinstance(value, str):
            continue
        assert not value.startswith(("/Users/", "/home/")), (name, value)
