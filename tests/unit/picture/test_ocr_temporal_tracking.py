"""Synthetic screen-recording proxies exercise OCR/UI temporal plumbing.

They measure serialization, candidate geometry and state changes only. They
do not establish real tutorial or screen-recording accuracy.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from library.tools.analysis.ocr_extractor import (
    OCRExtractor, OCRResult, TextDetection,
)
from library.tools.ocr_project_model import migrate_ocr_artifact
from library.tools.project_layout import ProjectLayout
from library.tools.project_model import validate_project_model
from library.tools.project_model_evidence import _Builder
from library.tools.project_model_masks import (
    UnresolvableMask, ocr_polygon_track_frames,
)

FIXTURE = (Path(__file__).resolve().parents[2]
           / "fixtures/video_intelligence/screen_recording_proxy.json")
DIGEST = "sha256:synthetic-screen-proxy"


def _frames():
    labels = json.loads(FIXTURE.read_text(encoding="utf-8"))["frames"]
    frames = []
    for index, item in enumerate(labels):
        frame = np.full((100, 160, 3), 235, dtype=np.uint8)
        frame[0, 0, 0] = index
        color = (220, 90, 20) if item["control_state"] == "blue" else (30, 190, 35)
        cv2.rectangle(frame, (12, 18), (72, 42), color, thickness=-1)
        cv2.rectangle(frame, (12, 18), (72, 42), (10, 10, 10), thickness=2)
        cx = int(item["cursor_center"][0] * frame.shape[1])
        cy = int(item["cursor_center"][1] * frame.shape[0])
        cursor = np.array([[cx, cy], [cx, cy + 13], [cx + 8, cy + 8]], dtype=np.int32)
        cv2.fillPoly(frame, [cursor], (0, 0, 0))
        cv2.polylines(frame, [cursor], True, (255, 255, 255), 1)
        frames.append(frame)
    return labels, frames


def test_synthetic_screen_recording_tracks_text_graphics_and_cursor(monkeypatch, tmp_path):
    labels, frames = _frames()

    class FakeCapture:
        def __init__(self, _path):
            self.index = 0

        def isOpened(self):
            return True

        def get(self, key):
            return {cv2.CAP_PROP_FPS: 1.0,
                    cv2.CAP_PROP_FRAME_COUNT: len(frames),
                    cv2.CAP_PROP_FRAME_WIDTH: 160,
                    cv2.CAP_PROP_FRAME_HEIGHT: 100}.get(key, 0)

        def set(self, _key, value):
            self.index = int(value)

        def read(self):
            return True, frames[self.index].copy()

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    extractor = OCRExtractor()

    def read_text(_self, frame, backend="easyocr"):
        i = int(frame[0, 0, 0])
        text = labels[i]["text"]
        return [TextDetection(
            text, 0.95, (14, 22, 42, 14), (0.0875, 0.22, 0.2625, 0.14),
            ((0.0875, 0.22), (0.35, 0.22), (0.35, 0.36), (0.0875, 0.36)),
        )]

    monkeypatch.setattr(OCRExtractor, "extract_text_from_frame", read_text)
    result = extractor.extract_text_from_clip("synthetic-proxy.mov", sample_fps=1.0)
    assert [row.text for row in result.detections] == ["Save", "Saved"]
    assert result.detections[0].track_id == "text-0001"
    assert [sample["timestamp"] for sample in result.detections[0].samples] == [0.0, 1.0]
    assert result.detections[0].language is None  # Latin OCR is not language identification.
    assert result.detections[0].alternatives == []
    assert result.detections[0].appearance_intervals == [{
        "start_seconds": 0.0, "end_seconds": 2.0,
        "boundary_basis": "sampled_presence_lower_bound",
        "end_exclusive": True,
    }]
    assert any(row["kind"] == "ui_control_candidate" for row in result.graphics)
    cursor_track = next(row for row in result.graphics if row["kind"] == "cursor_candidate")
    assert len(cursor_track["samples"]) == 3
    control_track = next(row for row in result.graphics if row["kind"] == "ui_control_candidate")
    assert len({row["visual_state"] for row in control_track["samples"]}) == 2
    assert len(result.graphics) == 2
    assert sum(len(row["samples"]) for row in result.graphics) == 6
    assert sum(len(row.samples) for row in result.detections) == 3

    output = tmp_path / "ocr"
    result.save(str(output))
    round_trip = OCRResult.load(str(output))
    assert round_trip.graphics == result.graphics
    assert round_trip.detections[0].samples == result.detections[0].samples


def test_easyocr_reader_never_downloads_model_weights(monkeypatch):
    calls = []
    class FakeReader:
        pass
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(
        Reader=lambda *args, **kwargs: calls.append((args, kwargs)) or FakeReader()))
    extractor = OCRExtractor()
    extractor.reader = None
    try:
        assert isinstance(extractor._get_reader(), FakeReader)
        assert calls == [((['en'],), {"download_enabled": False})]
    finally:
        extractor.reader = None


def test_frame_read_preserves_polygon_alternatives_and_script_hint():
    class StubReader:
        def readtext(self, _frame):
            return [([(3, 2), (24, 2), (24, 12), (3, 12)], "Привет", 0.8,
                     [{"text": "Привеτ", "confidence": 0.4}])]

    extractor = OCRExtractor()
    previous = extractor.reader
    extractor.reader = StubReader()
    try:
        detection, = extractor.extract_text_from_frame(np.zeros((20, 30, 3), dtype=np.uint8))
    finally:
        extractor.reader = previous
    assert detection.language == "und-Cyrl"
    assert detection.alternatives == [{"text": "Привеτ", "confidence": 0.4}]
    assert detection.polygon_normalized[0] == (0.1, 0.1)


def test_ocr_tracks_and_masks_migrate_with_content_checked_artifact_refs(tmp_path):
    project = tmp_path / "project"
    layout = ProjectLayout(str(project))
    artifact = layout.step_dir("ocr_extraction", create=True) / "clip_demo" / "ocr_result.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({
        "schema_version": 2, "resolution": [160, 100], "sample_fps": 1.0,
        "detections": [{
            "track_id": "text-0001", "text": "Save", "language": None,
            "alternatives": [], "appearances": [[0.0, 1.0]],
            "samples": [{"timestamp": 0.0, "frame_index": 0,
                         "bbox_normalized": [0.1, 0.2, 0.3, 0.1],
                         "polygon_normalized": [[0.1, 0.2], [0.4, 0.2], [0.4, 0.3], [0.1, 0.3]],
                         "confidence": 0.95}],
        }],
        "graphics": [{
            "track_id": "graphic-0001", "kind": "ui_control_candidate",
            "appearance_intervals": [[0.0, 1.0]],
            "samples": [{"timestamp": 0.0, "frame_index": 0,
                         "bbox_normalized": [0.05, 0.1, 0.4, 0.2],
                         "polygon_normalized": [[0.05, 0.1], [0.45, 0.1], [0.45, 0.3], [0.05, 0.3]],
                         "confidence": None, "visual_state": "20,90,220"}],
        }],
    }), encoding="utf-8")

    builder = _Builder("project-proxy")
    builder.model["assets"].append({
        "id": f"asset-{DIGEST}", "kind": "video", "uri": "synthetic-proxy.mov",
        "content_digest": DIGEST, "metadata": {}, "provenance": [],
    })
    builder.model["clocks"].append({
        "id": f"clock-{DIGEST}", "domain": {"kind": "source", "asset_id": f"asset-{DIGEST}"},
        "ticks_per_second": 1000, "frame_rate": None,
    })
    assert migrate_ocr_artifact(builder, project, "clip_demo", DIGEST) == 2
    assert {track["kind"] for track in builder.model["tracks"]} == {"ocr_text", "ui_control_candidate"}
    assert len(builder.model["observations"]) == 2
    assert len(builder.model["masks"]) == 2
    model = json.loads((Path(__file__).resolve().parents[2]
                        / "fixtures/project_model_e2e/expected-project-model.json").read_text(encoding="utf-8"))
    model["id"] = "project-proxy"
    model["assets"].append(builder.model["assets"][0])
    model["clocks"].append(builder.model["clocks"][0])
    model["streams"].append({
        "id": f"stream-{DIGEST}", "asset_id": f"asset-{DIGEST}",
        "stream_index": 0, "clock_id": f"clock-{DIGEST}",
        "metadata": {"codec_type": "video"},
    })
    for key in ("entities", "observations", "tracks", "masks", "provenance"):
        model[key].extend(builder.model[key])
    validate_project_model(model)
    evidence = builder.model["observations"][0]["payload"]["evidence_contract"]
    # OCR is a MODEL's reading of the frame, so the record is an
    # inference and carries the uncertainty the OCR score earned.
    assert evidence["evidence_type"] == "inference"
    assert evidence["certainty"] == "inferred"
    assert evidence["uncertainty"] is not None
    assert evidence["measurement_type"] == "image.ocr_text_track"
    assert evidence["coverage"] == "sampled"
    assert evidence["references"] == [{
        "kind": "element", "stream_id": f"stream-{DIGEST}",
        "clock_id": f"clock-{DIGEST}",
        "element": {"kind": "frame", "frame_index": 0},
    }]

    reference = builder.model["masks"][0]["representation"]
    width, height, frames = ocr_polygon_track_frames(reference, project_folder=project)
    assert (width, height) == (160, 100)
    assert frames == [(0, [[0.1, 0.2], [0.4, 0.2], [0.4, 0.3], [0.1, 0.3]])]
    artifact.write_text("{}", encoding="utf-8")
    with pytest.raises(UnresolvableMask, match="hash changed"):
        ocr_polygon_track_frames(reference, project_folder=project)


def test_ungrounded_vlm_text_stays_searchable_without_geometry_tracks(tmp_path):
    """A text-only VLM track migrates no geometry (GEO-025 honesty).

    The artifact remains the searchable authority (footage_query reads
    it directly); the migration records one note instead of placing a
    full-frame box nobody measured.
    """
    project = tmp_path / "project"
    layout = ProjectLayout(str(project))
    artifact = layout.step_dir("ocr_extraction", create=True) / "clip_vlm" / "ocr_result.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({
        "schema_version": 2, "resolution": [160, 100], "sample_fps": 1.0,
        "method": "PaddleOCR-VL-1.6-MLX", "precision": "bfloat16 (MLX, unquantized)",
        "model_revision": "test-revision",
        "detections": [{
            "track_id": "text-0001", "text": "Save", "language": None,
            "alternatives": [], "appearances": [[0.0, 1.0]],
            "spatially_grounded": False,
            "samples": [{"timestamp": 0.0, "frame_index": 0,
                         "bbox_normalized": [0.0, 0.0, 1.0, 1.0],
                         "polygon_normalized": [],
                         "confidence": 1.0}],
        }],
        "graphics": [],
    }), encoding="utf-8")

    builder = _Builder("project-proxy")
    builder.model["assets"].append({
        "id": f"asset-{DIGEST}", "kind": "video", "uri": "synthetic-proxy.mov",
        "content_digest": DIGEST, "metadata": {}, "provenance": [],
    })
    builder.model["clocks"].append({
        "id": f"clock-{DIGEST}", "domain": {"kind": "source", "asset_id": f"asset-{DIGEST}"},
        "ticks_per_second": 1000, "frame_rate": None,
    })
    assert migrate_ocr_artifact(builder, project, "clip_vlm", DIGEST) == 0
    assert builder.model["tracks"] == []
    assert any("spatially ungrounded" in note for note in builder.notes)
