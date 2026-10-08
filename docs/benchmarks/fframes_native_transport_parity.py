"""Exact raw-frame parity checks for legacy and native FFrames adaptation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docs/fframes-evidence/phase2-side-by-side/repro"))

from props import props

from library.tools import fframes_render


def _decode(path: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
         "-pix_fmt", "rgba", "-"],
        check=True, capture_output=True).stdout


def main() -> None:
    binary = str(ROOT / "native/target/release/ren-graphics-fframes")
    results = []
    with tempfile.TemporaryDirectory(prefix="ff-parity-") as temp:
        overlay_scene_dir = os.path.join(temp, "overlay-scene")
        os.makedirs(overlay_scene_dir)
        overlay_scene = fframes_render._build_scene(
            "SubtitleOverlay", props("SubtitleOverlay"), overlay_scene_dir, "")

        # Reproduce the old PNG -> Python premultiply -> qtrle path.
        png_dir = os.path.join(temp, "png")
        frames = fframes_render.render_png_sequence(overlay_scene, png_dir, 30)
        png_bytes_before = sum(Path(frame).stat().st_size for frame in frames)
        fframes_render.premultiply_frames(frames)
        png_bytes_after = sum(Path(frame).stat().st_size for frame in frames)
        old_overlay = os.path.join(temp, "old-overlay.mov")
        fframes_render._encode_overlay(frames, old_overlay, 30, 300)
        new_overlay = os.path.join(temp, "new-overlay.mov")
        fframes_render.render_one_card(
            "SubtitleOverlay", props("SubtitleOverlay"), new_overlay, temp)
        old_bytes, new_bytes = _decode(old_overlay), _decode(new_overlay)
        assert old_bytes == new_bytes, "qtrle decoded RGBA differs from legacy path"
        rgba = np.frombuffer(new_bytes, dtype=np.uint8).reshape(-1, 1920, 1080, 4)
        assert np.any(rgba[..., 3] < 255), "overlay alpha was lost"
        assert np.all(rgba[..., :3] <= rgba[..., 3:4]), "overlay RGB is not premultiplied"
        results.append({
            "path": "overlay",
            "frames": len(new_bytes) // (1080 * 1920 * 4),
            "decoded_rgba_matches_legacy": True,
            "alpha_preserved": True,
            "legacy_png_frame_count": len(frames),
            "legacy_png_bytes_before_premultiply": png_bytes_before,
            "legacy_png_bytes_after_premultiply": png_bytes_after,
            "native_intermediate_frame_files": 0,
        })

        opaque_dir = os.path.join(temp, "opaque-scene")
        os.makedirs(opaque_dir)
        opaque_scene = fframes_render._build_scene(
            "FullFrameCard", props("FullFrameCard"), opaque_dir, "")
        raw = subprocess.run(
            [binary, "render", opaque_scene, "--raw"],
            check=True, capture_output=True).stdout[8:]
        flattened = subprocess.run(
            [binary, "render", opaque_scene, "--raw", "--background", "#1A1A2E"],
            check=True, capture_output=True).stdout[8:]
        straight = np.frombuffer(raw, dtype=np.uint8).reshape(
            -1, 1920, 1080, 4).astype(np.uint16)
        ground = np.array([0x1A, 0x1A, 0x2E], dtype=np.uint16).reshape(1, 1, 1, 3)
        rgb = (straight[..., :3] * straight[..., 3:4]
               + ground * (255 - straight[..., 3:4]) + 127) // 255
        expected = np.concatenate([
            rgb.astype(np.uint8),
            np.full(straight.shape[:3] + (1,), 255, dtype=np.uint8),
        ], axis=3).tobytes()
        assert flattened == expected, "native flatten differs from Python reference"
        results.append({
            "path": "opaque",
            "frames": len(flattened) // (1080 * 1920 * 4),
            "raw_rgba_matches_python_flatten": True,
            "alpha_opaque": True,
        })
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
