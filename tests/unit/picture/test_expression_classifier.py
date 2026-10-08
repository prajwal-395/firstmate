"""Tests for the expression classifier (M3c).

Each test names a behavioral defect the fix prevents.
"""

import json
from pathlib import Path

import pytest

from library.tools import expression_classifier as ec


def _smile_lips():
    """Outer lip landmarks forming a smile (corners up, lower y in image coords)."""
    return [[0.1, 0.15], [0.3, 0.2], [0.5, 0.3], [0.7, 0.2], [0.9, 0.15]]


def _frown_lips():
    """Outer lip landmarks forming a frown (corners down, higher y in image coords)."""
    return [[0.1, 0.3], [0.3, 0.2], [0.5, 0.15], [0.7, 0.2], [0.9, 0.3]]


def _neutral_lips():
    """Outer lip landmarks forming a neutral mouth (flat)."""
    return [[0.1, 0.2], [0.3, 0.2], [0.5, 0.2], [0.7, 0.2], [0.9, 0.2]]


def _open_mouth_inner():
    """Inner lip landmarks forming an open mouth."""
    return [[0.3, 0.1], [0.5, 0.1], [0.7, 0.1], [0.3, 0.4], [0.5, 0.4], [0.7, 0.4]]


def _closed_mouth_inner():
    """Inner lip landmarks forming a closed mouth."""
    return [[0.3, 0.2], [0.5, 0.2], [0.7, 0.2], [0.3, 0.25], [0.5, 0.25], [0.7, 0.25]]


class TestMouthCurvature:
    """Mouth curvature is computed with the correct sign."""

    def test_smile_has_positive_curvature(self):
        """Defect: curvature sign flipped, smiles read as frowns."""
        curvature = ec.mouth_curvature(_smile_lips())
        assert curvature > 0, f"smile curvature should be positive, got {curvature}"

    def test_frown_has_negative_curvature(self):
        """Defect: curvature sign flipped, frowns read as smiles."""
        curvature = ec.mouth_curvature(_frown_lips())
        assert curvature < 0, f"frown curvature should be negative, got {curvature}"

    def test_neutral_has_near_zero_curvature(self):
        """Defect: neutral mouth produces spurious curvature."""
        curvature = ec.mouth_curvature(_neutral_lips())
        assert abs(curvature) < 0.01, f"neutral curvature should be ~0, got {curvature}"

    def test_curvature_is_scale_invariant(self):
        """Defect: curvature depends on absolute coordinates, not shape."""
        small = [[0.1, 0.3], [0.3, 0.2], [0.5, 0.15], [0.7, 0.2], [0.9, 0.3]]
        large = [[1.0, 3.0], [3.0, 2.0], [5.0, 1.5], [7.0, 2.0], [9.0, 3.0]]
        c_small = ec.mouth_curvature(small)
        c_large = ec.mouth_curvature(large)
        assert abs(c_small - c_large) < 0.01, (
            f"curvature should be scale-invariant: {c_small} vs {c_large}")


class TestMouthOpenness:
    """Mouth openness is computed correctly."""

    def test_open_mouth_has_high_openness(self):
        """Defect: openness not computed, surprise never detected."""
        openness = ec.mouth_openness(_neutral_lips(), _open_mouth_inner())
        assert openness > 0.3, f"open mouth openness should be high, got {openness}"

    def test_closed_mouth_has_low_openness(self):
        """Defect: closed mouth reads as open, false surprise."""
        openness = ec.mouth_openness(_neutral_lips(), _closed_mouth_inner())
        assert openness < 0.2, f"closed mouth openness should be low, got {openness}"

    def test_openness_is_scale_invariant(self):
        """Defect: openness depends on absolute coordinates."""
        small_outer = [[0.1, 0.2], [0.5, 0.2], [0.9, 0.2]]
        small_inner = [[0.3, 0.1], [0.5, 0.1], [0.7, 0.1],
                       [0.3, 0.4], [0.5, 0.4], [0.7, 0.4]]
        large_outer = [[1.0, 2.0], [5.0, 2.0], [9.0, 2.0]]
        large_inner = [[3.0, 1.0], [5.0, 1.0], [7.0, 1.0],
                       [3.0, 4.0], [5.0, 4.0], [7.0, 4.0]]
        o_small = ec.mouth_openness(small_outer, small_inner)
        o_large = ec.mouth_openness(large_outer, large_inner)
        assert abs(o_small - o_large) < 0.01, (
            f"openness should be scale-invariant: {o_small} vs {o_large}")


