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


def test_matte_projection_regenerates_the_checked_in_request():
    model = _fixture("model-matte.json")
    comp = ec.CompSpec(name="Main", width=32, height=32,
                       frame_rate=10.0, duration_seconds=0.2)
    sequence = ec.MatteSequence(
        mask_id="mask-checker", stem="mask-checker_matte",
        first_frame=0, count=2,
        files=["matte_00000.png", "matte_00001.png"],
        item_name="mask-checker_matte_[00000-00001].png",
        width=32, height=32)
    projection = ec.project_to_request(
        model, comp, _renders("matted_#####.png"),
        project_id="ec-conformance-matte",
        mattes={"mask-checker": sequence})
    assert projection.request == _fixture("request-matte.json")


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
    model["effects"][0]["class_id"] = "effect.does-not-exist"
    with pytest.raises(ec.UnknownEffect, match="does-not-exist"):
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
    with pytest.raises(ec.UnprojectableMask, match="not a static box"):
        ec.project_to_request(animated, _comp(), _renders())

    unbound = _fixture("model-masked.json")
    unbound["masks"][0]["entity_id"] = "person-nobody"
    with pytest.raises(ec.UnprojectableMask, match="binds to 0"):
        ec.project_to_request(unbound, _comp(), _renders())

    rle = _fixture("model-masked.json")
    rle["masks"][0]["representation"] = {"uri": "masks/person-1.rle"}
    with pytest.raises(ec.UnprojectableMask, match="not a static box"):
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


def test_ingest_passes_through_the_render_backend_recording():
    model = _fixture("model-masked.json")
    projection = ec.project_to_request(
        model, _comp(), _renders(), project_id="ec-conformance-masked")
    receipt = _receipt(projection)
    receipt["renders"][0].update({
        "backend": "gpu",
        "adapter": "Apple M5 (Metal)",
        "backend_note": None,
    })
    updated = ec.ingest_receipt(model, projection, receipt,
                                input_revision="rev-1")
    stored = next(r for r in updated["receipts"] if r["id"] == "receipt-ec-main")
    assert stored["details"]["renders"][0]["backend"] == "gpu"
    assert stored["details"]["renders"][0]["adapter"] == "Apple M5 (Metal)"
    assert stored["details"]["renders"][0]["backend_note"] is None
    # Receipts written before the backend recording stay ingestible and
    # record nothing rather than a guess.
    plain = ec.ingest_receipt(model, projection, _receipt(projection),
                              input_revision="rev-1")
    stored_plain = next(r for r in plain["receipts"] if r["id"] == "receipt-ec-main")
    assert stored_plain["details"]["renders"][0]["backend"] is None
    assert stored_plain["details"]["renders"][0]["adapter"] is None
    assert stored_plain["details"]["renders"][0]["backend_note"] is None


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


def test_saturation_maps_onto_ociosaturation_identity():
    model = _fixture("model-effect.json")
    model["effects"][0]["class_id"] = "color.saturation"
    model["effects"][0]["parameters"] = {"saturation": 0.8}
    model["effects"][0]["keyframes"] = [
        {"tick": 0, "value": 0.8}, {"tick": 2, "value": 0.8}]
    projection = ec.project_to_request(model, _comp(), _renders())
    apply = next(op for op in projection.request["operations"]
                 if op["op"] == "apply_effect")
    assert apply["effect_id"] == "ec.color.ociocdl"
    assert apply["params"] == {"saturation": 0.8}
    keys = [op for op in projection.request["operations"]
            if op["op"] == "keyframe"]
    assert [k["path"] for k in keys] == [
        "effects/#1/saturation", "effects/#1/saturation"]
    assert [k["value"] for k in keys] == [0.8, 0.8]


