"""OCR/object fusion: tracked strings and labels join hybrid as tags.

An on-screen sign nobody transcribed has no words to match; the 1.07
tracked text is the only thing that saw it. A label names its object
exactly ("bicycle" matches "bicycles" where BM25's exact tokens do
not). Matched tags confer eligibility and fusion halves, while absent
queries stay silent and tag-less indexes rank exactly as before.

Engineered matrices, stub embedder, no models.
"""
import json

import numpy as np

from library.tools.analysis.footage_query import (
    EMBEDDINGS_FILE,
    SEGMENTS_FILE,
    FootageIndex,
)

ROWS = [
    # (kind, span, text)
    ("scene", (0.0, 8.0), "bicycles parked in rows bicycles"),
    ("object", (10.0, 18.0), "red bicycle. object. background"),
    ("scene", (20.0, 28.0), "trees moving in the wind"),
    ("scene", (30.0, 38.0), "brick building exterior"),
    ("object", (40.0, 48.0), "red truck. bicycle rack nearby"),
]

OCR = [
    {"text": "SCUFFLEWA BREWING CO", "confidence": 0.85,
     "appearances": [[2.0, 6.0]]},
    {"text": "EXIT", "appearances": [[30.0, 38.0]]},  # no confidence
]


def _write_index(root, peaks, dim=2, ocr=True):
    index_dir = root / "idx"
    index_dir.mkdir(parents=True, exist_ok=True)
    segments = [
        {"segment_id": f"clip_001#{kind}#{i:04d}", "clip_id": "clip_001",
         "filename": "IMG_0001.MOV", "kind": kind,
         "start": start, "end": end, "duration": end - start, "text": text}
        for i, (kind, (start, end), text) in enumerate(ROWS)
    ]
    payload = {"project_folder": str(root),
               "embed_model": "sentence-transformers/all-MiniLM-L6-v2",
               "embed_backend": "test", "embed_dimension": dim,
               "segment_count": len(segments), "segments": segments}
    if ocr:
        payload["ocr_texts"] = {"clip_001": OCR}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    matrix = np.array([[p] + [0.05] * (dim - 1) for p in peaks],
                      dtype="float32")
    np.save(index_dir / EMBEDDINGS_FILE, matrix)
    idx = FootageIndex(str(root), index_dir=index_dir)
    idx._embedder = lambda texts: np.array([[1.0, 0.0]], dtype="float32")
    idx._embed_backend = "test"
    return idx


def test_ocr_match_is_evidence_without_words(tmp_path):
    idx = _write_index(tmp_path, peaks=[0.30, 0.30, 0.30, 0.20, 0.20])
    report = idx.search_report("brewing sign", mode="hybrid")
    assert report["abstained"] is False
    assert [r["segment_id"] for r in report["results"]] == [
        "clip_001#scene#0000"]
    assert report["results"][0]["ocr_score"] == 0.85


def test_object_half_breaks_dense_lexical_tie(tmp_path):
    # "cycle" is no word in any text (BM25 sees nothing), but it sits
    # inside the "bicycle" label - stemming the token match misses and
    # the label rule catches. Dense and lexical tie A/B; the tag half
    # puts the seen object first. Two-half fusion keeps index order.
    idx = _write_index(tmp_path, peaks=[0.80, 0.85, 0.70, 0.70, 0.70])
    report = idx.search_report("cycle", mode="hybrid", floor=0)
    got = [r["segment_id"] for r in report["results"]]
    assert got == ["clip_001#object#0001", "clip_001#scene#0000",
                   "clip_001#scene#0002", "clip_001#scene#0003",
                   "clip_001#object#0004"]
    assert report["results"][0]["object_score"] == 1.0


def test_tagless_query_ranks_exactly_as_before(tmp_path):
    idx = _write_index(tmp_path, peaks=[0.85, 0.80, 0.70, 0.60, 0.50])
    report = idx.search_report("helicopter", mode="dense", floor=0)
    got = [r["segment_id"] for r in report["results"]]
    assert got == ["clip_001#scene#0000", "clip_001#object#0001",
                   "clip_001#scene#0002", "clip_001#scene#0003",
                   "clip_001#object#0004"]
    hybrid = idx.search_report("helicopter", mode="hybrid", floor=0.90)
    assert hybrid["results"] == []
    assert hybrid["abstained"] is True


def test_unweighted_ocr_confers_nothing(tmp_path):
    # D overlaps only the confidence-less EXIT detection: no words,
    # dense below floor, unweighted tag - D stays out.
    idx = _write_index(tmp_path, peaks=[0.30, 0.30, 0.30, 0.20, 0.20])
    report = idx.search_report("exit sign", mode="hybrid")
    assert report["results"] == []
    assert report["abstained"] is True


def test_object_label_ignores_the_tail(tmp_path):
    # The truck row's tail names a bicycle rack (lexical evidence), but
    # its LABEL does not - the tag half stays out of it.
    idx = _write_index(tmp_path, peaks=[0.0] * 5)
    assert list(idx._object_scores("bicycle")) == [0.0, 1.0, 0.0, 0.0, 0.0]


def test_entry_spans_and_confidence_units(tmp_path):
    idx = _write_index(tmp_path, peaks=[0.0] * 5)
    spans = idx._entry_spans
    assert spans({"appearances": [[1.0, 2.0], ["bad", None]]}, 0, 9) == [
        (1.0, 2.0)]
    assert spans({"start": 3.0, "end": 4.0}, 0, 9) == [(3.0, 4.0)]
    assert spans({"start": 3.0}, 0, 9) == [(3.0, 3.0)]
    assert spans({"start": "when"}, 0, 9) == []
    conf = idx._confidence_of
    assert conf({"confidence": 0.7}) == 0.7
    assert conf({}) == 0.0
    assert conf({"confidence": True}) == 0.0
    assert conf({"confidence": "high"}) == 0.0