class TestClassifyExpression:
    """Expression classification maps features to correct labels."""

    def test_smile_classified_as_happy(self):
        """Defect: smile classified as neutral, expression not detected."""
        features = {"mouth_curvature": 0.05, "mouth_openness": 0.1,
                    "eye_edge_density": 0.05, "nose_edge_density": 0.02}
        expr, conf = ec.classify_expression(features)
        assert expr == "happy", f"smile should be happy, got {expr}"
        assert conf > 0.5

    def test_frown_classified_as_sad(self):
        """Defect: frown classified as neutral, expression not detected."""
        features = {"mouth_curvature": -0.05, "mouth_openness": 0.1,
                    "eye_edge_density": 0.05, "nose_edge_density": 0.02}
        expr, conf = ec.classify_expression(features)
        assert expr == "sad", f"frown should be sad, got {expr}"
        assert conf > 0.5

    def test_open_mouth_classified_as_surprised(self):
        """Defect: open mouth classified as neutral, surprise missed."""
        features = {"mouth_curvature": 0.0, "mouth_openness": 0.5,
                    "eye_edge_density": 0.05, "nose_edge_density": 0.02}
        expr, conf = ec.classify_expression(features)
        assert expr == "surprised", f"open mouth should be surprised, got {expr}"
        assert conf > 0.5

    def test_neutral_classified_as_neutral(self):
        """Defect: neutral face misclassified as an expression."""
        features = {"mouth_curvature": 0.0, "mouth_openness": 0.1,
                    "eye_edge_density": 0.05, "nose_edge_density": 0.02}
        expr, conf = ec.classify_expression(features)
        assert expr == "neutral", f"neutral should be neutral, got {expr}"

    def test_high_eye_density_classified_as_angry(self):
        """Defect: furrowed brows not detected, anger missed."""
        features = {"mouth_curvature": 0.0, "mouth_openness": 0.1,
                    "eye_edge_density": 0.18, "nose_edge_density": 0.02}
        expr, conf = ec.classify_expression(features)
        assert expr == "angry", f"high eye density should be angry, got {expr}"

    def test_high_nose_density_classified_as_disgusted(self):
        """Defect: nose wrinkling not detected, disgust missed."""
        features = {"mouth_curvature": 0.0, "mouth_openness": 0.1,
                    "eye_edge_density": 0.05, "nose_edge_density": 0.25}
        expr, conf = ec.classify_expression(features)
        assert expr == "disgusted", f"high nose density should be disgusted, got {expr}"


class TestClassifyFace:
    """classify_face combines landmarks and image correctly."""

    def test_classify_face_with_landmarks_only(self):
        """Defect: classifier fails when no crop image is available."""
        result = ec.classify_face(_smile_lips(), _closed_mouth_inner(), None)
        assert result["expression"] == "happy"
        assert result["confidence"] > 0.5
        assert "features" in result

    def test_classify_face_with_open_mouth(self):
        """Defect: open mouth not detected from landmarks."""
        result = ec.classify_face(_neutral_lips(), _open_mouth_inner(), None)
        assert result["expression"] == "surprised"

    def test_classify_face_returns_all_fields(self):
        """Defect: classify_face missing required output fields."""
        result = ec.classify_face(_smile_lips(), _closed_mouth_inner(), None)
        assert "expression" in result
        assert "confidence" in result
        assert "features" in result
        assert "mouth_curvature" in result["features"]
        assert "mouth_openness" in result["features"]


