"""RRF hybrid: ranks fuse, magnitudes don't vote, weights aren't set.

The hybrid half used a hand-set 0.6 blend over min-max normalised
scores. Reciprocal-rank fusion replaces it: each half contributes
1/(60 + rank), so a row leading one half outranks a row middling in
both, and no weight tunes the tradeoff per query. Retention (floors,
weak band, abstain) still reads the raw signals; fusion only orders.

Engineered score arrays, no models. Every ordering test fails on the
blend it replaces.
"""
import json

import numpy as np

from library.tools.analysis.footage_query import (
    EMBEDDINGS_FILE,
    SEGMENTS_FILE,
    FootageIndex,
    _rrf,
)


def _order(dense, lexical):
    return list(np.argsort(-_rrf(np.asarray(dense, dtype="float64"),
                                 np.asarray(lexical, dtype="float64")),
                           kind="stable"))


def test_single_half_leader_outranks_middling_both():
    # A leads dense and is scoreless lexically; C middles in both.
    # The old 0.6 blend ranked C first (0.65 vs 0.60); RRF ranks the
    # dense champion first.
    assert _order([0.9, 0.8, 0.7, 0.0],
                  [0.0, 0.0, 5.0, 10.0]) == [0, 3, 2, 1]


def test_row_leading_both_halves_stays_first():
    assert _order([0.9, 0.5, 0.4], [8.0, 0.0, 7.0])[0] == 0


def test_ties_keep_segment_order():
    assert _order([0.5, 0.5, 0.5], [0.0, 0.0, 0.0]) == [0, 1, 2]


def test_empty_candidate_set_fuses_empty():
    assert _rrf(np.zeros(0), np.zeros(0)).shape == (0,)


# ─── End to end through the hybrid path ─────────────────────────


def _write_index(root):
    texts = ("brick building exterior", "someone walking",
             "alpha and other words here",
             "alpha beta alpha beta alpha beta")
    index_dir = root / "idx"
    index_dir.mkdir(parents=True, exist_ok=True)
    segments = [
        {"segment_id": f"clip_001#scene#{i:04d}", "clip_id": "clip_001",
         "filename": "IMG_0001.MOV", "kind": "scene",
         "start": float(i * 10), "end": float(i * 10 + 8),
         "duration": 8.0, "text": text}
        for i, text in enumerate(texts)
    ]
    payload = {"project_folder": str(root),
               "embed_model": "sentence-transformers/all-MiniLM-L6-v2",
               "embed_backend": "test", "embed_dimension": 2,
               "segment_count": len(segments), "segments": segments}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    matrix = np.array([[0.9, 0.0], [0.8, 0.0], [0.7, 0.0], [0.0, 0.0]],
                      dtype="float32")
    np.save(index_dir / EMBEDDINGS_FILE, matrix)
    idx = FootageIndex(str(root), index_dir=index_dir)
    idx._embedder = lambda texts: np.array([[1.0, 0.0]], dtype="float32")
    idx._embed_backend = "test"
    return idx


def test_hybrid_search_orders_by_fused_ranks(tmp_path):
    idx = _write_index(tmp_path)
    report = idx.search_report("alpha beta", mode="hybrid", floor=0,
                               top_k=4)
    got = [r["segment_id"] for r in report["results"]]
    assert got == ["clip_001#scene#0000", "clip_001#scene#0003",
                   "clip_001#scene#0002", "clip_001#scene#0001"], (
        "dense champion first, lexical champion second, middling "
        "and scoreless rows after - the blend put the middling row first")
    assert {r["segment_id"] for r in report["results"]} == {
        s["segment_id"] for s in idx.segments}


def test_hybrid_ranking_reruns_identically(tmp_path):
    idx = _write_index(tmp_path)
    first = [r["segment_id"] for r in
             idx.search_report("alpha beta", mode="hybrid", floor=0)["results"]]
    second = [r["segment_id"] for r in
              idx.search_report("alpha beta", mode="hybrid", floor=0)["results"]]
    assert first == second
