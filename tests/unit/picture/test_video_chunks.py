"""Video chunks: 8 s windows, one EG2 vector each, text-queried.

The unified space earns its keep here: a text query embedding scores
directly against per-window video vectors, judged by the recording
encoder's floors with an abstain. Media comes from ffmpeg-generated
test clips (real extraction path); only the encoder is stubbed. No
test reads a real project or loads a model.
"""
import json
import subprocess
import sys
import types

import numpy as np
import pytest

from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import (
    CHUNK_EMBEDDINGS_FILE,
    EG2_TEXT_MODEL,
    SEGMENTS_FILE,
    VIDEO_CHUNK_S,
    FootageIndex,
    build_index,
    cut_video_windows,
    extract_chunk_frames,
    load_eg2_video_embedder,
    main,
)
from library.tools.ren_refusal import RenRefusal


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


@pytest.fixture
def media_project(tmp_path):
    root = tmp_path / "proj"
    _clip(root / "raw" / "A.mp4", 16)
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_A", "filename": "A.mp4",
             "source_file": str(root / "raw" / "A.mp4"),
             "path": str(root / "raw" / "A.mp4"),
             "duration_seconds": 16.0},
            {"clip_id": "clip_gone", "filename": "GONE.mp4",
             "source_file": str(root / "raw" / "GONE.mp4"),
             "path": str(root / "raw" / "GONE.mp4"),
             "duration_seconds": 8.0},
        ]}},
    })
    return root


# ─── The cutting and the stills ─────────────────────────────────


def test_cut_video_windows_tiles_like_the_text_grid():
    assert VIDEO_CHUNK_S == 8.0
    assert cut_video_windows(20.0) == [(0.0, 8.0), (8.0, 16.0), (16.0, 20.0)]
    assert cut_video_windows(8.0) == [(0.0, 8.0)]
    assert cut_video_windows(0) == []
    assert cut_video_windows(None) == []


def test_video_grid_is_the_text_grid():
    """One cutter, one grid: the video windows delegate to cut_chunks,
    so an 8 s window means the same span in both lanes.  A second
    implementation drifting apart is the defect this pins.
    """
    from library.tools.analysis.footage_segments import CHUNK_S, cut_chunks
    assert VIDEO_CHUNK_S == CHUNK_S
    for duration in (0.0, 3.0, 8.0, 20.0, 533.927, None, -5):
        assert cut_video_windows(duration) == cut_chunks(duration)


def test_extract_chunk_frames_cuts_one_fps(media_project):
    frames = extract_chunk_frames(
        str(media_project / "raw" / "A.mp4"), 0.0, 8.0)
    assert len(frames) == 8
    assert all(f.size[0] == 640 for f in frames)


def test_extract_chunk_frames_refuses_a_missing_file(tmp_path):
    with pytest.raises(RenRefusal):
        extract_chunk_frames(str(tmp_path / "nope.mp4"), 0.0, 8.0)


# ─── The video loader (fake ST 6.1) ─────────────────────────────


class _FakeEG2Video:
    prompts = {"SearchQuery": "sq:", "Document": "doc:"}
    calls = []

    def __init__(self, model_id):
        self.model_id = model_id

    def encode(self, inputs, **kwargs):
        type(self).calls.append((inputs, dict(kwargs)))
        n = len(inputs)
        base = np.tile(np.arange(768, dtype="float32"), (n, 1))
        return base / np.linalg.norm(base, axis=1, keepdims=True)


def _install_fake_st(monkeypatch, version="6.1.0"):
    _FakeEG2Video.calls.clear()
    mod = types.ModuleType("sentence_transformers")
    mod.__version__ = version
    mod.SentenceTransformer = _FakeEG2Video
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


def test_video_loader_returns_normalised_256d(monkeypatch):
    _install_fake_st(monkeypatch)
    encode, backend = load_eg2_video_embedder()
    assert "video" in backend
    stack = np.zeros((8, 32, 32, 3), dtype="uint8")
    out = encode([stack, stack])
    assert out.shape == (2, 256)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
    (inputs, kwargs) = _FakeEG2Video.calls[-1]
    assert inputs[0].shape == (8, 32, 32, 3)
    assert _FakeEG2Video.calls[0][0][0].shape == (8, 32, 32, 3)


def test_video_loader_refuses_old_st(monkeypatch):
    _install_fake_st(monkeypatch, version="6.0.1")
    encode, reason = load_eg2_video_embedder()
    assert encode is None
    assert "6.1" in reason


# ─── Build writes the lane; search reads it ─────────────────────


def _fake_256_video(stacks):
    n = len(stacks)
    base = np.tile(np.arange(256, dtype="float32"), (n, 1))
    return base / np.linalg.norm(base, axis=1, keepdims=True)


