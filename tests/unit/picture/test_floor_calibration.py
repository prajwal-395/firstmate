"""Build-time floor calibration: the index measures its own absent.

Five fixed absent probes run against the built chunk matrix; the
confident floor sits ABSENT_MARGIN above the worst absent best, the
weak floor IS that worst best, and all of it (plus per-probe bests)
is recorded in the payload beside the matrix. search_chunks prefers
the recorded floors over the registry. Stub encoders, tmp media and
projects only; no models.
"""
import json
import subprocess

import numpy as np

from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import (
    ABSENT_MARGIN,
    ABSENT_PROBES,
    CHUNK_EMBEDDINGS_FILE,
    EG2_TEXT_MODEL,
    SEGMENTS_FILE,
    FootageIndex,
    build_index,
    calibrate_video_floors,
)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _clip(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc=duration={seconds}:size=64x64:rate=1",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def _column_stub(columns):
    """A text encode returning one-hot column i for probe i."""

    def encode(texts, *, is_query=False):
        vecs = []
        for text in texts:
            i = list(ABSENT_PROBES).index(text) % len(columns)
            vec = np.zeros(256, dtype="float32")
            vec[i] = 1.0
            vecs.append(vec)
        return np.array(vecs, dtype="float32")

    return encode


def _matrix(col_maxes):
    dim = len(col_maxes)
    mat = np.zeros((3, 256), dtype="float32")
    for j, peak in enumerate(col_maxes):
        mat[:, j] = [peak, peak - 0.05, peak - 0.10]
    return mat


def test_calibrate_records_floor_weak_source_and_probes():
    mat = _matrix([0.60, 0.65, 0.62, 0.58, 0.55])
    confident, weak, source, bests = calibrate_video_floors(
        mat, _column_stub([0.60, 0.65, 0.62, 0.58, 0.55]))
    assert confident == round(0.65 + ABSENT_MARGIN, 4)
    assert weak == 0.65
    assert "absent-probe" in source and str(len(ABSENT_PROBES)) in source
    assert set(bests) == set(ABSENT_PROBES)
    assert max(bests.values()) == 0.65


def test_calibrate_empty_matrix_calibrates_nothing():
    assert calibrate_video_floors(np.zeros((0, 256), dtype="float32"),
                                  _column_stub([0.5])) is None
    assert calibrate_video_floors(None, _column_stub([0.5])) is None


def test_build_records_calibration_and_search_prefers_it(tmp_path,
                                                         monkeypatch):
    root = tmp_path / "proj"
    _clip(root / "raw" / "A.mp4", 16)
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_A", "filename": "A.mp4",
             "source_file": str(root / "raw" / "A.mp4"),
             "path": str(root / "raw" / "A.mp4"),
             "duration_seconds": 16.0},
        ]}},
    })

    def fake_video(stacks):
        # Probe i reads one-hot column i: every probe bests 0.60.
        n = len(stacks)
        mat = np.zeros((n, 256), dtype="float32")
        mat[:, :5] = 0.60 - np.arange(n)[:, None] * 0.01
        return mat

    monkeypatch.setattr(footage_query, "load_eg2_video_embedder",
                        lambda: (fake_video, "eg2-video-test"))
    monkeypatch.setattr(footage_query, "load_eg2_text_embedder",
                        lambda: (_column_stub([0.60] * 5), "eg2-text-test"))
    monkeypatch.setattr(footage_query, "load_embedder",
                        lambda: (None, "no embedder in test"))
    index_dir = root / "idx"
    stats = build_index(str(root), index_dir=index_dir, video_chunks=True)
    payload = json.loads((index_dir / SEGMENTS_FILE).read_text(
        encoding="utf-8"))
    # Every probe bests 0.60 off the fake matrix: floor must read
    # exactly margin above it, with its source attached.
    assert payload["video_floor_confident"] == round(0.60 + ABSENT_MARGIN, 4)
    assert payload["video_floor_weak"] == 0.60
    assert "absent-probe" in payload["video_floor_source"]
    assert len(payload["video_floor_probes"]) == len(ABSENT_PROBES)
    assert stats["video_floor_confident"] == payload["video_floor_confident"]

    idx = FootageIndex(str(root), index_dir=index_dir)
    probe = idx.search_chunks("a birthday cake with candles")
    assert probe["floor"] == payload["video_floor_confident"]
    assert "absent-probe" in probe["floor_source"]


def test_recorded_floor_retains_what_registry_would_miss(tmp_path):
    index_dir = tmp_path / "cidx"
    index_dir.mkdir(parents=True, exist_ok=True)
    chunks = [{"chunk_id": f"clip_A#vchunk#{i:03d}", "clip_id": "clip_A",
               "filename": "A.mp4", "start": float(i * 8),
               "end": float(i * 8 + 8)} for i in range(2)]
    payload = {"project_folder": str(tmp_path), "segments": [],
               "video_chunks": chunks, "video_embed_model": EG2_TEXT_MODEL,
               "video_embed_backend": "eg2-video-test",
               "video_embed_dimension": 256, "video_unembedded": [],
               "video_floor_confident": 0.68, "video_floor_weak": 0.62,
               "video_floor_source": "absent-probe calibration at build",
               "video_floor_probes": {}}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    matrix = np.array([[0.70] + [0.05] * 255, [0.62] + [0.05] * 255],
                      dtype="float32")
    np.save(index_dir / CHUNK_EMBEDDINGS_FILE, matrix)
    idx = FootageIndex(str(tmp_path), index_dir=index_dir)
    idx._chunk_embedder = lambda texts, *, is_query=False: np.array(
        [[1.0] + [0.0] * 255], dtype="float32")
    idx._chunk_embed_backend = "test"
    report = idx.search_chunks("something present")
    assert report["floor"] == 0.68
    assert [r["chunk_id"] for r in report["results"]] == [
        "clip_A#vchunk#000"]
    assert [w["dense_score"] for w in report["weak"]] == [0.62]


def test_build_without_text_loader_records_why(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    _clip(root / "raw" / "A.mp4", 16)
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_A", "filename": "A.mp4",
             "source_file": str(root / "raw" / "A.mp4"),
             "path": str(root / "raw" / "A.mp4"),
             "duration_seconds": 16.0},
        ]}},
    })

    def fake_video(stacks):
        return np.ones((len(stacks), 256), dtype="float32")

    monkeypatch.setattr(footage_query, "load_eg2_video_embedder",
                        lambda: (fake_video, "eg2-video-test"))
    monkeypatch.setattr(footage_query, "load_eg2_text_embedder",
                        lambda: (None, "no model here"))
    monkeypatch.setattr(footage_query, "load_embedder",
                        lambda: (None, "no embedder in test"))
    index_dir = root / "idx"
    build_index(str(root), index_dir=index_dir, video_chunks=True)
    payload = json.loads((index_dir / SEGMENTS_FILE).read_text(
        encoding="utf-8"))
    assert payload["video_floor_confident"] is None
    assert "registry floors apply" in payload["video_floor_source"]
