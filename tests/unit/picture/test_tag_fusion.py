"""Sound-tag fusion: the M5 labels join hybrid as a third RRF half.

A laughter query over footage nobody transcribed has no words to
match; the PANNs label is the only thing that heard it. Matched tags
confer eligibility and a fusion half, so sound evidence ranks without
a floor-clearing cosine - while absent queries (no label match) stay
silent. Indexes that never measured a sound rank exactly as before.

Engineered matrices, stub embedder, no models.
"""
import json

import numpy as np

from library.tools.analysis.footage_query import (
    EMBEDDINGS_FILE,
    SEGMENTS_FILE,
    FootageIndex,
)


TEXTS = ("brick building exterior",
         "someone walking down the street",
         "trees moving in the wind",
         "laughter echoing across the room")

# Segment spans: A 0-8, B 10-18, C 20-28, D 30-38 on one clip.
SPANS = [(0.0, 8.0), (10.0, 18.0), (20.0, 28.0), (30.0, 38.0)]

EVENTS = [
    {"label": "Laughter", "start": 11.0, "end": 13.0, "confidence": 0.9},
    {"label": "Laughter", "start": 21.0, "end": 23.0, "confidence": 0.7},
    {"label": "Music", "start": 30.0, "end": 38.0},  # no confidence
]


def _write_index(root, peaks, dim=2, sound=True):
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
    if sound:
        payload["sound_events"] = {"clip_001": EVENTS}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    matrix = np.array([[p] + [0.05] * (dim - 1) for p in peaks],
                      dtype="float32")
    np.save(index_dir / EMBEDDINGS_FILE, matrix)
    idx = FootageIndex(str(root), index_dir=index_dir)
    idx._embedder = lambda texts: np.array([[1.0, 0.0]], dtype="float32")
    idx._embed_backend = "test"
    return idx


def test_sound_match_is_evidence_without_words(tmp_path):
    # Dense below floor everywhere, no query term in any text except C
    # (which carries the word but no measured sound): the measured
    # laughter span still answers.
    idx = _write_index(tmp_path, peaks=[0.30, 0.35, 0.30, 0.20])
    report = idx.search_report("where does laughter happen", mode="hybrid")
    assert report["abstained"] is False
    top = report["results"][0]
    assert top["clip_id"] == "clip_001"
    assert top["start"] == 10.0 and top["end"] == 18.0
    assert top["sound_score"] == 0.9


def test_sound_half_reorders_the_fusion(tmp_path):
    # B sits below the dense floor but carries measured laughter; A
    # clears the floor on dense alone. Without the tag half B is not
    # evidence at all; with it, B answers alongside A.
    idx = _write_index(tmp_path, peaks=[0.85, 0.50, 0.30, 0.20])
    report = idx.search_report("laughter", mode="hybrid", floor=0.60)
    got = [r["segment_id"] for r in report["results"]]
    assert "clip_001#scene#0001" in got
    assert "clip_001#scene#0000" in got


def test_soundless_index_ranks_exactly_as_before(tmp_path):
    idx = _write_index(tmp_path, peaks=[0.85, 0.80, 0.30, 0.20],
                       sound=False)
    report = idx.search_report("laughter", mode="hybrid", floor=0)
    got = [r["segment_id"] for r in report["results"]]
    assert got[0] == "clip_001#scene#0000"
    assert all("sound_score" not in r for r in report["results"])


def test_absent_query_stays_silent_with_sound_lane(tmp_path):
    idx = _write_index(tmp_path, peaks=[0.30, 0.20, 0.20, 0.10])
    report = idx.search_report("helicopter", mode="hybrid")
    assert report["results"] == []
    assert report["abstained"] is True


def test_unweighted_event_confers_nothing(tmp_path):
    # D overlaps only the confidence-less Music event: no words, dense
    # below floor, unweighted tag - D stays out.
    idx = _write_index(tmp_path, peaks=[0.30, 0.80, 0.30, 0.20])
    report = idx.search_report("music", mode="hybrid")
    ids = [r["segment_id"] for r in report["results"]]
    assert "clip_001#scene#0003" not in ids


def test_sound_scores_join_by_clip_and_overlap(tmp_path):
    idx = _write_index(tmp_path, peaks=[0.0, 0.0, 0.0, 0.0])
    scores = idx._sound_scores("laughter")
    assert list(scores) == [0.0, 0.9, 0.7, 0.0]
    assert list(idx._sound_scores("helicopter")) == [0.0, 0.0, 0.0, 0.0]
