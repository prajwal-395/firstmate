"""EmbeddingGemma 2 as a TEXT encoder behind the existing loader shape.

MiniLM stays the default; `--embedder embeddinggemma-2` builds an
index the query path reads back with the same encoder, prompts
resolved from the checkpoint's own config and vectors truncated to
256-d and renormalised. A query vector whose width is not the
matrix's degrades to lexical instead of crashing the matmul.

Every test runs without models: a fake `sentence_transformers`
module stands in for 6.1, and projects are built under `tmp_path`.
No test reads a real project.
"""
import json
import sys
import types

import numpy as np
import pytest

from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import (
    EG2_TEXT_BACKEND,
    EG2_TEXT_DIM,
    EG2_TEXT_MODEL,
    EMBEDDINGS_FILE,
    SEGMENTS_FILE,
    FootageIndex,
    build_index,
    load_eg2_text_embedder,
)
from library.tools.ren_refusal import RenRefusal


# ─── A fake sentence-transformers 6.1 ───────────────────────────


class _FakeEG2:
    """Full-width rows so truncation to 256 is observable."""

    prompts = {"SearchQuery": "sq:", "Document": "doc:"}
    instances = []

    def __init__(self, model_id):
        self.model_id = model_id
        self.calls = []
        type(self).instances.append(self)

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), dict(kwargs)))
        n = len(texts)
        base = np.tile(np.arange(768, dtype="float32"), (n, 1))
        return base / np.linalg.norm(base, axis=1, keepdims=True)


def _install_fake_st(monkeypatch, version="6.1.0", prompts=None):
    _FakeEG2.instances.clear()
    if prompts is not None:
        _FakeEG2.prompts = prompts
    else:
        _FakeEG2.prompts = {"SearchQuery": "sq:", "Document": "doc:"}
    mod = types.ModuleType("sentence_transformers")
    mod.__version__ = version
    mod.SentenceTransformer = _FakeEG2
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    return mod


# ─── The loader ─────────────────────────────────────────────────


def test_eg2_loader_refuses_below_st_61(monkeypatch):
    _install_fake_st(monkeypatch, version="6.0.1")
    encode, reason = load_eg2_text_embedder()
    assert encode is None
    assert "6.1" in reason


def test_eg2_loader_refuses_without_st(monkeypatch):
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    encode, reason = load_eg2_text_embedder()
    assert encode is None
    assert "sentence-transformers" in reason


def test_eg2_loader_prompts_truncates_and_normalises(monkeypatch):
    _install_fake_st(monkeypatch)
    encode, backend = load_eg2_text_embedder()
    assert EG2_TEXT_BACKEND in backend

    docs = encode(["the brewery sign", "trees in the wind"])
    query = encode(["where is the brewery"], is_query=True)

    assert docs.shape == (2, EG2_TEXT_DIM)
    assert query.shape == (1, EG2_TEXT_DIM)
    # Truncating a unit row leaves it short; the loader renormalises.
    assert np.allclose(np.linalg.norm(docs, axis=1), 1.0, atol=1e-5)
    assert np.allclose(np.linalg.norm(query, axis=1), 1.0, atol=1e-5)

    model = _FakeEG2.instances[-1]
    assert model.model_id == EG2_TEXT_MODEL
    by_prompt = {tuple(c[0]): c[1] for c in model.calls}
    assert by_prompt[("the brewery sign", "trees in the wind")]["prompt_name"] == "Document"
    assert by_prompt[("where is the brewery",)]["prompt_name"] == "SearchQuery"
    assert by_prompt[("where is the brewery",)]["truncate_dim"] == EG2_TEXT_DIM


def test_eg2_loader_falls_back_to_retrieval_prompts(monkeypatch):
    _install_fake_st(monkeypatch, prompts={"Retrieval-query": "rq:",
                                           "Retrieval-document": "rd:"})
    encode, backend = load_eg2_text_embedder()
    assert "Retrieval-query" in backend and "Retrieval-document" in backend
    vec = encode(["trees"], is_query=True)
    assert vec.shape == (1, EG2_TEXT_DIM)


def test_eg2_loader_encodes_without_prompt_when_checkpoint_has_none(monkeypatch):
    _install_fake_st(monkeypatch, prompts={})
    encode, backend = load_eg2_text_embedder()
    assert "q:-_d:-" in backend
    vec = encode(["trees"])
    assert vec.shape == (1, EG2_TEXT_DIM)
    assert _FakeEG2.instances[-1].calls[0][1]["prompt_name"] is None


