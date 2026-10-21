"""MU-02: one mask-kind registry; EffectCraft reads migrated SAM masks.

Two behaviours the brief pins, plus the defect they close:

1. A fixture model produced by ``_migrate_segmentation`` from a
   real-shaped 1.06 file rasterizes through ``write_matte_sequence``
   into frames whose pixels equal the decoded masks. On main this
   refuses with the unknown-kind error.
2. Every kind any producer writes is accepted by the registry. This
   names the producer/consumer drift defect (the migration wrote
   ``sam2_rle_track`` while the matte writer refused it), not a count:
   a legitimate new kind extends the registry and the on-disk models
   without touching this test's shape.
"""

from __future__ import annotations

import json
import hashlib
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from library.tools import person_regions as pr
from library.tools import project_model_effectcraft as ec
from library.tools import project_model_evidence as evidence
from library.tools import project_model_masks as masks
from library.tools.analysis.object_segmentation import encode_rle
from library.tools.project_layout import ProjectLayout
from library.tools.project_model import validate_project_model

ROOT = Path(__file__).resolve().parents[3]
DIGEST = "digest-sam2-001"
WIDTH, HEIGHT = 8, 8
SAMPLE_FPS = 2.0
FRAMES = [0, 1, 2, 3]


def _sample_masks() -> list[np.ndarray]:
    """Four distinct 8x8 masks: held-frame mistakes show as pixels."""
    top = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    top[:4, :4] = 1
    bottom = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    bottom[4:, 4:] = 1
    cross = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    cross[3:5, :] = 1
    cross[:, 3:5] = 1
    corners = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    corners[0, 0] = 1
    corners[0, 7] = 1
    corners[7, 0] = 1
    corners[7, 7] = 1
    return [top, bottom, cross, corners]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "proj"
    memory = tmp_path / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    _write(project / "pipeline_data.json", {
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001",
                 "source_file": "/nonexistent/sam2.mov",
                 "path": "/nonexistent/sam2.mov"},
            ]}},
        "source_fingerprints": {
            "clip_001": {"path": "/nonexistent/sam2.mov",
                         "content_digest": DIGEST}},
    })
    seg = {
        "video_path": "/nonexistent/sam2.mov",
        "frame_count": 4,
        "resolution": [HEIGHT, WIDTH],
        "sample_fps": SAMPLE_FPS,
        "seed_note": "auto",
        "objects": [{
            "object_id": "obj_1",
            "label": "auto_object_1",
            "category": "unknown",
            "frames": list(FRAMES),
            "masks_rle": {str(i): encode_rle(mask)
                          for i, mask in enumerate(_sample_masks())},
            "bboxes": {str(i): [0, 0, WIDTH, HEIGHT] for i in FRAMES},
            "avg_area_ratio": 0.25,
        }],
    }
    seg_dir = ProjectLayout(str(project)).step_dir(
        "object_segmentation", create=True)
    _write(seg_dir / "clip_001_segmentation.json", seg)
    return {"project": project, "memory": memory}


def _read_matte_alpha(path: str) -> bytes:
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
    return bytes(alpha)


def _migrated_sam2_mask(tmp_path: Path) -> tuple[dict, dict, Path, Path]:
    paths = _fixture_project(tmp_path)
    model, _report = evidence.migrate_project_evidence(
        str(paths["project"]), memory_root=str(paths["memory"]))
    validate_project_model(model)
    mask = next(m for m in model["masks"]
                if m["id"] == "mask-clip_001-obj_1")
    assert mask["representation"]["kind"] == masks.SAM2_RLE_TRACK_KIND
    return model, mask, paths["project"], paths["memory"]


