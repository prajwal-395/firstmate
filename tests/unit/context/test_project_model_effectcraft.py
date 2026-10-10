"""Unit 7 tests: Project Model masks/effects project into EffectCraft.

Pure-Python boundary tests: projection, fail-closed refusals, receipt
ingest, UI exclusion, and the FFrames/EffectCraft conformance fixtures.
No EffectCraft CLI is built or invoked here; the Rust crate's adapter
test proves the checked-in requests speak the driver's serde contract.
"""
import copy
import json
from pathlib import Path

import pytest

from library.graphics_ir.model import Scene
from library.tools import project_model, project_model_effectcraft as ec

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/effectcraft_fframes_conformance"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _comp():
    return ec.CompSpec(name="Main", width=32, height=32,
                       frame_rate=10.0, duration_seconds=0.2)


def _renders(output="effect_#####.png"):
    return [ec.RenderSpec(format="rgba_png_sequence", output=output)]


def _receipt(projection, **overrides):
    render = {
        "format": "rgba_png_sequence",
        "path": "effect_00000.png",
        "width": projection.comp.width,
        "height": projection.comp.height,
        "frames": projection.comp.expected_frames(),
        "has_alpha": True,
    }
    receipt = {
        "version": ec.RECEIPT_VERSION,
        "project_id": projection.project_id,
        "upstream_revision": ec.PINNED_EFFECTCRAFT_REV,
        "engine_version": ec.PINNED_EFFECTCRAFT_VERSION,
        "project_file": "Main.ecproj",
        "renders": [render],
    }
    receipt.update(overrides)
    return receipt


def test_masked_projection_regenerates_the_checked_in_request():
    model = _fixture("model-masked.json")
    projection = ec.project_to_request(
        model, _comp(), _renders("masked_#####.png"),
        project_id="ec-conformance-masked")
    assert projection.request == _fixture("request-masked.json")
    assert projection.identity_map["masks"] == {"mask-checker": "checker.bmp"}
    assert projection.identity_map["layers"] == {
        "checker.bmp": {"clip_use": "use-checker", "asset": "asset-checker"}}
    assert projection.losses == []


def test_effect_projection_names_layers_keyframes_and_identities():
    model = _fixture("model-effect.json")
    projection = ec.project_to_request(
        model, _comp(), _renders(), project_id="ec-conformance-effect")
    assert projection.request == _fixture("request-effect.json")
    apply = next(op for op in projection.request["operations"]
                 if op["op"] == "apply_effect")
    assert apply == {"op": "apply_effect", "layer": "checker.bmp",
                     "effect_id": "ec.blur.gaussian",
                     "params": {"blurriness": 6.0}}
    keys = [op for op in projection.request["operations"] if op["op"] == "keyframe"]
    assert [(k["path"], k["time"], k["value"]) for k in keys] == [
        ("effects/#1/blurriness", 0.0, 6.0),
        ("effects/#1/blurriness", 0.2, 6.0),
    ]
    assert projection.identity_map["effects"]["effect-blur"] == {
        "layer": "checker.bmp", "effect_index": 1,
        "effectcraft_id": "ec.blur.gaussian"}


def test_person_mask_projects_and_binds_through_clip_use():
    model = _fixture("model-masked.json")
    model["entities"].append({"id": "person-ada", "kind": "person",
                              "label": "Ada", "object_class_id": None})
    model["clip_uses"][0]["entity_links"] = ["person-ada"]
    model["masks"][0]["entity_id"] = "person-ada"
    projection = ec.project_to_request(model, _comp(), _renders())
    assert projection.identity_map["masks"] == {"mask-checker": "checker.bmp"}
    assert projection.identity_map["entities"]["person-ada"] == ["checker.bmp"]


def test_unknown_effect_class_is_refused_never_passed_through():
    model = _fixture("model-effect.json")
    model["effects"][0]["class_id"] = "color.saturation"
    with pytest.raises(ec.UnknownEffect, match="color.saturation"):
        ec.project_to_request(model, _comp(), _renders())


