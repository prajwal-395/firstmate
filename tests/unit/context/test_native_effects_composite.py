"""Gap 1 tests: the native composite resolves values from declarations.

Proves the grade and the bezel come from the project's own
declarations (never hardcoded here), that unmapped look elements
refuse by name, that model effects mint in the shape the engines
read, and that the ffmpeg composite + SSIM helpers run on tiny
synthetic renders. No engine CLI is built or invoked here.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import native_effects_composite as nec
from library.tools.series_look import LookDeclarationError

ROOT = Path(__file__).resolve().parents[3]
ADAPTER_RS = ROOT / "native/ren-filmcraft/src/adapter.rs"

LOOK_YAML = """\
name: demo
slug: demo
status: draft
source:
  language: en
pipeline:
  brand_template: demo
style:
  series_look:
    name: v04_teal_split
    intent: Split-tone vivid.
    cdl:
      slope: [1.03, 1.0, 0.96]
      offset: [-0.01, 0.005, 0.02]
      power: [1.0, 1.0, 1.0]
      saturation: 1.12
    contrast: 0.12
    glow: {gain: 0.20, threshold: 0.72, size: 3.5}
    vignette: {blend: 0.35, soft: 0.30}
"""

FFMPEG = shutil.which("ffmpeg")


def _project(tmp_path, yaml_text=LOOK_YAML):
    (tmp_path / "project.yaml").write_text(yaml_text, encoding="utf-8")
    return str(tmp_path)


def test_grade_values_match_the_declared_look(tmp_path):
    resolved = nec.resolve_native_grade(_project(tmp_path))
    assert resolved is not None
    assert resolved["name"] == "v04_teal_split"
    grade = resolved["grade"]
    assert grade["gain"] == pytest.approx((1.03 + 1.0 + 0.96) / 3.0)
    assert grade["contrast"] == pytest.approx(0.12)
    assert grade["brightness"] == pytest.approx((-0.01 + 0.005 + 0.02) / 3.0)
    assert grade["saturation"] == pytest.approx(1.12)


def test_grade_refuses_unmapped_elements_by_name(tmp_path):
    resolved = nec.resolve_native_grade(_project(tmp_path))
    elements = {entry["element"] for entry in resolved["refused"]}
    assert elements == {"glow", "vignette"}
    reasons = " ".join(entry["reason"] for entry in resolved["refused"])
    assert "19.9 dB" in reasons  # the measured glow sweep, not a guess


def test_no_look_declared_means_no_grade(tmp_path):
    folder = _project(tmp_path, "name: demo\nslug: demo\nstatus: draft\nsource:\n  language: en\n")
    assert nec.resolve_native_grade(folder) is None


def test_template_look_answers_when_the_project_declares_none(tmp_path):
    folder = _project(tmp_path, "name: demo\nslug: demo\nstatus: draft\nsource:\n  language: en\n")
    template_style = {
        "series_look": {
            "name": "template-look",
            "cdl": {
                "slope": [1.0, 1.0, 1.0],
                "offset": [0.0, 0.0, 0.0],
                "power": [1.0, 1.0, 1.0],
                "saturation": 1.0,
            },
        }
    }
    resolved = nec.resolve_native_grade(folder, template_style)
    assert resolved is not None
    assert resolved["name"] == "template-look"
    assert resolved["grade"]["gain"] == pytest.approx(1.0)


def test_non_identity_power_refuses_rather_than_folding_into_gain(tmp_path):
    text = LOOK_YAML.replace("power: [1.0, 1.0, 1.0]", "power: [1.0, 1.1, 1.0]")
    resolved = nec.resolve_native_grade(_project(tmp_path, text))
    elements = {entry["element"] for entry in resolved["refused"]}
    assert "cdl.power" in elements


def test_malformed_look_raises_instead_of_completing(tmp_path):
    text = LOOK_YAML.replace("      saturation: 1.12\n", "")
    with pytest.raises(LookDeclarationError):
        nec.resolve_native_grade(_project(tmp_path, text))


def test_minted_effects_carry_the_resolved_grade_per_target(tmp_path):
    resolved = nec.resolve_native_grade(_project(tmp_path))
    targets = [
        {"clip_use_id": "use-a", "clock_id": "clock-24", "start": 0, "end": 240},
        {"clip_use_id": "use-b", "clock_id": "clock-24", "start": 240, "end": 480},
    ]
    effects = nec.mint_grade_effects(resolved, targets)
    assert [effect["class_id"] for effect in effects] == ["color.grade", "color.grade"]
    assert [effect["target_id"] for effect in effects] == ["use-a", "use-b"]
    assert effects[0]["parameters"] == resolved["grade"]
    assert effects[0]["interval"] == {"clock_id": "clock-24", "start": 0, "end": 240}
    assert effects[0]["keyframes"] == []


def test_minted_effects_refuse_an_empty_span(tmp_path):
    resolved = nec.resolve_native_grade(_project(tmp_path))
    with pytest.raises(ValueError):
        nec.mint_grade_effects(resolved, [
            {"clip_use_id": "use-a", "clock_id": "clock-24", "start": 9, "end": 9}])


def test_bezel_resolves_the_declared_asset(tmp_path):
    asset = tmp_path / "frame.png"
    asset.write_bytes(b"\x89PNG\r\n\x1a\n")
    folder = _project(tmp_path, (
        "name: demo\nslug: demo\nstatus: draft\n"
        "source:\n  language: en\n"
        "pipeline:\n  brand_template: demo\n"
        "  tv_frame:\n    asset: frame.png\n    punch_in: 2.30\n"
        "    offset_y: 220\n    scale: 0.927\n"))
    bezel = nec.resolve_native_bezel(folder)
    assert bezel is not None
    assert bezel["asset"] == str(asset.resolve())
    assert bezel["punch_in"] == pytest.approx(2.30)
    assert bezel["offset_y"] == 220
    assert bezel["scale"] == pytest.approx(0.927)


def test_no_asset_declared_means_no_bezel(tmp_path):
    folder = _project(tmp_path, "name: demo\nslug: demo\nstatus: draft\nsource:\n  language: en\n")
    assert nec.resolve_native_bezel(folder) is None


def test_punchin_mints_filmcraft_scale_keys_from_declarations(tmp_path):
    asset = tmp_path / "frame.png"
    asset.write_bytes(b"\x89PNG\r\n\x1a\n")
    folder = _project(tmp_path, (
        "name: demo\nslug: demo\nstatus: draft\n"
        "source:\n  language: en\n"
        "pipeline:\n  brand_template: demo\n"
        "  tv_frame:\n    asset: frame.png\n    punch_in: 2.30\n"
        "    scale: 0.927\n"))
    bezel = nec.resolve_native_bezel(folder)
    effects = nec.mint_punchin_effects(bezel, [
        {"clip_use_id": "use-a", "clock_id": "clock-24", "start": 0, "end": 240}])
    assert effects[0]["class_id"] == "transform.scale"
    assert effects[0]["parameters"] == {}
    assert effects[0]["keyframes"] == [
        {"tick": 0, "value": pytest.approx(2.30 * 0.927)},
        {"tick": 239, "value": pytest.approx(2.30 * 0.927)},
    ]
    routed = nec.partition_effect_classes([effects[0]["class_id"]])
    assert routed["filmcraft"] == ["transform.scale"]


def test_punchin_refuses_an_empty_span(tmp_path):
    with pytest.raises(ValueError):
        nec.mint_punchin_effects({"punch_in": 2.30, "scale": 1.0}, [
            {"clip_use_id": "use-a", "clock_id": "clock-24", "start": 7, "end": 7}])


def test_partition_routes_each_class_to_its_engine():
    routed = nec.partition_effect_classes([
        "color.lut", "transform.scale", "audio.volume",
        "color.grade", "blur.gaussian", "color.saturation",
        "move.slow_zoom_in", "fade.opacity",
        "glow.soft", "tv_power",
    ])
    assert routed["filmcraft"] == ["color.lut", "transform.scale", "audio.volume"]
    assert routed["effectcraft"] == [
        "color.grade", "blur.gaussian", "color.saturation",
        "move.slow_zoom_in", "fade.opacity"]
    assert routed["unmapped"] == ["glow.soft", "tv_power"]


def test_filmcraft_class_set_matches_the_rust_adapter():
    source = ADAPTER_RS.read_text(encoding="utf-8")
    for class_id in sorted(nec.FILMCRAFT_EXECUTED_CLASSES):
        assert f'"{class_id}"' in source, (
            f"{class_id} left the Rust filmcraft_executed set")


def test_grade_filter_is_derived_from_the_grade_dict():
    grade = {"gain": 0.9967, "contrast": 0.12, "brightness": 0.005, "saturation": 1.12}
    filtr = nec.grade_ffmpeg_filter(grade)
    assert "0.9967" in filtr and "1.12" in filtr and "contrast=1.12" in filtr


def test_composite_refuses_an_absent_base(tmp_path):
    with pytest.raises(FileNotFoundError):
        nec.composite_native(tmp_path / "missing.mp4", tmp_path / "out.mp4")


def test_ssim_refuses_an_absent_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        nec.measure_ssim(tmp_path / "missing.mp4", tmp_path / "missing2.mp4")


def _render_base(path, frames=8):
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size=64x112:rate=24:duration={frames / 24}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(path),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False)
    assert completed.returncode == 0, completed.stderr[:300]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_grade_only_composite_renders_and_scores_itself(tmp_path):
    base = tmp_path / "base.mp4"
    _render_base(base)
    out = tmp_path / "graded.mp4"
    grade = {"gain": 1.0, "contrast": 0.0, "brightness": 0.0, "saturation": 1.0}
    receipt = nec.composite_native(base, out, grade=grade)
    assert receipt["grade_applied"] and not receipt["bezel_applied"]
    scores = nec.measure_ssim(base, out, frames=8)
    assert scores["Y"] > 0.99  # a neutral grade barely moves the picture


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_declared_grade_moves_the_picture(tmp_path):
    base = tmp_path / "base.mp4"
    _render_base(base)
    out = tmp_path / "graded.mp4"
    nec.composite_native(base, out, grade={
        "gain": 1.2, "contrast": 0.3, "brightness": 0.1, "saturation": 1.5})
    scores = nec.measure_ssim(base, out, frames=8)
    assert scores["Y"] < 0.99


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_bezel_overlay_composites_the_declared_asset(tmp_path):
    base = tmp_path / "base.mp4"
    _render_base(base)
    # A landscape asset over a portrait base: the look turns it
    # upright (applied_rotation), so opaque ink must land on frame.
    bezel = tmp_path / "bezel.png"
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=black:size=112x64",
        "-frames:v", "1", str(bezel),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False)
    assert completed.returncode == 0, completed.stderr[:300]
    out = tmp_path / "framed.mp4"
    receipt = nec.composite_native(
        base, out, bezel_asset=bezel, bezel_scale=0.927, bezel_offset_y=2)
    assert receipt["bezel_applied"]
    assert out.is_file()
    scores = nec.measure_ssim(base, out, frames=8)
    assert scores["Y"] < 0.999  # the opaque bezel draws over the picture