class TestSlotConstants:
    """Slot constants are correctly defined."""

    def test_slot_expressions_defined(self):
        """Defect: SLOT_EXPRESSIONS not defined in source_memory."""
        from library.tools import source_memory
        assert hasattr(source_memory, "SLOT_EXPRESSIONS")
        assert source_memory.SLOT_EXPRESSIONS == "expressions.json"

    def test_expressions_not_in_reserved(self):
        """Defect: expressions slot still marked as reserved."""
        from library.tools import source_memory
        reserved = getattr(source_memory, "RESERVED_SLOTS", ())
        assert source_memory.SLOT_EXPRESSIONS not in reserved


class TestLaneWiring:
    """The expressions lane is wired into the analysis pipeline."""

    def test_expressions_lane_exists(self):
        """Defect: expressions lane not registered in footage_analysis."""
        from library.tools import footage_analysis
        lane_names = [lane.name for lane in footage_analysis.LANES]
        assert "expressions" in lane_names

    def test_expressions_lane_reads_persons(self):
        """Defect: expressions lane does not depend on M3."""
        from library.tools import footage_analysis, source_memory
        lane = next(l for l in footage_analysis.LANES if l.name == "expressions")
        assert source_memory.SLOT_PERSONS in lane.reads

    def test_expressions_lane_writes_expressions(self):
        """Defect: expressions lane writes wrong slot."""
        from library.tools import footage_analysis, source_memory
        lane = next(l for l in footage_analysis.LANES if l.name == "expressions")
        assert source_memory.SLOT_EXPRESSIONS in lane.slots


class TestMemoryExport:
    """The expressions slot is wired into memory export."""

    def test_expressions_in_writers(self):
        """Defect: expressions slot missing from export writers."""
        from library.tools import memory_export, source_memory
        assert source_memory.SLOT_EXPRESSIONS in memory_export.WRITERS

    def test_expressions_in_section_lane(self):
        """Defect: expressions section not mapped to a lane."""
        from library.tools import memory_export
        assert "expressions" in memory_export.SECTION_LANE

    def test_expressions_in_owed_sections(self):
        """Defect: expressions not listed as an owed section."""
        from library.tools import memory_export
        assert "expressions" in memory_export.SECTION_LANE


class TestReadM3C:
    """read_m3c reads the expressions slot correctly."""

    def test_read_m3c_missing(self, tmp_path):
        """Defect: read_m3c crashes on missing slot."""
        result = ec.read_m3c("nonexistent_digest", tmp_path)
        assert result is None

    def test_read_m3c_wrong_digest(self, tmp_path):
        """Defect: read_m3c returns record with wrong digest."""
        from library.tools import source_memory
        sdir = source_memory.source_dir("some_digest", tmp_path)
        sdir.mkdir(parents=True, exist_ok=True)
        record = {"content_digest": "different_digest", "status": "measured"}
        (sdir / source_memory.SLOT_EXPRESSIONS).write_text(json.dumps(record))
        result = ec.read_m3c("some_digest", tmp_path)
        assert result is None

    def test_read_m3c_valid(self, tmp_path):
        """Defect: read_m3c fails on valid record."""
        from library.tools import source_memory
        sdir = source_memory.source_dir("valid_digest", tmp_path)
        sdir.mkdir(parents=True, exist_ok=True)
        record = {"content_digest": "valid_digest", "status": "measured",
                  "frame_count": 10, "faces_classified": 50}
        (sdir / source_memory.SLOT_EXPRESSIONS).write_text(json.dumps(record))
        result = ec.read_m3c("valid_digest", tmp_path)
        assert result is not None
        assert result["frame_count"] == 10