def test_unknown_parameter_and_wrong_type_are_refused():
    model = _fixture("model-effect.json")
    bad_param = copy.deepcopy(model)
    bad_param["effects"][0]["parameters"] = {"saturation": 0.8}
    with pytest.raises(ec.UnknownParameter, match="saturation"):
        ec.project_to_request(bad_param, _comp(), _renders())
    bad_type = copy.deepcopy(model)
    bad_type["effects"][0]["parameters"] = {"blurriness": "soft"}
    with pytest.raises(ec.ParameterType, match="blurriness"):
        ec.project_to_request(bad_type, _comp(), _renders())


def test_animated_mask_unbound_mask_and_bad_target_are_refused():
    animated = _fixture("model-masked.json")
    animated["masks"][0]["representation"]["keyframes"].append(
        {"tick": 1, "bbox": [0.2, 0.2, 0.5, 0.5]})
    with pytest.raises(ec.UnprojectableMask, match="animated"):
        ec.project_to_request(animated, _comp(), _renders())

    unbound = _fixture("model-masked.json")
    unbound["masks"][0]["entity_id"] = "person-nobody"
    with pytest.raises(ec.UnprojectableMask, match="binds to 0"):
        ec.project_to_request(unbound, _comp(), _renders())

    rle = _fixture("model-masked.json")
    rle["masks"][0]["representation"] = {"uri": "masks/person-1.rle"}
    with pytest.raises(ec.UnprojectableMask, match="projectable shape"):
        ec.project_to_request(rle, _comp(), _renders())

    bad_target = _fixture("model-effect.json")
    bad_target["effects"][0]["target_id"] = "use-nobody"
    with pytest.raises(ec.UnprojectableEffectTarget, match="use-nobody"):
        ec.project_to_request(bad_target, _comp(), _renders())


def test_empty_projection_and_bad_render_specs_are_refused():
    model = _fixture("model-masked.json")
    model["masks"] = []
    with pytest.raises(ec.EmptyProjection):
        ec.project_to_request(model, _comp(), _renders())
    with pytest.raises(ec.EffectCraftAdapterError, match="at least one render"):
        ec.project_to_request(_fixture("model-masked.json"), _comp(), [])
    with pytest.raises(ec.EffectCraftAdapterError, match="#"):
        ec.project_to_request(_fixture("model-masked.json"), _comp(),
                              [ec.RenderSpec(output="no-pattern.png")])
    with pytest.raises(ec.EffectCraftAdapterError, match="render format"):
        ec.project_to_request(_fixture("model-masked.json"), _comp(),
                              [ec.RenderSpec(format="h264", output="out.mp4")])


def test_ingest_records_render_frame_map_identity_and_receipt():
    model = _fixture("model-masked.json")
    projection = ec.project_to_request(
        model, _comp(), _renders(), project_id="ec-conformance-masked")
    updated = ec.ingest_receipt(model, projection, _receipt(projection),
                                input_revision="rev-1")
    project_model.validate_project_model(updated)

    render = updated["renders"][-1]
    assert render["input_revision"] == "rev-1"
    assert render["frame_map_id"] == "map-ec-main"
    frame_map = next(m for m in updated["frame_maps"] if m["id"] == "map-ec-main")
    assert frame_map["from_clock"] == "clock-ec-main"
    assert frame_map["to_clock"] == "clock-render-main"
    assert frame_map["segments"] == [{
        "from": {"clock_id": "clock-ec-main", "start": 0, "end": 200},
        "to": {"clock_id": "clock-render-main", "start": 0, "end": 2},
        "mapping_kind": "retimed",
        "rate": {"numerator": 1, "denominator": 100},
        "source_clip_use": None,
    }]
    receipt = next(r for r in updated["receipts"] if r["id"] == "receipt-ec-main")
    assert receipt["status"] == "success"
    assert receipt["renderer"] == "effectcraft"
    assert receipt["details"]["upstream_revision"] == ec.PINNED_EFFECTCRAFT_REV
    assert receipt["details"]["has_alpha"] is True
    assert receipt["details"]["identity_map"] == projection.identity_map
    asset = next(a for a in updated["assets"] if a["id"] == render["output_asset_id"])
    assert asset["kind"] == "render"


