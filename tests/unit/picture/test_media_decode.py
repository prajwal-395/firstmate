"""The shared temporal decode must preserve each existing frame input."""

import shutil
import subprocess
from pathlib import Path

import pytest

from library.steps.step_1_04_temporal_index import step
from library.steps.step_1_04_temporal_index.media_decode import (
    decode_temporal_media,
)


def _ffmpeg_output(*args):
    return subprocess.run(
        ["ffmpeg", *args], capture_output=True, timeout=30, check=True,
    ).stdout


def test_shared_decode_keeps_legacy_video_representations(tmp_path):
    """Filter-graph fan-out cannot change pixels or sampled JPEGs."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required to compare decoded frame inputs")

    source = tmp_path / "source.mkv"
    _ffmpeg_output(
        "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=24:duration=2",
        "-an", "-c:v", "ffv1", "-y", str(source),
    )
    sample_w, sample_h = step.face_sample_dimensions(str(source))
    media = decode_temporal_media(
        str(source), sample_w, sample_h, duration=2.0)
    assert media is not None, "shared frame extraction should succeed"

    try:
        old_motion = _ffmpeg_output(
            "-i", str(source), "-vf", "fps=30,scale=160:90,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-v", "quiet", "-",
        )
        old_flow = _ffmpeg_output(
            "-i", str(source), "-vf", "fps=5,scale=160:90,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "-v", "quiet", "-",
        )
        old_face = _ffmpeg_output(
            "-i", str(source), "-vf",
            f"fps=5,scale={sample_w}:{sample_h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-v", "quiet", "-",
        )
        old_hue = _ffmpeg_output(
            "-i", str(source), "-vf", "fps=1,scale=80:45",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-v", "quiet", "-",
        )
        vision_dir = tmp_path / "vision"
        vision_dir.mkdir()
        _ffmpeg_output(
            "-i", str(source), "-vf",
            f"fps=5,scale={sample_w}:{sample_h}",
            "-f", "image2", "-q:v", "2",
            str(vision_dir / "f_%05d.jpg"),
        )

        assert old_motion == Path(media.motion_gray).read_bytes()
        assert old_flow == Path(media.flow_gray).read_bytes()
        assert old_face == Path(media.face_rgb).read_bytes()
        assert old_hue == Path(media.hue_rgb).read_bytes()
        old_jpegs = sorted(vision_dir.glob("*.jpg"))
        new_jpegs = [Path(path) for path in media.vision_jpegs]
        assert [path.read_bytes() for path in old_jpegs] == [
            path.read_bytes() for path in new_jpegs
        ]
        assert step.detect_scenes(str(source)) == step.detect_scenes(
            str(source), captured_stderr=media.scene_stderr)
    finally:
        media.close()
