"""Effect-coverage proof: every native law renders through the pinned CLI.

Two halves, both measured (never asserted from parameter names):

- live-registry grounding: the mapped EffectCraft effects exist in the
  pinned CLI's own ``list_effects`` with the JSON kinds the adapter
  checks, and every refused family has no exact counterpart there
  (Ren parameter names reach no parameter the closest pinned effect
  declares, or no effect id matches at all);
- fixture-anchored pixel proofs: ``tests/fixtures/
  effectcraft_coverage`` carries the measurement chart plus 256px
  crops of real Ren Fusion stills (captured 2026-10-10 off Ren
  Qualification through broker qualification sessions). Each test
  below re-renders the same chart through the pinned CLI under the
  mapped law, crops the same region, and asserts the measured bound
  (``GRADE_PSNR_*`` floors live beside the laws in
  ``library/tools/project_model_effectcraft.py``; max-abs bounds are
  stated per test with the sweep number that set them).

Heavy: shells the pinned CLI and renders. It SKIPS naming the
missing piece when the CLI or ffmpeg is absent.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import project_model_effectcraft as ec

pytestmark = pytest.mark.real_model

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/effectcraft_coverage"
CHECKER = ROOT / "native/ren-effectcraft/tests/fixtures/checker.bmp"

PINNED_REV = "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
WIDTH, HEIGHT = 1920, 1080


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
    work = Path(str(tmp_path_factory.mktemp("ec-coverage-proof")))
    return {"cli": cli, "work": work}


def _run(engine, *args, stdin_lines=None):
    proc = subprocess.run(
        [engine["cli"], *args], input=stdin_lines, capture_output=True,
        text=True, timeout=600, check=False)
    assert proc.returncode == 0, proc.stderr[:800] + proc.stdout[:800]
    return proc.stdout


def _registry(engine):
    handshake = "\n".join([
        '{"jsonrpc":"2.0","id":1,"method":"initialize",'
        '"params":{"protocolVersion":"2025-03-26","capabilities":{},'
        '"clientInfo":{"name":"ren-effectcraft","version":"0.1.0"}}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call",'
        '"params":{"name":"list_effects","arguments":{}}}',
        "",
    ])
    out = _run(engine, "mcp", stdin_lines=handshake)
    lines = [line for line in out.splitlines() if line.strip()]
    response = json.loads(lines[1])
    result = response["result"]
    data = result.get("structuredContent")
    if data is None:
        data = json.loads(result["content"][0]["text"])
    effects = data if isinstance(data, list) else data["effects"]
    return {
        item["id"]: {p.get("id"): p.get("default")
                     for p in item.get("params", [])}
        for item in effects
    }


def _kind(default):
    if isinstance(default, bool):
        return "boolean"
    if isinstance(default, (int, float)):
        return "number"
    if isinstance(default, str):
        return "string"
    if isinstance(default, list):
        return "array"
    return "object"


def test_native_map_matches_the_live_registry(engine):
    """The checked-in snapshot is what the pinned CLI declares."""
    live = _registry(engine)
    for ren_class, entry in ec.VERIFIED_EFFECT_MAP.items():
        effect_id = entry["effect"]
        assert effect_id in live, (ren_class, effect_id)
        if entry.get("project") == "grade":
            wanted = {"slopeRed", "slopeGreen", "slopeBlue",
                      "offsetRed", "offsetGreen", "offsetBlue",
                      "saturation"}
        else:
            wanted = set(entry["params"].values())
        for engine_param in wanted:
            assert engine_param in live[effect_id], (
                ren_class, effect_id, engine_param)
            assert _kind(live[effect_id][engine_param]) == \
                ec.fixture_catalog()[effect_id][engine_param], (
                ren_class, effect_id, engine_param)


def test_refused_families_have_no_exact_live_counterpart(engine):
    """Each refusal is grounded in the live registry, not in prose."""
    live = _registry(engine)
    # Ren parameter names the closest pinned effect does not declare:
    # there is nowhere for the projected value to go.
    for missing in ("gain", "size"):
        assert missing not in live["ec.stylize.glow"], missing
    for missing in ("MasterStrength", "MasterXSize", "MasterYSize"):
        assert missing not in live["ec.noise.addgrain"], missing
    for missing in ("blend", "soft", "width", "height"):
        assert missing not in live["ec.stylize.ccvignette"], missing
    # Ren transition recipes name no pinned transition effect.
    for recipe in ("zoom_blur", "defocus", "flash"):
        assert f"ec.transition.{recipe}" not in live, recipe
    # No pinned effect at all for these families.
    for stem in ("defocus", "tv_power", "powerband", "backdrop"):
        assert [eid for eid in live if stem in eid] == [], stem


def _render(engine, name, source, effects=(), keys=(), fps=10.0,
            duration=0.5, width=WIDTH, height=HEIGHT):
    work = engine["work"] / name
    work.mkdir(parents=True, exist_ok=True)
    batch = [
        "comp.new", json.dumps({"name": "Main", "width": width,
                                "height": height, "frameRate": fps,
                                "duration": duration}),
        "file.import", json.dumps({"paths": [str(source)]}),
        "layer.addItem", json.dumps({"item": source.name}),
    ]
    seen = 0
    for effect_id, params in effects:
        batch += ["effect.apply", json.dumps({"layer": source.name,
                                              "effect": effect_id})]
        seen += 1
        for param, value in params.items():
            batch += ["prop.set", json.dumps(
                {"layer": source.name,
                 "path": f"effects/#{seen}/{param}", "value": value})]
    for path, time, value in keys:
        batch += ["prop.addKey", json.dumps({"layer": source.name,
                                             "path": path, "time": time,
                                             "value": value})]
    project = work / f"{name}.ecproj"
    if project.exists():
        project.unlink()
    out = _run(engine, "run", *batch, "--empty", "--save-as",
               str(project), "--json")
    assert json.loads(out)["saved"] == str(project)
    pattern = work / f"{name}_#####.png"
    _run(engine, "render", "--project", str(project), "--comp", "Main",
         "--out", str(pattern), "--format", "png",
         "--start", "0", "--end", repr(duration),
         "--channels", "rgba", "--json")
    return sorted(work.glob(f"{name}_*.png"))


def _read_rgb(path, width=WIDTH, height=HEIGHT):
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=180,
        check=False)
    assert proc.returncode == 0, proc.stderr[:300]
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(
        height, width, 3)


def _read_rgba(path, width=WIDTH, height=HEIGHT):
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgba", "-"], capture_output=True, timeout=180,
        check=False)
    assert proc.returncode == 0, proc.stderr[:300]
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(
        height, width, 4)


def _crop(img, box):
    x, y, w, h = box
    return img[y:y + h, x:x + w]


def _max_abs(a, b):
    return int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max())


RAMP = (0, 0, 256, 256)
BARS = (240, 432, 256, 216)
DOTS = (832, 648, 256, 216)
PASTEL = (0, 0, 256, 256)
CDL = "ec.color.ociocdl"


def _ociocdl(slope=None, offset=None, saturation=None):
    params = {}
    if slope is not None:
        params.update({"slopeRed": slope, "slopeGreen": slope,
                       "slopeBlue": slope})
    if offset is not None:
        params.update({"offsetRed": offset, "offsetGreen": offset,
                       "offsetBlue": offset})
    if saturation is not None:
        params["saturation"] = saturation
    return [(CDL, params)]


def test_grade_gain_renders_the_measured_slope(engine):
    frame = _render(engine, "cov-gain", FIXTURES / "chart.png",
                    _ociocdl(slope=0.5))[0]
    ren = _read_rgb(FIXTURES / "ren/gain05-ramp.png", 256, 256)
    got = _crop(_read_rgb(frame), RAMP)
    assert _max_abs(got, ren) <= 1


def test_grade_contrast_renders_the_measured_pivot(engine):
    frame = _render(engine, "cov-contrast", FIXTURES / "chart.png",
                    _ociocdl(slope=1.5, offset=-0.25))[0]
    ren = _read_rgb(FIXTURES / "ren/contrast05-ramp.png", 256, 256)
    got = _crop(_read_rgb(frame), RAMP)
    assert _max_abs(got, ren) <= 1


def test_grade_brightness_contrast_compose_additively(engine):
    frame = _render(engine, "cov-brightcon", FIXTURES / "chart.png",
                    _ociocdl(slope=1.4, offset=0.1))[0]
    ren = _read_rgb(FIXTURES / "ren/brightcon-ramp.png", 256, 256)
    got = _crop(_read_rgb(frame), RAMP)
    assert _max_abs(got, ren) <= 2


def test_saturation_desaturation_on_primaries(engine):
    frame = _render(engine, "cov-sat05", FIXTURES / "chart.png",
                    _ociocdl(saturation=0.5))[0]
    ren = _read_rgb(FIXTURES / "ren/sat05-bars.png", 256, 216)
    got = _crop(_read_rgb(frame), BARS)
    # The primaries corner: gamut-mapping differs here by up to 16
    # LSB; everything achromatic is bit-identical.
    assert _max_abs(got, ren) <= 16


def test_saturation_boost_is_exact_where_primaries_clip(engine):
    frame = _render(engine, "cov-sat15", FIXTURES / "chart.png",
                    _ociocdl(saturation=1.5))[0]
    ren = _read_rgb(FIXTURES / "ren/sat15-bars.png", 256, 216)
    got = _crop(_read_rgb(frame), BARS)
    assert _max_abs(got, ren) <= 1


def test_saturation_boost_on_pastels(engine):
    frame = _render(engine, "cov-pastel-boost",
                    FIXTURES / "pastel1920.png",
                    _ociocdl(saturation=1.83))[0]
    ren = _read_rgb(FIXTURES / "ren/pastel-boost.png", 256, 256)
    got = _crop(_read_rgb(frame), PASTEL)
    assert _max_abs(got, ren) <= 4


def test_combined_grade_is_exact_in_gamut(engine):
    frame = _render(engine, "cov-pastel-combined",
                    FIXTURES / "pastel1920.png",
                    _ociocdl(slope=1.95, offset=-0.15, saturation=0.7))[0]
    ren = _read_rgb(FIXTURES / "ren/pastel-combined.png", 256, 256)
    got = _crop(_read_rgb(frame), PASTEL)
    assert _max_abs(got, ren) <= 1


def _flattened(frame):
    rgba = _read_rgba(frame).astype(np.float64)
    return rgba[..., :3] * (rgba[..., 3:4] / 255.0)


def test_fade_blend_renders_opacity_over_black(engine):
    frame = _render(engine, "cov-fade05", FIXTURES / "chart.png", [],
                    keys=[("transform/opacity", 0.0, 50.0)])[0]
    ren = _read_rgb(FIXTURES / "ren/fade05-dots.png", 256, 216)
    got = _crop(_flattened(frame), DOTS)
    assert np.abs(got - ren.astype(np.float64)).max() <= 2.0


def test_fade_to_black_mid_ramp_matches_the_recipe(engine):
    frame = _render(engine, "cov-f2bmid", FIXTURES / "chart.png", [],
                    keys=[("transform/opacity", 0.0, 50.0)])[0]
    ren = _read_rgb(FIXTURES / "ren/f2bmid-dots.png", 256, 216)
    got = _crop(_flattened(frame), DOTS)
    assert np.abs(got - ren.astype(np.float64)).max() <= 2.0


def test_static_zoom_matches_fusion_size(engine):
    frame = _render(engine, "cov-zoom11", FIXTURES / "chart.png", [],
                    keys=[("transform/scale", 0.0,
                           [110.0, 110.0, 100.0])])[0]
    ren = _read_rgb(FIXTURES / "ren/zoom11-dots.png", 256, 216)
    got = _crop(_read_rgb(frame), DOTS)
    assert _max_abs(got, ren) <= 2


def test_smooth_fade_ramp_matches_frame_by_frame(engine):
    """The cubic-sampled ramp renders Ren's smooth Bezier per frame.

    The full 12-frame fade_out ramp baked as dense opacity keys at
    the comp clock; frame 113 (blend 0.6875 off the exact spline)
    must flatten within the fade tolerance. The static points and
    the endpoint are covered above and in the unit tests.
    """
    from library.tools.fusion.effects import (
        fade_blend_spline,
        sample_smooth_bezier,
    )
    spline = fade_blend_spline(0, 119, 0, 12)
    dense = sample_smooth_bezier(spline.keyframes, 0, 119)
    keys = [("transform/opacity", frame / 24.0,
             round(dense[frame], 6) * 100.0)
            for frame in range(120)]
    frames = _render(engine, "cov-faderamp", FIXTURES / "chart.png",
                     [], keys=keys, fps=24.0, duration=5.0)
    assert len(frames) == 120
    ren = _read_rgb(FIXTURES / "ren/fademid-dots.png", 256, 216)
    got = _crop(_flattened(frames[113]), DOTS)
    assert np.abs(got - ren.astype(np.float64)).max() <= 2.0


def test_native_blur_key_holds_and_moves_pixels(engine):
    frame = _render(engine, "cov-blur", CHECKER,
                    [("ec.blur.gaussian", {"blurriness": 6.0})],
                    fps=10.0, duration=0.2, width=32, height=32)[0]
    out = _run(engine, "get", "--project", str(
        (engine["work"] / "cov-blur/cov-blur.ecproj")),
        "Main", CHECKER.name, "effects/#1/blurriness",
        "--time", "0", "--json")
    assert json.loads(out)["value"] == pytest.approx(6.0)
    plain = _render(engine, "cov-plain", CHECKER, [],
                    fps=10.0, duration=0.2, width=32, height=32)[0]
    blurred = _read_rgb(frame, 32, 32)
    unblurred = _read_rgb(plain, 32, 32)
    assert _max_abs(blurred, unblurred) > 0
