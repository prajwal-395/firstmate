"""Lane B (audit G3) proof: projected titles render inside the safe area.

For each producer (a ``timed_text_overlay`` moment and a text-bearing
motion-graphics entry) this drives the real pinned EffectCraft CLI end
to end: project the titles document into driver ops, execute them,
read the opacity keys back with the CLI's own ``get`` (proving the
timing holds through the pinned build, not just in the request JSON),
render PNGs, and measure the ink - present, inside the safe-area rect,
and absent at the corners (a transparent overlay, not a full frame).

It SKIPS naming the missing piece when the CLI, ffmpeg, or the
composition's own face (Montserrat) is absent from the engine's font
list. Run renders through the verify slots (see the launch brief),
never beside a pipeline.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_model

np = pytest.importorskip("numpy")

from library.tools import project_model_effectcraft_titles as titles
from library.tools.project_model_effectcraft import CompSpec
from library.tools.safe_area import resolve_safe_area

PINNED_REV = "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
WIDTH, HEIGHT, FPS, DURATION = 540, 960, 30.0, 0.5
FRAMES = int(round(DURATION * FPS))


def _cli():
    override = os.environ.get("REN_EFFECTCRAFT_CLI")
    if override and Path(override).is_file():
        return str(override)
    cached = (Path.home() / ".cache/ren/effectcraft" / PINNED_REV
              / "source/target/release/effectcraft-cli")
    if cached.is_file():
        return str(cached)
    pytest.skip(f"pinned EffectCraft CLI is absent ({cached})")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    cli = _cli()
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    work = Path(str(tmp_path_factory.mktemp("ec-titles-proof")))
    return {"cli": cli, "work": work}


def _run(engine, *args):
    proc = subprocess.run([engine["cli"], *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=300, check=False)
    assert proc.returncode == 0, proc.stderr[:800] + proc.stdout[:800]
    return proc.stdout


def _engine_has_montserrat(engine):
    out = _run(engine, "exec", "text.fonts", "--params", "{}",
               "--json")
    families = json.loads(out)
    names = [f.get("family") for f in families.get("families", [])]
    if titles.MOTION_FONT_FAMILY not in names:
        pytest.skip(f"engine has no {titles.MOTION_FONT_FAMILY!r} face; "
                    f"a substitute render would prove the wrong typeface")


def _op_to_command(op):
    if op["op"] == "new_comp":
        return ("comp.new", {"name": op["name"], "width": op["width"],
                             "height": op["height"],
                             "frameRate": op["frame_rate"],
                             "duration": op["duration"]})
    if op["op"] == "add_text":
        params = {"name": op["name"], "text": op["text"],
                  "size": op["size"], "fill": op["fill"],
                  "position": op["position"]}
        if op.get("font") is not None:
            params["font"] = op["font"]
        return ("layer.newText", params)
    if op["op"] == "keyframe":
        return ("prop.addKey", {"layer": op["layer"], "path": op["path"],
                                "time": op["time"], "value": op["value"]})
    raise AssertionError(f"titles projection uses no {op['op']!r} op")


def _execute(engine, name, operations):
    work = engine["work"] / name
    work.mkdir(parents=True, exist_ok=True)
    batch = []
    for op in operations:
        command, params = _op_to_command(op)
        batch.extend([command, json.dumps(params)])
    project = work / f"{name}.ecproj"
    if project.exists():
        project.unlink()
    out = _run(engine, "run", *batch, "--empty", "--save-as",
               str(project), "--json")
    assert json.loads(out)["saved"] == str(project)
    pattern = work / f"{name}_#####.png"
    _run(engine, "render", "--project", str(project), "--comp",
         "Main", "--out", str(pattern), "--format", "png",
         "--start", "0", "--end", repr(DURATION), "--channels", "rgba",
         "--json")
    frames = sorted(work.glob(f"{name}_*.png"))
    assert len(frames) == FRAMES, [p.name for p in work.iterdir()]
    return project, frames


def _read_png(path):
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr[:300]
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(
        HEIGHT, WIDTH, 4)


def _timed_document():
    return {"timed_text": {
        "fontFamily": "Montserrat",
        "moments": [{
            "text": "Night 1",
            "color": "#D4A34A",
            "fontSize": 32,
            "startFrame": 0,
            "durationFrames": FRAMES,
            "x": 0.5,
            "y": 0.5,
            "fadeInFrames": 0,
            "fadeOutFrames": 0,
            "fontWeight": 400,
            "textShadow": "none",
            "textAlign": "center",
        }],
    }}


def _motion_document():
    return {"motion_moments": [{
        "element": "title_lockup",
        "anchor": "top_centre",
        "row": 0,
        "runs": [{"text": "THE TITLE", "type_role": "display",
                  "uppercase": False}],
        "color": "#FFFFFF",
        "colorBasis": "stated by the plan",
        "entrance": "cut",
        "exit": "cut",
        "timeline_start": 0.0,
        "timeline_end": DURATION,
        "startFrame": 0,
        "durationFrames": FRAMES,
        "footprint": 0.5,
        "data": {},
    }]}


def _projection(document):
    comp = CompSpec(name="Main", width=WIDTH, height=HEIGHT,
                    frame_rate=FPS, duration_seconds=DURATION)
    safe = resolve_safe_area(width=WIDTH, height=HEIGHT)
    projection = titles.project_titles(document, comp, FPS, safe)
    return {"operations": [{
        "op": "new_comp",
        "name": comp.name,
        "width": comp.width,
        "height": comp.height,
        "frame_rate": comp.frame_rate,
        "duration": comp.duration_seconds,
    }] + projection.operations}


def _ink_rect(img):
    ink = img[..., 3] > 0
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    return ink, rows, cols


def _assert_title_frame(engine, name, document):
    _engine_has_montserrat(engine)
    request = _projection(document)
    project, frames = _execute(engine, name, request["operations"])
    mid = _read_png(frames[len(frames) // 2])
    ink, rows, cols = _ink_rect(mid)
    assert int(ink.sum()) > 500, \
        f"{name}: no readable copy rendered ({int(ink.sum())} ink pixels)"
    safe = resolve_safe_area(width=WIDTH, height=HEIGHT)
    top, bottom = int(rows.min()), int(rows.max())
    left, right = int(cols.min()), int(cols.max())
    assert top >= safe.top, (name, top, safe.top)
    assert bottom <= HEIGHT - safe.bottom, (name, bottom, safe.bottom)
    assert left >= safe.left, (name, left, safe.left)
    assert right <= WIDTH - safe.right, (name, right, safe.right)
    corners = [mid[0, 0], mid[0, WIDTH - 1],
               mid[HEIGHT - 1, 0], mid[HEIGHT - 1, WIDTH - 1]]
    assert all(int(px[3]) == 0 for px in corners), \
        f"{name}: overlay corners are not transparent"
    return project


def test_timed_text_card_renders_inside_the_safe_area(engine):
    _assert_title_frame(engine, "timed-card", _timed_document())


def test_motion_title_renders_inside_the_safe_area(engine):
    _assert_title_frame(engine, "motion-title", _motion_document())


def test_projected_opacity_keys_hold_through_the_engine(engine):
    _engine_has_montserrat(engine)
    request = _projection(_timed_document())
    project, _ = _execute(engine, "timed-keys", request["operations"])
    out = _run(engine, "get", "--project", str(project), "Main",
               "timed-text-0", "transform/opacity", "--time", "0.25",
               "--json")
    assert json.loads(out)["value"] == pytest.approx(100.0)