def _region_mask_row(tmp_path: Path, memory: Path, entity: str, clock: str,
                     provenance_id: str) -> dict:
    """A ``region_rle`` row over a real sidecar: one 8x8 sample written
    by the lane's own sidecar writer, so the registry resolves what a
    producer actually writes."""
    grid = [WIDTH, HEIGHT]
    blob = np.zeros((HEIGHT, WIDTH), dtype=bool)
    blob[:4, :4] = True
    ref = pr.write_sidecar(memory / DIGEST, [pr.encode_runs(blob)], grid)
    return {
        "id": "mask-drift-region", "entity_id": entity,
        "interval": {"clock_id": clock, "start": 0, "end": 2000},
        "representation": {
            "kind": "region_rle", "content_digest": DIGEST,
            "sidecar_file": ref["file"], "sidecar_sha256": ref["sha256"],
            "codec": ref["codec"], "grid": grid,
            "track_id": "region_001",
            "sample_ticks": [0], "mask_indices": [0]},
        "source_model": "apple_vision_person_instance_mask",
        "provenance_id": provenance_id,
    }


def test_migrated_sam2_masks_rasterize_to_the_decoded_pixels(tmp_path):
    """The migrated SAM track reaches matte frames pixel-identical to
    the 1.06 masks: numpy mask -> encode -> file -> migrate ->
    resolve -> decode -> PNG alpha, compared against the numpy
    originals, never against another RLE decode."""
    model, mask, project, _memory = _migrated_sam2_mask(tmp_path)
    comp = ec.CompSpec(name="Main", width=WIDTH, height=HEIGHT,
                       frame_rate=10.0, duration_seconds=2.0)
    sequence = ec.write_matte_sequence(
        mask, model, comp, tmp_path / "mattes", project_folder=project)
    assert (sequence.first_frame, sequence.count) == (0, 20)
    originals = _sample_masks()
    for frame, path in enumerate(sequence.files):
        tick = frame * 100
        held = min(tick // 500, 3)
        expected = bytes((originals[held].flatten() * 255).tolist())
        assert _read_matte_alpha(path) == expected, (
            f"comp frame {frame} (tick {tick}) holds sample {held}")


def _model_paths() -> list[Path]:
    found = sorted(
        (ROOT / "tests/fixtures/effectcraft_fframes_conformance").glob(
            "model-*.json"))
    e2e = ROOT / "tests/fixtures/project_model_e2e/expected-project-model.json"
    return found + ([e2e] if e2e.is_file() else [])


def test_producer_consumer_drift_every_produced_kind_rasterizes(tmp_path):
    """Producer/consumer drift: every mask kind found in a produced
    model - the migrated SAM migration plus every checked-in
    conformance and contract fixture - is a kind the registry lists,
    and every listed kind rasterizes through the consumer instead of
    refusing as unknown."""
    produced: set[str] = set()
    model, sam2_mask, project, memory = _migrated_sam2_mask(tmp_path)
    produced.add(sam2_mask["representation"]["kind"])
    for path in _model_paths():
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in doc.get("masks") or []:
            kind = (row.get("representation") or {}).get("kind")
            if isinstance(kind, str):
                produced.add(kind)
    assert produced, "the fixtures hold no masks to drift on"
    assert produced <= set(masks.MASK_KINDS), (
        f"producer wrote what the registry cannot read: "
        f"{sorted(produced - set(masks.MASK_KINDS))}")
    comp = ec.CompSpec(name="Main", width=WIDTH, height=HEIGHT,
                       frame_rate=10.0, duration_seconds=2.0)
    out_dir = tmp_path / "mattes"
    sequence = ec.write_matte_sequence(
        sam2_mask, model, comp, out_dir, project_folder=project)
    assert sequence.count == 20
    entity = sam2_mask["entity_id"]
    clock = sam2_mask["interval"]["clock_id"]
    extra = [
        {"id": "mask-drift-ellipse", "entity_id": entity,
         "interval": {"clock_id": clock, "start": 0, "end": 2000},
         "representation": {
             "kind": "ellipse", "normalized_bbox": [0.0, 0.0, 0.5, 0.5],
             "keyframes": [{"tick": 0, "bbox": [0.0, 0.0, 0.5, 0.5]},
                           {"tick": 2000, "bbox": [0.5, 0.5, 0.5, 0.5]}]},
         "source_model": "fixture",
         "provenance_id": sam2_mask["provenance_id"]},
        {"id": "mask-drift-rect", "entity_id": entity,
         "interval": {"clock_id": clock, "start": 0, "end": 2000},
         "representation": {
             "kind": "rect", "normalized_bbox": [0.0, 0.0, 0.5, 0.5],
             "keyframes": [{"tick": 0, "bbox": [0.0, 0.0, 0.5, 0.5]},
                           {"tick": 2000, "bbox": [0.5, 0.5, 0.5, 0.5]}]},
         "source_model": "fixture",
         "provenance_id": sam2_mask["provenance_id"]},
        {"id": "mask-drift-face", "entity_id": entity,
         "interval": {"clock_id": clock, "start": 0, "end": 2000},
         "representation": {
             "kind": "face_bbox_track", "face_track": "drift",
             "samples": [{"t": 0.0, "box": [0.0, 0.0, 0.5, 0.5]},
                         {"t": 1.9, "box": [0.0, 0.0, 0.5, 0.5]}]},
         "source_model": "fixture",
         "provenance_id": sam2_mask["provenance_id"]},
        {"id": "mask-drift-rle", "entity_id": entity,
         "interval": {"clock_id": clock, "start": 0, "end": 2000},
         "representation": {
             "kind": "rle", "width": WIDTH, "height": HEIGHT,
             "frames": [{"tick": 0, "rle": "0 4"}]},
         "source_model": "fixture",
         "provenance_id": sam2_mask["provenance_id"]},
    ]
    model["masks"].extend(extra)
    model["masks"].append(_region_mask_row(
        tmp_path, memory, entity, clock, sam2_mask["provenance_id"]))
    from library.tools.project_layout import ProjectLayout
    ocr_artifact = (ProjectLayout(str(project)).step_dir(
        "ocr_extraction", create=True) / "clip_ocr" / "ocr_result.json")
    ocr_artifact.parent.mkdir(parents=True, exist_ok=True)
    ocr_payload = {
        "schema_version": 2, "resolution": [WIDTH, HEIGHT],
        "detections": [{"track_id": "text-0001", "samples": [{
            "timestamp": 0.0, "bbox_normalized": [0.1, 0.2, 0.3, 0.2],
            "polygon_normalized": [[0.1, 0.2], [0.4, 0.2],
                                   [0.4, 0.4], [0.1, 0.4]],
        }]}], "graphics": [],
    }
    ocr_bytes = json.dumps(ocr_payload).encode("utf-8")
    ocr_artifact.write_bytes(ocr_bytes)
    model["masks"].append({
        "id": "mask-drift-ocr", "entity_id": entity,
        "interval": {"clock_id": clock, "start": 0, "end": 1000},
        "representation": {
            "kind": masks.OCR_POLYGON_TRACK_KIND,
            "content_digest": "fixture-ocr", "track_id": "text-0001",
            "artifact_path": ocr_artifact.relative_to(project).as_posix(),
            "artifact_sha256": hashlib.sha256(ocr_bytes).hexdigest(),
            "sample_ticks": [0], "sample_indices": [0],
            "coordinate_space": "source-normalised-tl",
        },
        "source_model": "fixture-ocr",
        "provenance_id": sam2_mask["provenance_id"],
    })
    validate_project_model(model)
    kinds = {row["representation"]["kind"] for row in extra}
    kinds.add("region_rle")
    kinds.add(masks.OCR_POLYGON_TRACK_KIND)
    assert kinds | produced == set(masks.MASK_KINDS)
    width, height, ocr_frames = masks.ocr_polygon_track_frames(
        model["masks"][-1]["representation"], project_folder=project)
    assert (width, height, ocr_frames) == (
        WIDTH, HEIGHT, [(0, [[0.1, 0.2], [0.4, 0.2],
                             [0.4, 0.4], [0.1, 0.4]])])
    for row in extra:
        rasterized = ec.write_matte_sequence(
            row, model, comp, out_dir / row["id"], project_folder=project)
        assert rasterized.count == 20
        assert len(rasterized.files) == 20
    region = next(m for m in model["masks"]
                  if m["representation"].get("kind") == "region_rle")
    rasterized = ec.write_matte_sequence(
        region, model, comp, out_dir / region["id"],
        project_folder=project, memory_root=memory)
    assert rasterized.count == 20
    assert len(rasterized.files) == 20


def test_same_object_id_in_two_clips_resolves_per_file(tmp_path):
    """OBJ-021: an object id is clip-local. Two segmentation files both
    carrying ``obj_1`` resolve through their own file reference, so the
    same bare id never reunites two clips' objects; a reference naming
    no file refuses instead of guessing one."""
    first = tmp_path / "clip_a_segmentation.json"
    second = tmp_path / "clip_b_segmentation.json"
    _write(first, {
        "video_path": "/nonexistent/a.mov",
        "frame_count": 4,
        "resolution": [HEIGHT, WIDTH],
        "sample_fps": SAMPLE_FPS,
        "objects": [{
            "object_id": "obj_1",
            "label": "laptop",
            "category": "laptop",
            "frames": [0, 1],
            "masks_rle": {str(i): encode_rle(mask)
                          for i, mask in enumerate(_sample_masks()[:2])},
            "bboxes": {str(i): [0, 0, WIDTH, HEIGHT] for i in (0, 1)},
            "avg_area_ratio": 0.25,
        }],
    })
    _write(second, {
        "video_path": "/nonexistent/b.mov",
        "frame_count": 4,
        "resolution": [HEIGHT, WIDTH],
        "sample_fps": SAMPLE_FPS,
        "objects": [{
            "object_id": "obj_1",
            "label": "person",
            "category": "person",
            "frames": [2, 3],
            "masks_rle": {str(i + 2): encode_rle(mask)
                          for i, mask in enumerate(_sample_masks()[2:])},
            "bboxes": {str(i): [0, 0, WIDTH, HEIGHT] for i in (2, 3)},
            "avg_area_ratio": 0.25,
        }],
    })
    rep_a = {"kind": masks.SAM2_RLE_TRACK_KIND,
             "segmentation_file": str(first),
             "object_id": "obj_1", "sample_fps": SAMPLE_FPS}
    rep_b = {"kind": masks.SAM2_RLE_TRACK_KIND,
             "segmentation_file": str(second),
             "object_id": "obj_1", "sample_fps": SAMPLE_FPS}
    width_a, height_a, frames_a = masks.sam2_track_frames(rep_a)
    width_b, height_b, frames_b = masks.sam2_track_frames(rep_b)
    assert (width_a, height_a) == (WIDTH, HEIGHT)
    assert [tick for tick, _blob in frames_a] == [0, 500]
    assert [tick for tick, _blob in frames_b] == [1000, 1500]
    assert [blob for _tick, blob in frames_a] != [
        blob for _tick, blob in frames_b]
    with pytest.raises(masks.UnresolvableMask):
        masks.sam2_track_frames({"kind": masks.SAM2_RLE_TRACK_KIND,
                                 "object_id": "obj_1",
                                 "sample_fps": SAMPLE_FPS})


def test_unknown_mask_kind_still_refuses_with_the_registry_listed(tmp_path):
    """Fail-closed is kept: a kind no producer writes refuses, and the
    refusal lists the registry so the next producer/consumer drift
    reads as a name, not a mystery.

    The refusal now happens where the model is written: a mask nobody
    can read is refused at model validation, before anything tries to
    project it."""
    model, mask, _project, _memory = _migrated_sam2_mask(tmp_path)
    mask["representation"] = {"kind": "magic_mask"}
    comp = ec.CompSpec(name="Main", width=WIDTH, height=HEIGHT,
                       frame_rate=10.0, duration_seconds=2.0)
    with pytest.raises(ValueError) as caught:
        validate_project_model(model)
    for kind in masks.MASK_KINDS:
        assert kind in str(caught.value)
    with pytest.raises(ValueError):
        ec.write_matte_sequence(mask, model, comp, tmp_path / "mattes")