def test_build_video_chunks_names_missing_media(media_project, monkeypatch):
    monkeypatch.setattr(footage_query, "load_eg2_video_embedder",
                        lambda: (_fake_256_video, "eg2-video-test"))
    monkeypatch.setattr(footage_query, "load_embedder",
                        lambda: (None, "no embedder in test"))
    index_dir = media_project / "idx"
    stats = build_index(str(media_project), index_dir=index_dir,
                        video_chunks=True)
    payload = json.loads((index_dir / SEGMENTS_FILE).read_text(
        encoding="utf-8"))
    assert [c["chunk_id"] for c in payload["video_chunks"]] == [
        "clip_A#vchunk#000", "clip_A#vchunk#001"]
    assert payload["video_embed_model"] == EG2_TEXT_MODEL
    assert payload["video_embed_dimension"] == 256
    mat = np.load(index_dir / CHUNK_EMBEDDINGS_FILE)
    assert mat.shape == (2, 256)
    assert stats["video_chunk_count"] == 2
    assert [u["clip_id"] for u in payload["video_unembedded"]] == [
        "clip_gone"]
    assert [u["clip_id"] for u in stats["video_unembedded"]] == [
        "clip_gone"]


def _write_chunk_index(root, peaks, dim=256,
                       model=EG2_TEXT_MODEL, backend="eg2-video-test"):
    index_dir = root / "cidx"
    index_dir.mkdir(parents=True, exist_ok=True)
    chunks = [
        {"chunk_id": f"clip_A#vchunk#{i:03d}", "clip_id": "clip_A",
         "filename": "A.mp4", "start": float(i * 8),
         "end": float(i * 8 + 8)}
        for i in range(len(peaks))
    ]
    payload = {"project_folder": str(root), "segments": [],
               "video_chunks": chunks, "video_embed_model": model,
               "video_embed_backend": backend,
               "video_embed_dimension": dim, "video_unembedded": []}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    matrix = np.array([[p] + [0.05] * (dim - 1) for p in peaks],
                      dtype="float32")
    np.save(index_dir / CHUNK_EMBEDDINGS_FILE, matrix)
    return index_dir


def _stub_text_loader(vec):
    def load():
        return (lambda texts, *, is_query=False: np.array(
            [vec], dtype="float32")), "text-test"
    return load


def test_search_chunks_ranks_and_abstains(tmp_path, monkeypatch):
    # The stub scores every query alike, so present and absent run on
    # separate matrices: a peak above the EG2 floor ranks, peaks below
    # it (two in the weak band) abstain.
    monkeypatch.setattr(footage_query, "load_eg2_text_embedder",
                        _stub_text_loader([1.0] + [0.0] * 255))

    present_dir = _write_chunk_index(tmp_path / "p",
                                     peaks=[0.80, 0.62, 0.55])
    idx = FootageIndex(str(tmp_path), index_dir=present_dir)
    present = idx.search_chunks("a person pointing at the camera")
    assert present["floor"] == 0.75
    assert [r["chunk_id"] for r in present["results"]] == [
        "clip_A#vchunk#000"]
    assert present["results"][0]["timecode"] == "00:00.000-00:08.000"
    assert present["abstained"] is False

    absent_dir = _write_chunk_index(tmp_path / "a",
                                    peaks=[0.69, 0.62, 0.55])
    idx = FootageIndex(str(tmp_path), index_dir=absent_dir)
    absent = idx.search_chunks("a birthday cake with candles")
    assert [r["dense_score"] for r in absent["results"]] == []
    assert absent["abstained"] is True
    assert [w["dense_score"] for w in absent["weak"]] == [0.69, 0.62], (
        "the abstain names its near miss, in the EG2 weak band")


def test_search_chunks_without_matrix_refuses_in_words(tmp_path):
    index_dir = tmp_path / "empty"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / SEGMENTS_FILE).write_text(
        json.dumps({"project_folder": str(tmp_path), "segments": []}),
        encoding="utf-8")
    idx = FootageIndex(str(tmp_path), index_dir=index_dir)
    report = idx.search_chunks("anything")
    assert report["error"] and "--video-chunks" in report["error"]


def test_search_chunks_without_query_encoder_refuses(tmp_path, monkeypatch):
    index_dir = _write_chunk_index(tmp_path, peaks=[0.80])
    monkeypatch.setattr(footage_query, "load_eg2_text_embedder",
                        lambda: (None, "no model here"))
    idx = FootageIndex(str(tmp_path), index_dir=index_dir)
    report = idx.search_chunks("anything")
    assert report["error"] and "could not load" in report["error"]


def test_cli_build_then_search_chunks(media_project, tmp_path, monkeypatch,
                                      capsys):
    monkeypatch.setattr(footage_query, "load_eg2_video_embedder",
                        lambda: (_fake_256_video, "eg2-video-test"))
    monkeypatch.setattr(footage_query, "load_embedder",
                        lambda: (None, "no embedder in test"))
    monkeypatch.setattr(footage_query, "load_eg2_text_embedder",
                        _stub_text_loader([1.0] + [0.0] * 255))
    index_dir = str(tmp_path / "cli-idx")
    assert main(["build", str(media_project), "--index-dir", index_dir,
                 "--video-chunks"]) == 0
    out = capsys.readouterr().out
    assert '"video_chunk_count": 2' in out
    assert main(["search", str(media_project), "test query", "--chunks",
                 "--index-dir", index_dir, "--floor", "0"]) == 0
    out = capsys.readouterr().out
    assert "clip_A" in out