class TestCropFaceBinaryCapture:
    """`_crop_face` returns ffmpeg's PNG bytes without text decoding."""

    def test_png_bytes_pass_through_undecoded(self, monkeypatch):
        """Defect: stdout captured with encoding="utf-8", so the PNG
        header byte 0x89 raised UnicodeDecodeError and failed the whole
        expressions lane on any footage with a face."""
        import subprocess

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        seen = {}

        class _Result:
            returncode = 0
            stdout = png
            stderr = b""

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            if kwargs.get("encoding") is not None or kwargs.get("text"):
                # Behave like the stdlib: text capture decodes stdout,
                # which raises on the PNG header exactly as in production.
                png.decode(kwargs.get("encoding") or "utf-8")
            return _Result()

        monkeypatch.setattr(subprocess, "run", fake_run)

        out = ec._crop_face("source.MXF", 1.0, [0.1, 0.1, 0.5, 0.5],
                            [640, 480])

        assert out == png
        assert seen.get("encoding") is None
        assert not seen.get("text")

    def test_failed_crop_returns_none(self, monkeypatch):
        """Defect: a failed ffmpeg crop raises instead of measuring no crop."""
        import subprocess

        class _Result:
            returncode = 1
            stdout = b""
            stderr = b""

        monkeypatch.setattr(subprocess, "run",
                            lambda cmd, **kwargs: _Result())

        assert ec._crop_face("source.MXF", 1.0, [0.1, 0.1, 0.5, 0.5],
                             [640, 480]) is None


class TestExpressionLanes:
    """`build_project_expressions` runs one lane per source on the
    bounded FREE lanes. Digest dedup is decided serially up front (a
    shared `seen` set cannot decide across lanes); the classifier is
    rule-based over per-call data, so the lanes take no lock. The
    assembly restores catalog order. A wiring that silently serializes
    must fail the overlap assertion here, not in production."""

    def test_rows_match_serial_contract_and_lanes_overlap(
            self, tmp_path, monkeypatch):
        import os
        import threading
        import time

        from library.tools import source_memory
        from library.tools.concurrency_routing import lane_width

        paths = {}
        for i, name in enumerate(("A", "B", "C", "D"), start=1):
            media = tmp_path / f"{name}.MOV"
            media.write_bytes(b"bytes-" + name.encode())
            paths[f"clip_{i:03d}"] = str(media)
        catalog = ([{"clip_id": cid, "path": path}
                    for cid, path in paths.items()]
                   + [{"clip_id": "clip_005", "path": paths["clip_002"]},
                      {"clip_id": "clip_006",
                       "path": "/nonexistent/B.MOV"}])
        monkeypatch.setattr(source_memory, "load_catalog",
                            lambda project_folder: catalog)
        monkeypatch.setattr(source_memory, "load_recorded_fingerprints",
                            lambda project_folder: {})
        monkeypatch.setattr(
            source_memory, "digest_for_clip",
            lambda project_folder, clip, recorded: (
                "digest-" + os.path.basename(clip["path"]), "test"))

        spans = []
        spans_lock = threading.Lock()
        measured = []

        def fake_build(path, digest, root=None):
            if path == paths["clip_002"]:
                raise RuntimeError("no M3 persons")
            with spans_lock:
                measured.append(path)
            start = time.monotonic()
            time.sleep(0.2)
            with spans_lock:
                spans.append((start, time.monotonic()))
            return {"content_digest": digest, "faces_classified": 1}

        monkeypatch.setattr(ec, "build_source_expressions", fake_build)

        report = ec.build_project_expressions(str(tmp_path))

        rows = report["clips"]
        assert [r["clip_id"] for r in rows] == [
            "clip_001", "clip_002", "clip_003",
            "clip_004", "clip_005", "clip_006"]
        assert report["failed"] == ["clip_002"]
        assert rows[1]["failed"] == "RuntimeError: no M3 persons"
        assert rows[1]["content_digest"] == "digest-B.MOV"
        assert rows[4] == {"clip_id": "clip_005",
                           "source_file": paths["clip_002"],
                           "skipped": "reused"}
        assert rows[5]["skipped"] == "media-offline"
        assert sorted(measured) == sorted(
            [paths["clip_001"],
             paths["clip_003"], paths["clip_004"]])

        width = lane_width()
        peak, depth, events = 0, 0, sorted(
            [(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
        for _, delta in events:
            depth += delta
            peak = max(peak, depth)
        assert peak >= min(2, width), (
            f"lanes never overlapped (peak {peak}, width {width})")