# ─── A minimal project for build_index ──────────────────────────


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "IMG_0001.MOV",
             "source_file": str(root / "raw" / "IMG_0001.MOV"),
             "path": str(root / "raw" / "IMG_0001.MOV"),
             "duration_seconds": 20.0},
        ]}},
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001",
        "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "parking", "start": 2.0, "end": 2.4}]},
        ],
        "motion_energy": {"sample_rate_hz": 1, "values": [0.5] * 20},
    })
    return root


def _fake_256(texts, **kwargs):
    n = len(texts)
    base = np.tile(np.arange(256, dtype="float32"), (n, 1))
    return base / np.linalg.norm(base, axis=1, keepdims=True)


def test_build_index_refuses_an_unknown_embedder(project):
    with pytest.raises(RenRefusal):
        build_index(str(project), embedder="no-such-encoder")


def test_build_index_eg2_records_its_own_model(project, monkeypatch):
    monkeypatch.setattr(footage_query, "load_eg2_text_embedder",
                        lambda: (_fake_256, "embeddinggemma-2-text+q:S_d:D"))
    stats = build_index(str(project), embedder="embeddinggemma-2")
    payload = json.loads((project / "pipeline_output" / "scratch"
                          / "footage_index" / SEGMENTS_FILE).read_text(encoding="utf-8"))
    assert payload["embed_model"] == EG2_TEXT_MODEL
    assert payload["embed_dimension"] == 256
    assert stats["embed_dimension"] == 256


# ─── Query routing back to the writing encoder ──────────────────


def _write_manual_index(root, *, embed_model, peaks, dim):
    index_dir = root / "idx"
    index_dir.mkdir(parents=True, exist_ok=True)
    segments = [
        {"segment_id": f"clip_001#scene#{i:04d}", "clip_id": "clip_001",
         "filename": "IMG_0001.MOV", "kind": "scene",
         "start": float(i * 10), "end": float(i * 10 + 8),
         "duration": 8.0, "text": text}
        for i, text in enumerate(("a brick building exterior in daylight",
                                  "someone walking down the street",
                                  "trees moving in the wind"))
    ]
    payload = {"project_folder": str(root), "embed_model": embed_model,
               "embed_backend": "test", "embed_dimension": dim,
               "segment_count": len(segments), "segments": segments}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload), encoding="utf-8")
    matrix = np.array([[p] + [0.1] * (dim - 1) for p in peaks], dtype="float32")
    np.save(index_dir / EMBEDDINGS_FILE, matrix)
    return index_dir


def test_query_loads_the_encoder_that_wrote_the_index(tmp_path, monkeypatch):
    index_dir = _write_manual_index(tmp_path, embed_model=EG2_TEXT_MODEL,
                                    peaks=[0.8, 0.5, 0.4], dim=256)
    calls = []

    def fake_eg2():
        calls.append("eg2")
        return lambda texts, *, is_query=False: np.ones((len(texts), 256),
                                                        dtype="float32"), "eg2-test"

    def fake_minilm():
        calls.append("minilm")
        return lambda texts: np.ones((len(texts), 384), dtype="float32"), "minilm-test"

    monkeypatch.setattr(footage_query, "load_eg2_text_embedder", fake_eg2)
    monkeypatch.setattr(footage_query, "load_embedder", fake_minilm)
    idx = FootageIndex(str(tmp_path), index_dir=index_dir)
    idx.search("brick building", mode="dense", floor=0)
    assert calls == ["eg2"]

    calls.clear()
    minilm_dir = _write_manual_index(tmp_path / "other", embed_model="x/all-MiniLM-L6-v2",
                                     peaks=[0.8, 0.5, 0.4], dim=384)
    idx = FootageIndex(str(tmp_path), index_dir=minilm_dir)
    idx.search("brick building", mode="dense", floor=0)
    assert calls == ["minilm"]


def test_dim_mismatch_degrades_instead_of_crashing(tmp_path, monkeypatch):
    # A 256-d EG2 matrix met by a 384-d MiniLM query vector: the old
    # code fed both to the matmul and crashed. The guard turns dense
    # off and the report says lexical-only, in both modes.
    index_dir = _write_manual_index(tmp_path, embed_model=EG2_TEXT_MODEL,
                                    peaks=[0.8, 0.5, 0.4], dim=256)
    monkeypatch.setattr(
        footage_query, "load_eg2_text_embedder",
        lambda: (lambda texts, *, is_query=False: np.ones((len(texts), 384),
                                                          dtype="float32"), "wide-test"))
    idx = FootageIndex(str(tmp_path), index_dir=index_dir)
    hybrid = idx.search_report("brick building", mode="hybrid")
    assert hybrid["degraded"] is not None
    dense = idx.search_report("brick building", mode="dense")
    assert dense["error"] is not None
