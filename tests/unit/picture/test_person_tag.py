"""Person-tag fusion: roster names select measured face/voice spans.

A query naming someone the M3b roster knows ranks the segments
their spans overlap - face (seen) union voice (heard) - without a
floor-clearing cosine or a matching word. Unknown names, an unbuilt
store, and a corrupt one all score zero and never break the query.

Engineered matrices, stub embedder, mocked roster. No models.
"""
import json

import numpy as np
import pytest

from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import (
    EMBEDDINGS_FILE,
    SEGMENTS_FILE,
    FootageIndex,
)

TEXTS = ("brick building exterior",
         "someone walking down the street",
         "trees moving in the wind",
         "a parked car by the curb")
SPANS = [(0.0, 8.0), (10.0, 18.0), (20.0, 28.0), (30.0, 38.0)]

ROSTER = [
    {"person_id": "person_001", "name": "Craig",
     "face_spans": [{"clip_id": "clip_001", "start": 1.0, "end": 3.0}],
     "voice_spans": [{"clip_id": "clip_001", "start": 11.0, "end": 13.0}]},
    {"person_id": "person_002", "name": "Akshita",
     "face_spans": [],
     "voice_spans": [{"clip_id": "clip_001", "start": 21.0, "end": 23.0}]},
]


def _write_index(root, peaks, dim=2):
    index_dir = root / "idx"
    index_dir.mkdir(parents=True, exist_ok=True)
    segments = [
        {"segment_id": f"clip_001#scene#{i:04d}", "clip_id": "clip_001",
         "filename": "IMG_0001.MOV", "kind": "scene",
         "start": start, "end": end, "duration": end - start, "text": text}
        for i, ((start, end), text) in enumerate(zip(SPANS, TEXTS))
    ]
    payload = {"project_folder": str(root),
               "embed_model": "sentence-transformers/all-MiniLM-L6-v2",
               "embed_backend": "test", "embed_dimension": dim,
               "segment_count": len(segments), "segments": segments}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    matrix = np.array([[p] + [0.05] * (dim - 1) for p in peaks],
                      dtype="float32")
    np.save(index_dir / EMBEDDINGS_FILE, matrix)
    idx = FootageIndex(str(root), index_dir=index_dir)
    idx._embedder = lambda texts: np.array([[1.0, 0.0]], dtype="float32")
    idx._embed_backend = "test"
    return idx


@pytest.fixture
def roster(monkeypatch):
    calls = []

    def resolve(project_folder, root=None):
        calls.append(project_folder)
        return {"persons": ROSTER}

    monkeypatch.setattr(footage_query.person_entity,
                        "resolve_person_tracks", resolve)
    return calls


def test_named_spans_answer_without_words(tmp_path, roster):
    idx = _write_index(tmp_path, peaks=[0.30, 0.30, 0.30, 0.20])
    report = idx.search_report("where is craig", mode="hybrid")
    assert report["abstained"] is False
    got = [(r["segment_id"], r["person_score"])
           for r in report["results"]]
    assert got == [("clip_001#scene#0000", 1.0),
                   ("clip_001#scene#0001", 1.0)]


def test_second_person_selects_only_their_span(tmp_path, roster):
    idx = _write_index(tmp_path, peaks=[0.30, 0.30, 0.30, 0.20])
    report = idx.search_report("akshita", mode="hybrid")
    assert [r["segment_id"] for r in report["results"]] == [
        "clip_001#scene#0002"]


def test_unknown_name_stays_silent(tmp_path, roster):
    idx = _write_index(tmp_path, peaks=[0.30, 0.20, 0.20, 0.10])
    report = idx.search_report("zara", mode="hybrid")
    assert report["results"] == []
    assert report["abstained"] is True


def test_unbuilt_store_scores_zero_and_never_breaks(tmp_path, monkeypatch):
    monkeypatch.setattr(footage_query.person_entity,
                        "resolve_person_tracks", lambda *a, **k: {"persons": []})
    idx = _write_index(tmp_path, peaks=[0.85, 0.80, 0.30, 0.20])
    report = idx.search_report("craig", mode="hybrid", floor=0)
    assert [r["segment_id"] for r in report["results"]][:2] == [
        "clip_001#scene#0000", "clip_001#scene#0001"]
    assert all("person_score" not in r for r in report["results"])


def test_corrupt_store_scores_zero_and_never_breaks(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("store unreadable")

    monkeypatch.setattr(footage_query.person_entity,
                        "resolve_person_tracks", boom)
    idx = _write_index(tmp_path, peaks=[0.30, 0.30, 0.30, 0.20])
    report = idx.search_report("craig", mode="hybrid")
    assert report["results"] == []
    assert report["abstained"] is True


def test_roster_resolves_once_per_index(tmp_path, roster):
    idx = _write_index(tmp_path, peaks=[0.30, 0.30, 0.30, 0.20])
    idx.search_report("craig", mode="hybrid")
    idx.search_report("akshita", mode="hybrid")
    assert roster == [str(tmp_path)]


def test_person_scores_join_by_clip_and_overlap(tmp_path, roster):
    idx = _write_index(tmp_path, peaks=[0.0] * 4)
    assert list(idx._person_scores("craig")) == [1.0, 1.0, 0.0, 0.0]
    assert list(idx._person_scores("AKSHITA")) == [0.0, 0.0, 1.0, 0.0]
    assert list(idx._person_scores("helicopter")) == [0.0, 0.0, 0.0, 0.0]