def test_ingest_refuses_mismatched_receipts_without_touching_the_model():
    model = _fixture("model-masked.json")
    projection = ec.project_to_request(
        model, _comp(), _renders(), project_id="ec-conformance-masked")
    before = copy.deepcopy(model)

    flattened = _receipt(projection)
    flattened["renders"][0]["has_alpha"] = False
    with pytest.raises(ec.ReceiptMismatch, match="alpha"):
        ec.ingest_receipt(model, projection, flattened, input_revision="rev-1")

    wrong_rev = _receipt(projection, upstream_revision="0" * 40)
    with pytest.raises(ec.ReceiptMismatch, match="upstream revision"):
        ec.ingest_receipt(model, projection, wrong_rev, input_revision="rev-1")

    wrong_frames = _receipt(projection)
    wrong_frames["renders"][0]["frames"] = 99
    with pytest.raises(ec.ReceiptMismatch, match="frame count"):
        ec.ingest_receipt(model, projection, wrong_frames, input_revision="rev-1")

    wrong_project = _receipt(projection, project_id="someone-else")
    with pytest.raises(ec.ReceiptMismatch, match="does not match"):
        ec.ingest_receipt(model, projection, wrong_project, input_revision="rev-1")

    assert model == before


def test_effectcraft_ui_stays_out_of_rens_build_and_runtime():
    import tomllib
    workspace = tomllib.loads((ROOT / "native/Cargo.toml").read_text(encoding="utf-8"))
    members = workspace["workspace"]["members"]
    assert "ren-effectcraft" in members
    assert not [m for m in members
                if any(token in m for token in ("ui", "app", "desktop", "viewer"))]
    for member in ("ren-cli", "ren-core", "ren-protocol"):
        deps = tomllib.loads(
            (ROOT / f"native/{member}/Cargo.toml").read_text(encoding="utf-8"))
        assert "ren-effectcraft" not in deps.get("dependencies", {})
    driver_deps = tomllib.loads(
        (ROOT / "native/ren-effectcraft/Cargo.toml").read_text(encoding="utf-8"))
    assert not [d for d in driver_deps.get("dependencies", {})
                if d.startswith("effectcraft")]
    driver = (ROOT / "native/ren-effectcraft/src/lib.rs").read_text(encoding="utf-8")
    assert '"effectcraft-cli"' in driver


def test_conformance_fixtures_agree_across_both_renderers():
    spec = _fixture("spec.json")
    assert spec["version"] == 1
    for case in spec["cases"]:
        request = _fixture(case["effectcraft_request"])
        assert request["version"] == ec.REQUEST_VERSION
        assert request["operations"][0]["op"] == "new_comp"
        if case["graphics_ir_scene"] is not None:
            scene = Scene.model_validate(_fixture(case["graphics_ir_scene"]))
            assert scene.width == request["operations"][0]["width"]
            assert scene.height == request["operations"][0]["height"]
            assert scene.width == case["expect"]["width"]
    title = _fixture("request-title.json")
    assert (title["operations"][1]["op"], title["operations"][1]["text"]) == (
        "add_text", "REN")
    masked = _fixture("request-masked.json")
    assert masked["operations"][3] == {
        "op": "add_mask", "layer": "checker.bmp", "shape": "ellipse",
        "rect": [4.0, 4.0, 24.0, 24.0]}


def test_cli_projects_and_ingests_off_fixtures(tmp_path):
    model_path = FIXTURES / "model-masked.json"
    projection_path = tmp_path / "projection.json"
    rc = ec.main(["project", str(model_path), "--out", str(projection_path),
                  "--width", "32", "--height", "32", "--fps", "10.0",
                  "--duration", "0.2", "--render-output", "masked_#####.png",
                  "--project-id", "ec-conformance-masked"])
    assert rc == 0
    stored = json.loads(projection_path.read_text(encoding="utf-8"))["projection"]
    assert stored["request"] == _fixture("request-masked.json")

    receipt_path = tmp_path / "receipt.json"
    projection = ec.project_to_request(
        _fixture("model-masked.json"), _comp(),
        _renders("masked_#####.png"), project_id="ec-conformance-masked")
    receipt_path.write_text(json.dumps(_receipt(projection)), encoding="utf-8")
    out_path = tmp_path / "model-out.json"
    rc = ec.main(["ingest", str(model_path), "--projection", str(projection_path),
                  "--receipt", str(receipt_path), "--out", str(out_path),
                  "--input-revision", "rev-1"])
    assert rc == 0
    project_model.validate_project_model(
        json.loads(out_path.read_text(encoding="utf-8")))