def test_grade_projects_slope_offset_and_saturation():
    model = _fixture("model-effect.json")
    model["effects"][0]["class_id"] = "color.grade"
    model["effects"][0]["parameters"] = {
        "gain": 1.5, "contrast": 0.3, "saturation": 0.7}
    model["effects"][0]["keyframes"] = []
    projection = ec.project_to_request(model, _comp(), _renders())
    apply = next(op for op in projection.request["operations"]
                 if op["op"] == "apply_effect")
    assert apply["effect_id"] == "ec.color.ociocdl"
    assert apply["params"] == {
        "slopeRed": 1.95, "slopeGreen": 1.95, "slopeBlue": 1.95,
        "offsetRed": -0.15, "offsetGreen": -0.15, "offsetBlue": -0.15,
        "saturation": 0.7,
    }
    assert projection.identity_map["effects"]["effect-blur"] == {
        "layer": "checker.bmp", "effect_index": 1,
        "effectcraft_id": "ec.color.ociocdl"}


def test_grade_defaults_absent_axes_to_neutral():
    model = _fixture("model-effect.json")
    model["effects"][0]["class_id"] = "color.grade"
    model["effects"][0]["parameters"] = {"gain": 2.0}
    model["effects"][0]["keyframes"] = []
    projection = ec.project_to_request(model, _comp(), _renders())
    apply = next(op for op in projection.request["operations"]
                 if op["op"] == "apply_effect")
    assert apply["params"]["slopeRed"] == pytest.approx(2.0)
    assert apply["params"]["offsetRed"] == pytest.approx(0.0)
    assert apply["params"]["saturation"] == pytest.approx(1.0)


def test_grade_refusals_name_the_axis():
    model = _fixture("model-effect.json")
    model["effects"][0]["class_id"] = "color.grade"
    model["effects"][0]["keyframes"] = []
    empty = copy.deepcopy(model)
    empty["effects"][0]["parameters"] = {}
    with pytest.raises(ec.EffectCraftAdapterError, match="no parameters"):
        ec.project_to_request(empty, _comp(), _renders())
    named = copy.deepcopy(model)
    named["effects"][0]["parameters"] = {"gain": 1.0, "glow": 5.0}
    with pytest.raises(ec.UnknownParameter, match="glow"):
        ec.project_to_request(named, _comp(), _renders())
    typed = copy.deepcopy(model)
    typed["effects"][0]["parameters"] = {"gain": "loud"}
    with pytest.raises(ec.ParameterType, match="gain"):
        ec.project_to_request(typed, _comp(), _renders())
    keyed = copy.deepcopy(model)
    keyed["effects"][0]["parameters"] = {"gain": 1.5}
    keyed["effects"][0]["keyframes"] = [{"tick": 0, "value": 1.5}]
    with pytest.raises(ec.EffectCraftAdapterError, match="ambiguous"):
        ec.project_to_request(keyed, _comp(), _renders())


