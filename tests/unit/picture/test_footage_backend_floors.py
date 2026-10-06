"""Backend floors: an index is judged by the encoder that wrote it.

A single global floor cannot serve two encoders: EmbeddingGemma 2
scores absent queries at 0.65-0.70 where MiniLM stays <= 0.18 (scout
measurement on 17 podcast transcript segments), so judging an EG2
matrix by MiniLM's 0.40 floor returns confident wrong rows for
subjects nowhere in the footage - the precise failure the floor
system was built to prevent. `floors_for` resolves each index's own
pair from its recorded backend, and an unrecognised backend fails
closed at the strictest measured floor.

Every test builds its index under `tmp_path` with an engineered
matrix (no model loads) and would fail on the pre-registry code,
which applied 0.40 to every backend. No test reads a real project.
"""
import json

import numpy as np

from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import (
    EMBEDDINGS_FILE,
    SEGMENTS_FILE,
    FootageIndex,
    floors_for,
)

# Scout's measured absent-query peak on EmbeddingGemma 2 (0.696 for
# "motorcycle" against filler segments): above MiniLM's 0.40 floor,
# below the EG2 0.75 floor. An index peaking here is the defect case -
# silence under its own floor, confident rows under the wrong one.
ABSENT_PEAK = 0.69

TEXTS = (
    "a brick building exterior in daylight",
    "someone walking down the street",
    "trees moving in the wind",
)
QUERY = "a motorcycle parked outdoors"


def _write_index(root, *, embed_model, embed_backend, peaks):
    index_dir = root / "idx"
    index_dir.mkdir(parents=True, exist_ok=True)
    segments = [
        {
            "segment_id": f"clip_001#scene#{i:04d}",
            "clip_id": "clip_001",
            "filename": "IMG_0001.MOV",
            "kind": "scene",
            "start": float(i * 10),
            "end": float(i * 10 + 8),
            "duration": 8.0,
            "text": text,
        }
        for i, text in enumerate(TEXTS)
    ]
    payload = {
        "project_folder": str(root),
        "embed_model": embed_model,
        "embed_backend": embed_backend,
        "embed_dimension": 2,
        "segment_count": len(segments),
        "segments": segments,
    }
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload), encoding="utf-8")
    # Query vector is [1, 0], so the dense cosine of row i is peaks[i].
    matrix = np.array([[p, 0.1] for p in peaks], dtype="float32")
    np.save(index_dir / EMBEDDINGS_FILE, matrix)
    idx = FootageIndex(str(root), index_dir=index_dir)
    # The encoder signature carries is_query (EG2 queries encode with
    # the query prompt); the stub ignores the flag and returns the same
    # engineered row either way.
    idx._embedder = lambda texts, *, is_query=False: np.array(
        [[1.0, 0.0]], dtype="float32")
    idx._embed_backend = "test"
    return idx


def test_floors_for_names_each_backend():
    assert floors_for("sentence-transformers/all-MiniLM-L6-v2",
                      "transformers+mean-pooling")[:2] == (0.40, 0.28)
    assert floors_for("google/embeddinggemma-2",
                      "sentence-transformers")[:2] == (0.75, 0.60)
    # The model string decides, even when the backend name is shared:
    # EG2 loads through sentence-transformers too.
    assert floors_for("google/embeddinggemma-2", None)[0] == 0.75


def test_eg2_absent_peak_stays_silent_under_its_own_floor(tmp_path):
    idx = _write_index(tmp_path, embed_model="google/embeddinggemma-2",
                       embed_backend="sentence-transformers",
                       peaks=[ABSENT_PEAK, 0.55, 0.51])
    report = idx.search_report(QUERY, mode="dense")
    assert report["floor"] == 0.75
    assert report["results"] == []
    assert report["abstained"] is True
    # The abstain still names its near miss, in the EG2 weak band.
    assert report["weak"], "an abstain that names nothing reads as broken"
    assert all(w["dense_score"] >= 0.60 for w in report["weak"])


def test_minilm_index_keeps_current_behaviour(tmp_path):
    idx = _write_index(
        tmp_path, embed_model="sentence-transformers/all-MiniLM-L6-v2",
        embed_backend="transformers+mean-pooling",
        peaks=[ABSENT_PEAK, 0.55, 0.51])
    report = idx.search_report(QUERY, mode="dense")
    assert report["floor"] == 0.40
    assert len(report["results"]) == 3
    assert report["abstained"] is False


def test_unknown_backend_fails_closed(tmp_path):
    idx = _write_index(tmp_path, embed_model="some-future-encoder-v9",
                       embed_backend="some-backend",
                       peaks=[ABSENT_PEAK, 0.55, 0.51])
    report = idx.search_report(QUERY, mode="dense")
    assert report["floor"] == max(v[0] for v in footage_query.DENSE_FLOORS.values())
    assert report["results"] == []
    assert report["abstained"] is True
    assert "fail closed" in report["floor_source"]


def test_explicit_floor_still_disables_the_abstain(tmp_path):
    idx = _write_index(tmp_path, embed_model="google/embeddinggemma-2",
                       embed_backend="sentence-transformers",
                       peaks=[ABSENT_PEAK, 0.55, 0.51])
    report = idx.search_report(QUERY, mode="dense", floor=0)
    assert len(report["results"]) == 3
    assert report["abstained"] is False


def test_legacy_index_without_backend_keeps_minilm_default(tmp_path):
    idx = _write_index(tmp_path, embed_model=None, embed_backend=None,
                       peaks=[ABSENT_PEAK, 0.55, 0.51])
    report = idx.search_report(QUERY, mode="dense")
    assert report["floor"] == 0.40
    assert len(report["results"]) == 3
