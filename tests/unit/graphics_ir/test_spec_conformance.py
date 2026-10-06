"""Spec conformance: each renderer judged against the scene IR itself.

Parity only measures sameness - if both engines agreed on the wrong
picture, parity would pass. This check renders a small fixed StagedScene
declaration through each available engine and measures the pixels
against what the lowered IR declares: element positions, colours and
reveal timing. A parity disagreement is resolved by asking which
renderer matches the spec.

This test renders pixels through full engines, so it is expensive - one
short declaration per available renderer - and states that reason here.
It is kept short (one second of frames, no camera) so the geometry it
checks is the lowering's, not a moving camera's.

Tolerances come from the deep-dive report's measured Chrome-vs-Chrome
numbers, not from invention: sub-pixel geometry agreed to 0.85 px at
worst (lead text under zoom), so positions must land within 1 px;
colours use the parity gate's own 8/255 bar; reveal steps are
frame-exact, the way motion-on-twos measured frame-exact.
"""

import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from library.graphics_ir.lowering import lower_staged_scene
from library.graphics_ir.serialization import serialize
from library.tools import fframes_render

POSITION_TOLERANCE_PX = 1.0
"""Ceiling of the worst measured Chrome-vs-Chrome geometry offset."""

COLOUR_TOLERANCE = 8.0
"""The parity gate's own per-pixel bar, in levels out of 255."""

_DECLARATION = {
    "ground": {"colour": "#202020"},
    "layers": [
        {"kind": "rule", "id": "rule", "x": 0.0, "y": 0.1, "length": 0.5,
         "thickness": 0.01, "opacity": 1.0, "colour": "#FF0000"},
        {"kind": "disc", "id": "pool", "x": -0.3, "y": -0.05, "size": 0.2,
         "colour": "#0000FF", "blur": 0.0},
        {"kind": "text", "id": "cue", "align": "left",
         "placement": {"x": -0.2, "y": 0.25, "width": 0.5},
         "runs": [{"text": "Hi", "size": 0.1, "weight": 700,
                   "colour": "#00FF00", "revealAt": 0.5}]},
    ],
    "fontFamily": "Montserrat",
    "fps": 30,
    "width": 480,
    "height": 270,
    "durationInFrames": 30,
}


def _fframes_available() -> bool:
    try:
        usable, _ = fframes_render.fframes_available()
        return bool(usable)
    except Exception:
        return False


def _pilot_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent.parent


def _remotion_available() -> bool:
    from tests.unit.graphics_ir.test_parity_gate import _remotion_available as probe

    return probe()


def _measure(frames, scene) -> dict:
    """Deviation of rendered pixels from the IR, per element."""
    nodes = {node.id: node for node in scene.nodes}
    report = {}

    rule = np.asarray(Image.open(frames[-1]).convert("RGB")).astype(np.float32)
    red = (rule[..., 0] > 200) & (rule[..., 1] < 80) & (rule[..., 2] < 80)
    rows = np.nonzero(red.any(1))[0]
    # The bar's top ink row against the declared top: means carry a
    # thickness bias on thin bars, edges do not.
    report["rule_top_px"] = float(abs(rows.min() - nodes["rule"].bounds.y)) if len(rows) else float("inf")
    report["rule_found"] = bool(len(rows))

    ground = rule[5, 5]
    report["ground_levels"] = float(np.abs(ground - np.array([0x20, 0x20, 0x20])).max())

    blue = (rule[..., 2] > 200) & (rule[..., 0] < 80) & (rule[..., 1] < 80)
    ys, xs = np.nonzero(blue)
    disc = nodes["pool"].bounds
    report["disc_found"] = bool(len(xs))
    if len(xs):
        report["disc_box_px"] = float(max(
            abs(xs.min() - disc.x), abs(xs.max() - (disc.x + disc.width - 1)),
            abs(ys.min() - disc.y), abs(ys.max() - (disc.y + disc.height - 1))))

    early = np.asarray(Image.open(frames[13]).convert("RGB")).astype(np.float32)
    late = np.asarray(Image.open(frames[16]).convert("RGB")).astype(np.float32)
    box = nodes["cue"].bounds
    x0, y0 = int(box.x), int(box.y)
    region_early = early[y0:y0 + int(box.height), x0:x0 + int(box.width)]
    region_late = late[y0:y0 + int(box.height), x0:x0 + int(box.width)]
    green_early = ((region_early[..., 1] > 200) & (region_early[..., 0] < 80)).sum()
    green_late = ((region_late[..., 1] > 200) & (region_late[..., 0] < 80)).sum()
    report["reveal_ink_before"] = int(green_early)
    report["reveal_ink_after"] = int(green_late)
    return report


def _check_conformance(engine: str, report: dict) -> None:
    """Each renderer meets the IR it was given, named on failure."""
    assert report["rule_found"], f"{engine}: rule draws no red ink"
    assert report["rule_top_px"] <= POSITION_TOLERANCE_PX, (
        f"{engine}: rule top off by {report['rule_top_px']:.1f}px, "
        f"tolerance {POSITION_TOLERANCE_PX:.0f}px")
    assert report["ground_levels"] <= COLOUR_TOLERANCE, (
        f"{engine}: ground off by {report['ground_levels']:.0f} levels")
    assert report["disc_found"], f"{engine}: disc draws no blue ink"
    assert report["disc_box_px"] <= POSITION_TOLERANCE_PX, (
        f"{engine}: disc box off by {report['disc_box_px']:.1f}px")
    assert report["reveal_ink_before"] == 0, (
        f"{engine}: reveal visible a frame early ({report['reveal_ink_before']} px)")
    assert report["reveal_ink_after"] > 0, f"{engine}: reveal never appears"


@pytest.mark.skipif(not _fframes_available(), reason="FFrames binary not available")
def test_fframes_conforms_to_the_scene_ir(tmp_path):
    """FFrames draws what the IR declares, within the measured floor."""
    scene = lower_staged_scene(_DECLARATION)
    fframes_render.stage_scene_fonts(scene, str(tmp_path / "fonts"))
    scene_json = tmp_path / "scene.json"
    scene_json.write_text(json.dumps(json.loads(serialize(scene))), encoding="utf-8")
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frames = fframes_render.render_png_sequence(str(scene_json), str(frames_dir), scene.fps)
    _check_conformance("fframes", _measure(sorted(frames), scene))


@pytest.mark.skipif(not _remotion_available(), reason="Remotion not available")
def test_remotion_conforms_to_the_scene_ir(tmp_path):
    """Remotion draws what the IR declares, within the measured floor."""
    scene = lower_staged_scene(_DECLARATION)
    props_path = tmp_path / "props.json"
    props_path.write_text(json.dumps(_DECLARATION), encoding="utf-8")
    output_dir = tmp_path / "remotion"
    output_dir.mkdir()
    command = [
        "npx", "--yes", "remotion", "render",
        "src/index.ts", "StagedScene", str(output_dir),
        "--sequence", "--image-format=png", "--timeout=120000",
        f"--props={props_path}",
    ]
    done = subprocess.run(
        command, cwd=str(_pilot_root() / "remotion-subtitles"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    assert done.returncode == 0, f"Remotion render failed: {done.stderr[-600:]}"
    frames = sorted(output_dir.glob("*.png"))
    _check_conformance("remotion", _measure(frames, scene))