def _read_matte_alpha(path):
    import struct
    import zlib
    raw = Path(path).read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, width, height, pixels = 8, None, None, b""
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        tag = raw[pos + 4:pos + 8]
        payload = raw[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color, _, _, _ = struct.unpack(
                ">IIBBBBB", payload)
            assert (depth, color) == (8, 6)
        elif tag == b"IDAT":
            pixels += payload
        pos += 12 + length
    data = zlib.decompress(pixels)
    stride = width * 4 + 1
    alpha = bytearray()
    for row in range(height):
        assert data[row * stride] == 0
        for col in range(width):
            px = data[row * stride + 1 + col * 4:row * stride + 5 + col * 4]
            assert px[:3] == b"\xff\xff\xff"
            alpha.append(px[3])
    return width, height, bytes(alpha)


def _animated_model():
    model = _fixture("model-masked.json")
    model["masks"][0]["representation"]["keyframes"].append(
        {"tick": 1, "bbox": [0.5, 0.5, 0.25, 0.25]})
    return model


def test_matte_sequence_rasterizes_animated_boxes_per_frame(tmp_path):
    model = _animated_model()
    mask = model["masks"][0]
    comp = ec.CompSpec(name="Main", width=8, height=8,
                       frame_rate=10.0, duration_seconds=0.2)
    sequence = ec.write_matte_sequence(mask, model, comp, tmp_path)
    assert (sequence.first_frame, sequence.count) == (0, 2)
    assert sequence.item_name == f"{sequence.stem}_[00000-00001].png"
    _, _, first = _read_matte_alpha(sequence.files[0])
    assert first[3 * 8 + 3] == 255 and first[0] == 0
    _, _, second = _read_matte_alpha(sequence.files[1])
    assert second[5 * 8 + 5] == 255 and second[0] == 0


def test_matte_sequence_decodes_rle_with_held_frames(tmp_path):
    model = _fixture("model-masked.json")
    model["masks"][0]["representation"] = {
        "kind": "rle", "width": 4, "height": 4,
        "frames": [{"tick": 0, "rle": "0 4"}],
    }
    comp = ec.CompSpec(name="Main", width=4, height=4,
                       frame_rate=10.0, duration_seconds=0.2)
    sequence = ec.write_matte_sequence(
        model["masks"][0], model, comp, tmp_path)
    assert sequence.count == 2
    for path in sequence.files:
        _, _, alpha = _read_matte_alpha(path)
        assert alpha == bytes([255] * 4 + [0] * 12)


def test_matte_sequence_refuses_what_it_cannot_cover(tmp_path):
    comp = ec.CompSpec(name="Main", width=8, height=8,
                       frame_rate=10.0, duration_seconds=0.2)
    model = _animated_model()
    model["masks"][0]["representation"]["keyframes"] = [
        {"tick": 1, "bbox": [0.5, 0.5, 0.25, 0.25]}]
    with pytest.raises(ec.UnprojectableMask, match="no keyframe covering"):
        ec.write_matte_sequence(model["masks"][0], model, comp, tmp_path)

    rle = _fixture("model-masked.json")
    rle["masks"][0]["representation"] = {
        "kind": "rle", "width": 16, "height": 16,
        "frames": [{"tick": 0, "rle": "0 1"}],
    }
    with pytest.raises(ec.UnprojectableMask, match="resampling"):
        ec.write_matte_sequence(rle["masks"][0], rle, comp, tmp_path)

    pixels = _fixture("model-masked.json")
    pixels["masks"][0]["representation"] = {
        "kind": "face_bbox_track", "face_track": "t1",
        "samples": [{"t": 0.0, "box": [4.0, 4.0, 8.0, 8.0]}],
    }
    with pytest.raises(ec.UnprojectableMask, match="not normalized"):
        ec.write_matte_sequence(pixels["masks"][0], pixels, comp, tmp_path)

    unknown = _fixture("model-masked.json")
    unknown["masks"][0]["representation"] = {"uri": "masks/person-1.rle"}
    with pytest.raises(ec.UnprojectableMask, match="no matte rasterization"):
        ec.write_matte_sequence(unknown["masks"][0], unknown, comp, tmp_path)


def test_face_track_samples_rasterize_when_normalized(tmp_path):
    model = _fixture("model-masked.json")
    model["masks"][0]["representation"] = {
        "kind": "face_bbox_track", "face_track": "t1",
        "samples": [{"t": 0.0, "box": [0.0, 0.0, 0.5, 0.5]},
                    {"t": 0.1, "box": [0.0, 0.0, 0.5, 0.5]}],
    }
    comp = ec.CompSpec(name="Main", width=4, height=4,
                       frame_rate=10.0, duration_seconds=0.2)
    sequence = ec.write_matte_sequence(
        model["masks"][0], model, comp, tmp_path)
    _, _, alpha = _read_matte_alpha(sequence.files[0])
    assert alpha == bytes([255] * 2 + [0] * 2) * 2 + bytes([0] * 8)


def test_matte_projection_conforms_sequence_and_sets_track_matte(tmp_path):
    model = _animated_model()
    comp = ec.CompSpec(name="Main", width=32, height=32,
                       frame_rate=10.0, duration_seconds=0.2)
    sequence = ec.write_matte_sequence(
        model["masks"][0], model, comp, tmp_path)
    projection = ec.project_to_request(
        model, comp, _renders(), mattes={sequence.mask_id: sequence})
    ops = projection.request["operations"]
    kinds = [op["op"] for op in ops]
    assert kinds == ["new_comp", "import_footage", "add_footage_layer",
                     "import_footage", "interpret_footage",
                     "add_footage_layer", "set_track_matte"]
    assert ops[3] == {"op": "import_footage", "path": sequence.files[0]}
    assert ops[4] == {"op": "interpret_footage", "frame_rate": 10.0}
    assert ops[5] == {"op": "add_footage_layer",
                      "footage": sequence.item_name}
    assert ops[6] == {"op": "set_track_matte", "layer": "checker.bmp",
                      "matte": sequence.item_name, "kind": "alpha"}
    assert projection.identity_map["masks"] == {"mask-checker": "checker.bmp"}
    assert (projection.identity_map["mattes"]["mask-checker"]["item_name"]
            == sequence.item_name)


def test_matte_projection_refuses_a_matte_for_another_clock(tmp_path):
    model = _animated_model()
    comp = ec.CompSpec(name="Main", width=32, height=32,
                       frame_rate=10.0, duration_seconds=0.2)
    sequence = ec.write_matte_sequence(
        model["masks"][0], model, comp, tmp_path)
    other = ec.CompSpec(name="Main", width=32, height=32,
                        frame_rate=20.0, duration_seconds=0.2)
    with pytest.raises(ec.UnprojectableMask, match="covers comp frames"):
        ec.project_to_request(model, other, _renders(),
                              mattes={sequence.mask_id: sequence})


def test_e2e_person_mask_rasterizes_to_identical_matte_frames(tmp_path):
    e2e = ROOT / "tests/fixtures/project_model_e2e/expected-project-model.json"
    model = json.loads(e2e.read_text(encoding="utf-8"))
    mask = next(m for m in model["masks"] if m["id"] == "mask-ada")
    assert mask["entity_id"] == "person-ada"
    comp = ec.CompSpec(name="Main", width=48, height=32,
                       frame_rate=24.0, duration_seconds=2.0)
    sequence = ec.write_matte_sequence(mask, model, comp, tmp_path)
    assert (sequence.first_frame, sequence.count) == (0, 48)
    _, _, expected = _read_matte_alpha(sequence.files[0])
    assert 0 < sum(expected) // 255 < 48 * 32
    for path in sequence.files[1:]:
        _, _, alpha = _read_matte_alpha(path)
        assert alpha == expected


def test_cli_matte_and_project_round_trip_off_fixtures(tmp_path):
    animated = _animated_model()
    model_path = tmp_path / "model-animated.json"
    model_path.write_text(json.dumps(animated), encoding="utf-8")
    out_dir = tmp_path / "mattes"
    sidecar = tmp_path / "matte.json"
    rc = ec.main(["matte", str(model_path), "--mask", "mask-checker",
                  "--out-dir", str(out_dir), "--sidecar", str(sidecar),
                  "--width", "32", "--height", "32", "--fps", "10.0",
                  "--duration", "0.2"])
    assert rc == 0
    stored = json.loads(sidecar.read_text(encoding="utf-8"))["matte"]
    assert stored["count"] == 2 and len(stored["files"]) == 2
    projection_path = tmp_path / "projection.json"
    rc = ec.main(["project", str(model_path), "--out", str(projection_path),
                  "--width", "32", "--height", "32", "--fps", "10.0",
                  "--duration", "0.2", "--render-output", "matted_#####.png",
                  "--matte", str(sidecar),
                  "--project-id", "ec-conformance-matte"])
    assert rc == 0
    stored_request = json.loads(
        projection_path.read_text(encoding="utf-8"))["projection"]["request"]
    assert stored_request["operations"][-1]["op"] == "set_track_matte"


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


# ── Effect coverage: every Ren family is native or refused with a reason ──

def _effect_model(class_id):
    model = _fixture("model-effect.json")
    model["effects"][0]["class_id"] = class_id
    model["effects"][0]["parameters"] = {"probe": 1.0}
    model["effects"][0]["keyframes"] = []
    return model


@pytest.mark.parametrize("class_id", sorted(ec.REFUSED_EFFECT_COVERAGE))
def test_surveyed_but_unmapped_classes_refuse_with_their_reason(class_id):
    with pytest.raises(ec.UnknownEffect) as caught:
        ec.project_to_request(_effect_model(class_id), _comp(), _renders())
    assert class_id in str(caught.value)
    assert "Surveyed and deliberately unmapped" in str(caught.value)
    assert ec.refusal_reason(class_id) in str(caught.value)


def test_unsurveyed_class_refuses_without_a_survey_reason():
    with pytest.raises(ec.UnknownEffect) as caught:
        ec.project_to_request(_effect_model("neon.dreams"), _comp(),
                              _renders())
    assert "neon.dreams" in str(caught.value)
    assert "Surveyed and deliberately unmapped" not in str(caught.value)
    assert ec.refusal_reason("neon.dreams") is None


def test_refused_catalog_entries_cite_measurements_or_absence():
    # Every refusal carries its evidence: a dB/LSB/ratio number from
    # the pinned-CLI sweep that missed the mapping floor, or the
    # registry absence where no counterpart exists. The catalog has
    # no count pin: a legitimate addition extends the parametrized
    # refusal test above without touching this one.
    for class_id, reason in ec.REFUSED_EFFECT_COVERAGE.items():
        assert isinstance(reason, str) and len(reason) >= 100, class_id
        assert ("dB" in reason or "PSNR" in reason or "no counterpart"
                in reason or "no defocus effect" in reason
                or "no single pinned effect" in reason
                or "zero-mean" in reason), class_id


def test_plan_vfx_toolkit_all_projects_as_moves():
    from library.steps.step_4_03_plan_vfx.post_bridge import (
        TOOLKIT_PARAMETERS,
    )
    from library.tools.project_model_effectcraft_moves import (
        MOVE_CLASS_IDS,
    )
    # The toolkit is drift/punch/shake/reframe vocabulary, never engine
    # effects: a toolkit name added without a move projection fails here
    # until the moves module or the refusal catalog takes it.
    assert set(TOOLKIT_PARAMETERS) == {
        "slow_zoom_in", "slow_zoom_out", "zoom_emphasis", "screen_shake",
        "cut_in",
    }
    toolkit_to_move = {
        "slow_zoom_in": "move.slow_zoom_in",
        "slow_zoom_out": "move.slow_zoom_out",
        "zoom_emphasis": "move.zoom_emphasis",
        "screen_shake": "move.screen_shake",
        "cut_in": "move.cut_in",
    }
    for toolkit_name, move in toolkit_to_move.items():
        assert move in MOVE_CLASS_IDS, toolkit_name


def test_series_look_vocabulary_is_surveyed():
    from library.tools.series_look import LOOK_ELEMENTS
    # A look element added without a disposition fails here. Fusion-
    # delivered emits must resolve to a mapped or catalogued family;
    # CDL-delivered terms stay Resolve-side and are prose, not effects.
    assert {e.key for e in LOOK_ELEMENTS} == {
        "cdl", "contrast", "glow", "grain", "vignette",
        "exposure_reference",
    }
    fusion_emits_to_family = {
        "grade_contrast": "color.grade",
        "glow_gain": "glow.soft",
        "film_grain": "grain.film",
        "vignette": "vignette.ellipse",
    }
    by_key = {e.key: e for e in LOOK_ELEMENTS}
    for element in LOOK_ELEMENTS:
        if element.delivered_by != "fusion":
            continue
        for emit in element.emits:
            if emit in fusion_emits_to_family:
                family = fusion_emits_to_family[emit]
                assert (family in ec.REFUSED_EFFECT_COVERAGE
                        or family in ec.VERIFIED_EFFECT_MAP), emit
    assert by_key["contrast"].emits == ("grade_contrast",)
    assert set(by_key["glow"].required) == {"gain", "threshold", "size"}
    assert set(by_key["grain"].required) == {"power", "size"}


# ── Fade projection: opacity keys off the exact Fusion splines ──

def _fade_model(class_id, parameters, end=120):
    model = _fixture("model-effect.json")
    model["effects"][0]["class_id"] = class_id
    model["effects"][0]["parameters"] = parameters
    model["effects"][0]["keyframes"] = []
    model["effects"][0]["interval"] = {"clock_id": "clock-project",
                                       "start": 0, "end": end}
    return model


def _fade_keys(projection):
    return [(op["time"], op["value"])
            for op in projection.request["operations"]
            if op.get("op") == "keyframe"]


def test_fade_out_bakes_the_smooth_spline():
    # fx.fade's out-ramp holds near 1.0 through its midpoint handle
    # (1.0, not 0.5): frame 113 of a 12-frame tail is 0.6875, and no
    # assumed symmetry reproduces that - only sampling the spline.
    projection = ec.project_to_request(
        _fade_model("fade.opacity", {"fade_out_frames": 12}),
        ec.CompSpec(name="Main", width=32, height=32, frame_rate=10.0,
                    duration_seconds=12.0),
        _renders())
    keys = _fade_keys(projection)
    assert len(keys) == 120
    by_time = dict(keys)
    assert by_time[0.0] == pytest.approx(100.0)
    assert by_time[11.3] == pytest.approx(68.75)
    assert by_time[11.9] == pytest.approx(0.0)
    assert projection.identity_map["effects"]["effect-blur"] == {
        "layer": "checker.bmp", "fade": "fade.opacity",
        "paths": ["transform/opacity"], "frames": [0, 119]}


def test_fade_matches_the_builder_spline_key_for_key():
    # The projector samples fade_blend_spline, the exact function
    # fx.fade keys from: same keys in, same curve out.
    from library.tools.fusion.effects import fade_blend_spline
    spline = fade_blend_spline(0, 119, 8, 12)
    assert [(k.frame, k.value) for k in spline.keyframes] == [
        (0, 0.0), (8, 1.0), (107, 1.0), (119, 0.0)]


def test_transition_fade_to_black_bakes_the_linear_recipe():
    # The recipe's own endpoints (1.0 to 0.0 tail), read off the built
    # block, then baked linear - which is what linearize forces
    # Resolve to render.
    from library.tools.fusion.effects import _reset_counters, fx
    _reset_counters()
    block = fx.transition_tail(120, "fade_to_black", 12, res=(32, 32),
                               source_in=None, source_out=None)
    splines = [n for n in block.nodes
               if type(n).__name__ == "BezierSpline"]
    assert [(k.frame, k.value) for s in splines for k in s.keyframes] == [
        (107, 1.0), (119, 0.0)]
    projection = ec.project_to_request(
        _fade_model("transition.fade_to_black",
                    {"duration_frames": 12, "direction": "tail"}),
        ec.CompSpec(name="Main", width=32, height=32, frame_rate=10.0,
                    duration_seconds=12.0),
        _renders())
    keys = _fade_keys(projection)
    by_time = dict(keys)
    assert by_time[10.7] == pytest.approx(100.0)
    assert by_time[11.3] == pytest.approx(50.0)
    assert by_time[11.9] == pytest.approx(0.0)


def test_fade_refusals_name_the_ramp():
    bad_target = _fade_model("fade.opacity", {"fade_out_frames": 4})
    bad_target["effects"][0]["target_id"] = "mask-checker"
    with pytest.raises(ec.UnprojectableFade, match="no picture"):
        ec.project_to_request(bad_target, _comp(), _renders())
    keyed = _fade_model("fade.opacity", {"fade_out_frames": 4})
    keyed["effects"][0]["keyframes"] = [{"tick": 0, "value": 1.0}]
    with pytest.raises(ec.UnprojectableFade, match="double-driven"):
        ec.project_to_request(keyed, _comp(), _renders())
    empty = _fade_model("fade.opacity",
                        {"fade_in_frames": 0, "fade_out_frames": 0},
                        end=2)
    with pytest.raises(ec.UnprojectableFade, match="ramps nowhere"):
        ec.project_to_request(empty, _comp(), _renders())
    named = _fade_model("fade.opacity", {"fade_out_frames": 1,
                                         "strength": "lots"},
                        end=2)
    with pytest.raises(ec.UnprojectableFade, match="strength"):
        ec.project_to_request(named, _comp(), _renders())
    longs = _fade_model("transition.fade_to_black",
                        {"duration_frames": 99, "direction": "tail"},
                        end=2)
    with pytest.raises(ec.UnprojectableFade, match="never reaches"):
        ec.project_to_request(longs, _comp(), _renders())
    headed = _fade_model("transition.fade_to_black",
                         {"duration_frames": 1, "direction": "up"},
                         end=2)
    with pytest.raises(ec.UnprojectableFade, match="direction"):
        ec.project_to_request(headed, _comp(), _renders())
