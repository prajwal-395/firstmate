import json
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools.native_overlay_media import prepare_projection


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not FFMPEG or not FFPROBE,
                                reason="ffmpeg and ffprobe are required")


def _video(path: Path, pixels: bytes) -> None:
    raw = path.with_suffix(".rgba")
    raw.write_bytes(pixels)
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
         "-pix_fmt", "rgba", "-s", "16x16", "-r", "24", "-i", str(raw),
         "-frames:v", "1", "-c:v", "qtrle", "-pix_fmt", "argb", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8")


def _decode(path: Path) -> bytes:
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True)
    return result.stdout


def test_qtrle_overlay_is_cached_as_alpha_prores_and_matches_hand_transcode(tmp_path):
    # Half-transparent pixels expose both alpha and color-channel changes.
    pixels = bytes(channel for i in range(256)
                   for channel in (i % 256, (i * 3) % 256, 90, 0 if i % 2 else 128))
    source = tmp_path / "overlay.mov"
    _video(source, pixels)
    projection = {
        "imports": [{"asset_id": "caption", "uri": source.name}],
        "placements": [{"asset_id": "caption", "role": "subtitle_overlay"}],
    }
    prepared, first = prepare_projection(projection, tmp_path, tmp_path / "cache")
    cached = Path(prepared["imports"][0]["uri"])
    assert first[0]["cache_hit"] is False
    assert first[0]["transcode_seconds"] > 0

    manual = tmp_path / "hand.mov"
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-map", "0:v:0", "-an", "-sn", "-dn", "-c:v", "prores_ks",
         "-profile:v", "4", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16",
         str(manual)], check=True, capture_output=True, text=True, encoding="utf-8")
    cached_rgba, hand_rgba = _decode(cached), _decode(manual)
    assert cached_rgba == hand_rgba
    assert cached_rgba[3::4] == hand_rgba[3::4]

    again, second = prepare_projection(projection, tmp_path, tmp_path / "cache")
    assert again["imports"][0]["uri"] == str(cached)
    assert second[0]["cache_hit"] is True
    assert second[0]["transcode_seconds"] == 0


def test_only_used_compositing_qtrle_assets_are_rewritten(tmp_path):
    source = tmp_path / "overlay.mov"
    _video(source, bytes((20, 40, 60, 128)) * 256)
    projection = {
        "imports": [{"asset_id": "caption", "uri": source.name}],
        "placements": [{"asset_id": "caption", "role": "end_card"}],
    }
    prepared, records = prepare_projection(projection, tmp_path, tmp_path / "cache")
    assert prepared == projection
    assert records == []
