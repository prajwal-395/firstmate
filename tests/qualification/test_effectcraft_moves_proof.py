"""Unit 7c proof: moves render through the pinned EffectCraft CLI.

For each checked-in move request this drives the real engine end to
end: execute the projection, read every frame's transform back with
the CLI's own ``get`` (proving the bake holds through the pinned
build, not just in the request JSON), and render PNGs proving the
picture follows the keys - each move frame is pixel-exact against a
static single-value reference render at the same sampled value, and
the shake's rendered centroid shift equals its projected offset.

Tolerances: get-back values equal the fixture keys within 1e-9 (f64
round-trip); reference pixels are bit-exact (max abs diff 0), which
is what the engine's deterministic CPU sampler gives for identical
sampled values. A Resolve pixel reference is not needed: the values
are Ren's recorded Fusion parameters baked per frame, and the engine
provably renders what the keys say.

Heavy: shells the pinned CLI and renders. It SKIPS naming the
missing piece when the CLI or ffmpeg is absent. Run renders through
the verify slots (see the launch brief), never beside a pipeline.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_model

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/effectcraft_fframes_conformance"
CHECKER = ROOT / "native/ren-effectcraft/tests/fixtures/checker.bmp"

PINNED_REV = "549e9d825132986d6167c1432cd3328efcbb0278"
WIDTH, HEIGHT, FPS = 96, 64, 10.0

MOVES = ["drift", "punch", "shake", "cut"]


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
    if not CHECKER.is_file():
        pytest.skip(f"fixture footage is absent ({CHECKER})")
    work = Path(str(tmp_path_factory.mktemp("ec-moves-proof")))
    return {"cli": cli, "work": work}


def _run(engine, *args):
    proc = subprocess.run([engine["cli"], *args], capture_output=True,
                          text=True, timeout=300, check=False)
    assert proc.returncode == 0, proc.stderr[:800] + proc.stdout[:800]
    return proc.stdout


def _request(name):
    return json.loads((FIXTURES / f"request-move-{name}.json")
                      .read_text(encoding="utf-8"))


def _op_to_command(op, checker):
    if op["op"] == "new_comp":
        return ("comp.new", {"name": op["name"], "width": op["width"],
                             "height": op["height"],
                             "frameRate": op["frame_rate"],
                             "duration": op["duration"]})
    if op["op"] == "import_footage":
        return ("file.import", {"paths": [checker]})
    if op["op"] == "add_footage_layer":
        params = {"item": op["footage"]}
        if "time" in op:
            params["time"] = op["time"]
        return ("layer.addItem", params)
    if op["op"] == "keyframe":
        return ("prop.addKey", {"layer": op["layer"],
                                "path": op["path"],
                                "time": op["time"],
                                "value": op["value"]})
    raise AssertionError(f"move fixtures use no {op['op']!r} op")


def _execute(engine, name):
    """Run the checked-in request through the pinned CLI; return paths."""
    request = _request(name)
    work = engine["work"] / name
    work.mkdir(parents=True, exist_ok=True)
    batch = []
    for op in request["operations"]:
        command, params = _op_to_command(op, str(CHECKER))
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
         "--start", "0", "--end", "0.5", "--channels", "rgba",
         "--json")
    frames = sorted(work.glob(f"{name}_*.png"))
    assert len(frames) == 5, [p.name for p in work.iterdir()]
    return project, frames


def _read_png(path):
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgba", "-"], capture_output=True, timeout=120,
                          check=False)
    assert proc.returncode == 0, proc.stderr[:300]
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(
        HEIGHT, WIDTH, 4)


def _get(engine, project, path, time):
    out = _run(engine, "get", "--project", str(project), "Main",
               "checker.bmp", path, "--time", repr(time), "--json")
    return json.loads(out)["value"]


def _static_render(engine, name, path, value):
    """One single-key project holding ``value`` everywhere it renders."""
    work = engine["work"] / f"{name}-static"
    work.mkdir(parents=True, exist_ok=True)
    project = work / "static.ecproj"
    if project.exists():
        project.unlink()
    _run(engine, "run", "comp.new",
         json.dumps({"name": "Main", "width": WIDTH, "height": HEIGHT,
                     "frameRate": FPS, "duration": 0.5}),
         "file.import", json.dumps({"paths": [str(CHECKER)]}),
         "layer.addItem", json.dumps({"item": CHECKER.name}),
         "prop.addKey", json.dumps({"layer": CHECKER.name,
                                    "path": path, "time": 0.0,
                                    "value": value}),
         "--empty", "--save-as", str(project), "--json")
    pattern = work / "static_#####.png"
    _run(engine, "render", "--project", str(project), "--comp",
         "Main", "--out", str(pattern), "--format", "png",
         "--start", "0", "--end", "0.5", "--channels", "rgba",
         "--json")
    return _read_png(work / "static_00000.png")


def _max_abs(a, b):
    return int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max())


@pytest.mark.parametrize("name", MOVES)
def test_engine_holds_the_baked_curve_frame_by_frame(engine, name):
    project, _ = _execute(engine, name)
    for op in _request(name)["operations"]:
        if op.get("op") != "keyframe":
            continue
        seen = _get(engine, project, op["path"], op["time"])
        # The engine widens short vectors to its vec3 with 0 fill; the
        # fixture stores the CLI idiom ([x, y]). Both must agree where
        # the fixture speaks, and the fill must be 0 past it.
        assert len(seen) >= len(op["value"]), (name, op)
        for got, want in zip(seen, op["value"]):
            assert got == pytest.approx(want, abs=1e-9), (name, op)
        for fill in seen[len(op["value"]):]:
            assert fill == pytest.approx(0.0, abs=1e-9), (name, op)


@pytest.mark.parametrize("name", ["drift", "punch", "cut"])
def test_move_frames_match_static_reference_pixels(engine, name):
    """Keys render exactly what the same value renders statically.

    Bit-exact (PSNR infinite): the engine's sampler is a pure
    function of the sampled values, so a baked key and a direct set
    of one number must draw identical bytes.
    """
    _, frames = _execute(engine, name)
    for op in _request(name)["operations"]:
        if op.get("op") != "keyframe":
            continue
        frame = round(op["time"] * FPS)
        reference = _static_render(engine, name, op["path"],
                                   op["value"])
        rendered = _read_png(frames[frame])
        diff = _max_abs(rendered, reference)
        assert diff == 0, (name, op["time"], f"max abs diff {diff}")


def test_drift_opens_neutral_and_moves_the_picture(engine):
    _, frames = _execute(engine, "drift")
    neutral = _static_render(engine, "drift-neutral",
                             "transform/scale", [100.0, 100.0, 100.0])
    first = _read_png(frames[0])
    assert _max_abs(first, neutral) == 0
    last = _read_png(frames[-1])
    assert _max_abs(last, neutral) > 0


def test_shake_moves_the_centroid_by_its_projected_offset(engine):
    _, frames = _execute(engine, "shake")
    neutral = _static_render(engine, "shake-neutral",
                             "transform/position",
                             [WIDTH / 2.0, HEIGHT / 2.0])
    first = _read_png(frames[0])
    assert _max_abs(first, neutral) > 0, "the shake must move pixels"

    def centroid(img):
        ys, xs = np.nonzero(img[..., 3] > 0)
        return (float(xs.mean()), float(ys.mean()))

    base = centroid(neutral)
    ops = [op for op in _request("shake")["operations"]
           if op.get("op") == "keyframe"]
    for op in ops[:2]:
        frame = round(op["time"] * FPS)
        predicted = (op["value"][0] - WIDTH / 2.0,
                     op["value"][1] - HEIGHT / 2.0)
        got = centroid(_read_png(frames[frame]))
        measured = (got[0] - base[0], got[1] - base[1])
        assert measured == pytest.approx(predicted, abs=0.5), (
            op["time"], measured, predicted)
    # The decay settles: the tail renders the neutral picture back.
    tail = _read_png(frames[-1])
    assert _max_abs(tail, neutral) == 0


def test_renders_carry_alpha_at_comp_dimensions(engine):
    _, frames = _execute(engine, "drift")
    img = _read_png(frames[0])
    assert img.shape == (HEIGHT, WIDTH, 4)
    assert (img[..., 3] == 0).any(), "comp corners stay transparent"
    assert (img[..., 3] > 0).any(), "footage stays visible"
