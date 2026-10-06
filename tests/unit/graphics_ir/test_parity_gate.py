"""The parity gate: FFrames against Remotion on a fixed StagedScene declaration.

This test renders pixels through both engines and compares them. It is
expensive - two full renders of a short declaration - and exists to catch
the class of error the bakeoff found: a wrong font weight, a wrong camera
sign, a gradient that is not a gradient. Without this gate, those errors
go unnoticed because each engine is only ever compared to itself.

The fixed declaration is kept short (a few seconds of frames) to keep the
test runnable in CI. The thresholds are the ones the report measured:
mean pct8 under 1%, worst frame under 5%, motion-on-twos counts equal.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from library.graphics_ir.lowering import lower_staged_scene
from library.graphics_ir.serialization import serialize
from library.tools import fframes_render

_PILOT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_STAGED_SCENE = {
    "ground": {
        "colour": "#181610",
        "vignette": {"strength": 0.5, "radiusX": 0.8, "radiusY": 0.55},
    },
    "camera": {
        "zoom": [{"at": 0, "value": 1.0}, {"at": 2.0, "value": 1.05, "ease": "inOut"}],
        "x": [{"at": 0, "value": 0.0}, {"at": 2.0, "value": 0.02, "ease": "inOut"}],
        "y": [{"at": 0, "value": -0.05}, {"at": 2.0, "value": 0.0, "ease": "inOut"}],
        "roll": [{"at": 0, "value": 0.0}, {"at": 2.0, "value": -1.0, "ease": "inOut"}],
        "focus": [{"at": 0, "value": 0.0}, {"at": 2.0, "value": 0.0}],
    },
    "layers": [
        {"kind": "disc", "id": "pool", "x": 0.02, "y": -0.3, "size": 0.95,
         "opacity": [{"at": 0, "value": 0.0}, {"at": 1.0, "value": 0.9, "ease": "out"}],
         "colour": "#3A3426", "blur": 0.06},
        {"kind": "rule", "id": "rule", "x": 0.0, "y": 0.02, "length": 1.6,
         "thickness": 0.003, "opacity": 0.9, "colour": "#4A4436"},
        {"kind": "text", "id": "lead", "align": "left", "revealRamp": 0.067,
         "placement": {"x": 0.02, "y": 0.13, "width": 0.78},
         "runs": [
             {"text": "he plays ", "size": 0.05, "weight": 500, "colour": "#8A8578", "revealAt": 0.5},
             {"text": "with his", "size": 0.05, "weight": 500, "colour": "#8A8578", "revealAt": 0.7},
             {"text": "mind", "size": 0.16, "weight": 900, "colour": "#E8E4D8", "revealAt": 1.0,
              "letterSpacing": -0.003, "newLine": True},
         ]},
        {"kind": "text", "id": "label", "space": "frame", "align": "center",
         "placement": {"x": 0.0, "y": -0.74, "width": 0.8},
         "runs": [{"text": "FIG. 3 / THE FIELD", "size": 0.022, "weight": 600,
                   "colour": "#6B6658", "letterSpacing": 0.004}]},
    ],
    "fontFamily": "Montserrat",
    "holdFrames": 2,
    "fps": 30,
    "width": 1080,
    "height": 1920,
    "durationInFrames": 90,
}


def _fframes_available() -> bool:
    try:
        usable, _ = fframes_render.fframes_available()
        return bool(usable)
    except Exception:
        return False


def _remotion_available() -> bool:
    # Probed where the render runs (remotion-subtitles/): npx resolves
    # the local binary there and nowhere else. `versions` needs no entry
    # point; `--version` is not a remotion command and exits 1.
    npx = shutil.which("npx")
    if not npx:
        return False
    try:
        done = subprocess.run(
            [npx, "--no-install", "remotion", "versions"],
            capture_output=True, text=True, timeout=60,
            cwd=str(_PILOT_ROOT / "remotion-subtitles"),
        )
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _fframes_available(), reason="FFrames binary not available")
@pytest.mark.skipif(not _remotion_available(), reason="Remotion not available")
def test_fframes_matches_remotion_on_a_fixed_staged_scene(tmp_path):
    """FFrames and Remotion draw the same declaration to within 1% mean pct8.

    This is the parity gate. It renders a short StagedScene declaration
    through both engines and compares pixels. The thresholds are the ones
    the report measured: mean pct8 under 1%, worst frame under 5%,
    motion-on-twos counts equal.
    """
    scene = lower_staged_scene(_STAGED_SCENE)
    fonts_dir = tmp_path / "fonts"
    fframes_render.stage_scene_fonts(scene, str(fonts_dir))
    scene_json = tmp_path / "scene.json"
    scene_json.write_text(json.dumps(json.loads(serialize(scene))), encoding="utf-8")

    fframes_dir = tmp_path / "fframes"
    fframes_dir.mkdir()
    fframes_frames = fframes_render.render_png_sequence(
        str(scene_json), str(fframes_dir), scene.fps)

    remotion_dir = tmp_path / "remotion"
    remotion_dir.mkdir()
    _render_remotion(tmp_path, str(remotion_dir))
    remotion_frames = sorted(remotion_dir.glob("*.png"))

    assert len(fframes_frames) == len(remotion_frames), (
        f"frame count mismatch: FFrames {len(fframes_frames)} vs Remotion {len(remotion_frames)}")

    pct8_values = []
    for ff_path, rm_path in zip(fframes_frames, remotion_frames):
        ff = np.asarray(Image.open(ff_path).convert("RGB")).astype(np.float32)
        rm = np.asarray(Image.open(rm_path).convert("RGB")).astype(np.float32)
        diff = np.abs(ff - rm)
        pct8 = float((diff.max(-1) > 8).mean() * 100)
        pct8_values.append(pct8)

    mean_pct8 = float(np.mean(pct8_values))
    worst_pct8 = float(np.max(pct8_values))

    assert mean_pct8 < 1.0, f"mean pct8 {mean_pct8:.2f}% exceeds 1% threshold"
    assert worst_pct8 < 5.0, f"worst frame pct8 {worst_pct8:.2f}% exceeds 5% threshold"

    fframes_twos = _identical_pairs(fframes_frames)
    remotion_twos = _identical_pairs(remotion_frames)
    assert fframes_twos == remotion_twos, (
        f"motion-on-twos mismatch: FFrames {fframes_twos} vs Remotion {remotion_twos}")


def _identical_pairs(frames) -> int:
    """Consecutive pairs drawn identically, the reference doc's own metric.

    Each frame is read as luma at 180x320; a pair counts when its mean abs
    diff is under 0.0008. Ported from the deep-dive lab's twos.py, which
    measured 283 such pairs (63.0%) on the full 450-frame scene, frame-exact
    across all three engines. The gate's short declaration holds every
    second frame, so most pairs must agree exactly between engines.
    """
    previous = None
    identical = 0
    for path in frames:
        frame = np.asarray(Image.open(path).convert("L").resize((180, 320))).astype(
            np.float32) / 255
        if previous is not None and float(np.abs(frame - previous).mean()) < 0.0008:
            identical += 1
        previous = frame
    return identical


def _render_remotion(tmp_path: Path, output_dir: str) -> None:
    """Render the StagedScene declaration through Remotion."""
    props_path = tmp_path / "props.json"
    props_path.write_text(json.dumps(_STAGED_SCENE), encoding="utf-8")
    remotion_root = _PILOT_ROOT / "remotion-subtitles"
    command = [
        "npx", "--yes", "remotion", "render",
        "src/index.ts", "StagedScene", output_dir,
        "--sequence", "--image-format=png", "--timeout=120000",
        f"--props={props_path}",
    ]
    done = subprocess.run(
        command, cwd=str(remotion_root),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    assert done.returncode == 0, f"Remotion render failed: {done.stderr[-600:]}"
